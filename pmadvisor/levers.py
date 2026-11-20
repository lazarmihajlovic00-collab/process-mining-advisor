"""Automation levers: what can be changed, and how much it would save, estimated from the event log.

Every estimate is computed from the log with explicit assumptions, never by the LLM. Two kinds of levers:
- automation levers whose effect is mechanical (a manual step disappears, payments go out daily). Their saving
  is measured directly from the log: the manual minutes that would no longer be spent, the discounts that would
  have been captured.
- process levers whose effect depends on people (fewer PO changes after framework contracts). Their saving uses
  an assumed reduction rate, marked as an assumption.

Implementation costs are rough assumptions for a Serbian mid-size company (editable), used for payback only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

import pandas as pd

from .analysis import Analysis
from .scenario import Scenario
from .worktime import from_dt, next_open, to_dt


@dataclass
class Lever:
    id: str
    title: str
    kind: str                     # automation | process | control
    description: str
    fixes: str                    # finding id
    one_off_rsd: float
    running_rsd_per_year: float
    apply: Callable[[Scenario], None]
    assumed_reduction: float | None = None   # for process levers


def _set(**kw):
    def f(s: Scenario):
        for k, v in kw.items():
            setattr(s, k, v)
    return f


def _scale(attr: str, factor: float):
    def f(s: Scenario):
        setattr(s, attr, getattr(s, attr) * factor)
    return f


LEVERS: dict[str, Lever] = {l.id: l for l in [
    Lever("sef_erp_integration", "Automatski uvoz e-faktura iz SEF-a u ERP", "automation",
          "SEF API (UBL 2.1) direktno u ERP: faktura se ne prekucava, podaci su strukturirani.",
          "manual_invoice_entry", 600_000, 120_000, _set(sef_api_share=1.0)),
    Lever("auto_three_way_match", "Automatsko trostruko uparivanje sa tolerancijom", "automation",
          "ERP uparuje narudžbenicu, prijemnicu i fakturu po pravilima; čovek rešava samo odstupanja.",
          "manual_three_way_match", 900_000, 180_000, _set(manual_match=False)),
    Lever("approval_escalation", "Eskalacija odobrenja posle 24 h na zamenika", "automation",
          "Ako odobrenje čeka duže od 24 h, sistem ga prosleđuje zameniku (kontroler finansija).",
          "approval_bottleneck", 80_000, 0, _set(approval_escalation_hours=24.0)),
    Lever("daily_payment_run", "Svakodnevni platni nalog u 10:00", "process",
          "Odobrene fakture idu u plaćanje svakog radnog dana; fakture sa popustom stižu u rok.",
          "batched_payment_run", 50_000, 0, _set(payment_days=(0, 1, 2, 3, 4))),
    Lever("duplicate_check", "Automatska provera duplih faktura pri unosu", "control",
          "Pravilo dobavljač + broj fakture + iznos + datum blokira dupli unos pre plaćanja.",
          "duplicate_payments", 150_000, 0, _set(duplicate_check=True)),
    Lever("price_catalog_sync", "Sinhronizacija cenovnika dobavljača sa ERP-om", "process",
          "Važeći cenovnici dobavljača se učitavaju u ERP, pa narudžbenica ima tačnu cenu.",
          "price_discrepancy_rework", 400_000, 60_000, _scale("price_discrepancy_rate", 0.5), 0.5),
    Lever("po_change_controls", "Okvirni ugovori i katalog za česte artikle", "process",
          "Za artikle koji se stalno naručuju: katalog sa količinama i cenama, manje izmena narudžbenica.",
          "po_change_rework", 250_000, 0, _scale("po_change_rate", 0.5), 0.5),
    Lever("no_po_no_pay", "Pravilo \"nema narudžbenice, nema plaćanja\"", "control",
          "Faktura bez narudžbenice napravljene pre nabavke se vraća nabavci; dobavljači se obaveštavaju.",
          "maverick_buying", 100_000, 0, _scale("maverick_rate", 0.3), 0.7),
]}


@dataclass
class Estimate:
    lever: str
    title: str
    kind: str
    applicable: bool
    hours_per_year: float = 0.0
    labor_rsd_per_year: float = 0.0
    cash_rsd_per_year: float = 0.0          # money kept (e.g. early-payment discounts)
    one_off_rsd: float = 0.0
    running_rsd_per_year: float = 0.0
    net_rsd_per_year: float = 0.0
    payback_months: float | None = None
    effects: dict = field(default_factory=dict)   # non-money effects (waiting, risk, compliance)
    basis: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    confidence: str = "measured"            # measured | assumed

    def to_dict(self) -> dict:
        return asdict(self)


def _manual_minutes(an: Analysis, activity: str, cases=None, first_only=False) -> tuple[float, int]:
    x = an.df[(an.df["activity"] == activity) & an.df["manual"]]
    if cases is not None:
        x = x[x["case_id"].isin(cases)]
    if first_only:
        x = x.drop_duplicates("case_id")
    return float(x["service_min"].sum()), int(len(x))


def _finish(an: Analysis, lever: Lever, e: Estimate) -> Estimate:
    h = an.profile.hourly_cost_rsd
    e.hours_per_year = round(e.hours_per_year)
    e.labor_rsd_per_year = round(e.hours_per_year * h, -3)
    e.cash_rsd_per_year = round(e.cash_rsd_per_year, -3)
    e.one_off_rsd = lever.one_off_rsd
    e.running_rsd_per_year = lever.running_rsd_per_year
    e.net_rsd_per_year = round(e.labor_rsd_per_year + e.cash_rsd_per_year - lever.running_rsd_per_year, -3)
    e.payback_months = round(12 * lever.one_off_rsd / e.net_rsd_per_year, 1) if e.net_rsd_per_year > 0 else None
    e.assumptions.append(f"trošak rada {h:,.0f} RSD po satu (bruto sa doprinosima)".replace(",", "."))
    e.assumptions.append(f"godišnje = period loga x {an.annual_factor:.2f}")
    e.assumptions.append(f"uvođenje oko {lever.one_off_rsd:,.0f} RSD, održavanje {lever.running_rsd_per_year:,.0f} RSD godišnje (gruba procena)".replace(",", "."))
    return e


def estimate(an: Analysis, lever_id: str, **params) -> Estimate:
    lever = LEVERS[lever_id]
    e = Estimate(lever_id, lever.title, lever.kind, True)
    p, A, df = an.profile, an.annual_factor, an.df
    if lever_id == "sef_erp_integration":
        ch = p.attr.get("channel")
        cases = an.cases.index
        if ch in an.cases.columns:
            cases = an.cases.index[an.cases[ch].astype(str).str.startswith(p.e_invoice_channel_prefix)]
        minutes, n = _manual_minutes(an, p.act("invoice_entry"), cases, first_only=True)
        e.hours_per_year = minutes / 60 * A
        rec = df[df["activity"] == p.act("invoice_receipt")].groupby("case_id")["complete"].min()
        ent = df[(df["activity"] == p.act("invoice_entry")) & df["case_id"].isin(cases)].groupby("case_id")["complete"].min()
        lag = ((ent - rec.reindex(ent.index)).dt.total_seconds() / 3600).dropna()
        e.effects = {"invoices_per_year": round(n * A), "receipt_to_entry_h_mean_now": round(float(lag.mean()), 1) if len(lag) else None,
                     "receipt_to_entry_h_after": 0}
        e.basis.append(f"{n} ručnih unosa e-faktura u periodu, ukupno {minutes / 60:.0f} h")
        e.assumptions.append("svi unosi e-faktura iz SEF-a postaju automatski (strukturiran UBL); fakture iz inostranstva ostaju ručne")
    elif lever_id == "auto_three_way_match":
        share = float(params.get("auto_share", 1.0))
        minutes, n = _manual_minutes(an, p.act("match"))
        e.hours_per_year = minutes / 60 * A * share
        e.basis.append(f"{n} ručnih uparivanja u periodu, ukupno {minutes / 60:.0f} h")
        e.assumptions.append(f"{share:.0%} uparivanja radi sistem; odstupanja i dalje rešava nabavka (korak razlike u ceni ostaje)")
    elif lever_id == "approval_escalation":
        from .findings import _approval_waits

        limit_h = float(params.get("hours", 24.0))
        x = _approval_waits(an)
        if x.empty:
            e.applicable = False
            return e
        by = x.groupby("resource")["wait_min"].median()
        slow = by[by > limit_h * 60 * 0.5].index.tolist() or [by.idxmax()]
        others = x[~x["resource"].isin(slow)]
        affected = x[x["resource"].isin(slow)]
        # when do the other approvers actually approve? hour-of-week slots observed in the log
        how = (others["start"].dt.dayofweek * 24 + others["start"].dt.hour).value_counts()
        slots = sorted(how[how >= max(3, 0.002 * len(others))].index.tolist()) or list(range(168))
        ready = affected["start"] - pd.to_timedelta(affected["wait_min"], unit="m")
        esc = ready + pd.Timedelta(hours=limit_h)

        def deputy_start(t):
            base = t.normalize() - pd.Timedelta(days=t.dayofweek)  # Monday 00:00 of that week
            h = t.dayofweek * 24 + t.hour
            for k in range(0, 24 * 14):
                if (h + k) % 168 in slots:
                    cand = base + pd.Timedelta(hours=h + k)
                    return max(cand, t)
            return t

        new_start = esc.map(deputy_start)
        new_wait = pd.concat([affected["wait_min"],
                              (new_start - ready).dt.total_seconds() / 60], axis=1).min(axis=1)
        e.effects = {"approvers": slow, "approvals_per_year": round(len(affected) * A),
                     "escalated_share_pct": round(100 * float((new_wait < affected["wait_min"] - 1).mean()), 1),
                     "approval_wait_h_mean_now": round(float(affected["wait_min"].mean() / 60), 1),
                     "approval_wait_h_mean_after": round(float(new_wait.mean() / 60), 1)}
        e.basis.append(f"{len(affected)} odobrenja kod {', '.join(slow)}, srednje čekanje {affected['wait_min'].mean() / 60:.1f} h")
        e.assumptions.append(f"posle {limit_h:.0f} h odobrenje prelazi na zamenika, koji odobrava u terminima kada to "
                             f"rade ostali odobravaoci (iz loga: {len(slots)} sati u nedelji)")
        e.assumptions.append("ušteda je u vremenu ciklusa, ne u satima rada")
    elif lever_id == "daily_payment_run":
        dcol, acol = p.attr.get("discount_pct"), p.attr.get("amount")
        if dcol not in an.cases.columns:
            e.applicable = False
            e.basis.append("log nema podatak o popustu dobavljača")
            return e
        rec = df[df["activity"] == p.act("invoice_receipt")].groupby("case_id")["complete"].min()
        appr = df[df["activity"] == p.act("invoice_approval")].groupby("case_id")["complete"].max()
        pay = df[df["activity"] == p.act("payment")].groupby("case_id")["complete"].min()
        c = an.cases[an.cases[dcol] > 0].join(rec.rename("rec")).join(appr.rename("appr")).join(pay.rename("pay")).dropna(subset=["rec", "appr", "pay"])
        h = p.payment_run_hour * 60
        new_pay = c["appr"].map(lambda t: to_dt(next_open(from_dt(t.to_pydatetime()), ((h, h + 1),))))
        lim = pd.Timedelta(days=p.discount_days)
        missed_now = (c["pay"] - c["rec"]) > lim
        missed_after = (new_pay - c["rec"]) > lim
        gained = c[missed_now & ~missed_after]
        e.cash_rsd_per_year = float((gained[acol] * gained[dcol] / 100).sum() * A)
        wait_now = ((c["pay"] - c["appr"]).dt.total_seconds() / 3600).median()
        wait_after = ((new_pay - c["appr"]).dt.total_seconds() / 3600).median()
        e.effects = {"discount_invoices": int(len(c)), "missed_now": int(missed_now.sum()), "missed_after": int(missed_after.sum()),
                     "approval_to_payment_h_median_now": round(float(wait_now), 1),
                     "approval_to_payment_h_median_after": round(float(wait_after), 1)}
        e.basis.append(f"{int(missed_now.sum())} od {len(c)} faktura sa popustom plaćeno posle {p.discount_days} dana; "
                       f"sa dnevnim plaćanjem {int(missed_after.sum())}")
        e.assumptions.append(f"popust {p.discount_days} dana od prijema fakture; plaćanje prvog radnog dana u {p.payment_run_hour}:00 posle odobrenja")
        e.assumptions.append("raniji odliv novca (trošak kapitala) nije uračunat")
    elif lever_id == "duplicate_check":
        pay = df[df["activity"] == p.act("payment")].groupby("case_id").size()
        dup_cases = pay[pay > 1].index
        ent_acts = [p.act("invoice_entry"), p.act("invoice_import")]
        ent = df[df["activity"].isin(ent_acts)].groupby("case_id").size()
        dup_entered = ent[ent > 1].index
        x = df[df["case_id"].isin(dup_entered)]
        # every entry after the first one, whether the first was typed in or imported
        later = x[x["activity"].isin(ent_acts)].groupby("case_id").apply(lambda g: g.iloc[1:], include_groups=False)
        second_entry = float(later.loc[later["manual"], "service_min"].sum()) if len(later) else 0.0
        x = x[x["manual"]]
        extra = x[x["activity"].isin([p.act("duplicate_cancel"), p.act("refund")])]["service_min"].sum()
        e.hours_per_year = float((second_entry + extra) / 60 * A)
        acol = p.attr.get("amount")
        risk = float(an.cases.loc[dup_cases, acol].sum() * A) if acol in an.cases.columns else None
        e.effects = {"paid_twice_per_year": round(len(dup_cases) * A), "entered_twice_per_year": round(len(dup_entered) * A),
                     "cash_at_risk_rsd_per_year": round(risk, -3) if risk is not None else None}
        e.basis.append(f"{len(dup_entered)} duplih unosa i {len(dup_cases)} duplih plaćanja u periodu")
        e.assumptions.append("duplo plaćen iznos se vraća povraćajem, pa se ne računa kao ušteda nego kao rizik (cash_at_risk)")
    elif lever_id in ("price_catalog_sync", "po_change_controls"):
        red = float(params.get("reduction", lever.assumed_reduction))
        if lever_id == "price_catalog_sync":
            acts = [p.act("price_discrepancy")]
            cases = set(df.loc[df["activity"] == p.act("price_discrepancy"), "case_id"])
            m2 = df[df["case_id"].isin(cases) & (df["activity"] == p.act("match")) & df["manual"]]
            rematch = m2.groupby("case_id")["service_min"].apply(lambda s: s.iloc[1:].sum()).sum()
        else:
            acts = [p.act("po_change"), p.act("po_change_approval")]
            cases = set(df.loc[df["activity"] == p.act("po_change"), "case_id"])
            rematch = 0.0
        minutes = sum(_manual_minutes(an, a)[0] for a in acts) + rematch
        e.hours_per_year = minutes / 60 * A * red
        e.effects = {"cases_per_year": round(len(cases) * A), "assumed_reduction_pct": round(100 * red)}
        e.basis.append(f"{len(cases)} slučajeva ponovnog rada u periodu, {minutes / 60:.0f} h")
        e.assumptions.append(f"pretpostavka: {red:.0%} manje ovakvih slučajeva (zavisi od dobavljača i ljudi, nije izmereno)")
        e.confidence = "assumed"
    elif lever_id == "no_po_no_pay":
        red = float(params.get("reduction", lever.assumed_reduction))
        retro = df[df["activity"] == p.act("po_retroactive")]
        cases = set(retro["case_id"])
        retro_min = float(retro.loc[retro["manual"], "service_min"].sum())
        normal = df[df["activity"].isin([p.act("requisition"), p.act("requisition_approval"), p.act("po_create")]) & df["manual"]]
        per_case_normal = float(normal.groupby("case_id")["service_min"].sum().mean()) if len(normal) else 0.0
        e.hours_per_year = (retro_min - per_case_normal * len(cases)) / 60 * A * red
        acol = p.attr.get("amount")
        spend = float(an.cases.loc[list(cases), acol].sum() * A) if acol in an.cases.columns and cases else 0.0
        e.effects = {"cases_per_year": round(len(cases) * A), "uncontrolled_spend_rsd_per_year": round(spend, -3),
                     "assumed_reduction_pct": round(100 * red)}
        e.basis.append(f"{len(cases)} narudžbenica napravljenih naknadno u periodu")
        e.assumptions.append(f"pretpostavka: {red:.0%} manje nabavki bez narudžbenice; redovan put (trebovanje, odobrenje, PO) "
                             f"traje {per_case_normal:.0f} min po slučaju, pa ova mera ne štedi vreme nego uvodi kontrolu")
        e.confidence = "assumed"
    if e.applicable and e.hours_per_year == 0 and e.cash_rsd_per_year == 0 and not e.effects:
        e.applicable = False
    return _finish(an, lever, e)


def estimate_all(an: Analysis, lever_ids: list[str] | None = None) -> list[Estimate]:
    return [estimate(an, lid) for lid in (lever_ids or LEVERS)]
