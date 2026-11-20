"""P2P findings: rule-based detectors that turn the event log into concrete, evidenced problems.

Each detector measures one thing from the log, compares it with a threshold and, if the threshold is crossed,
returns a Finding with the numbers behind it. Thresholds are deliberately simple and written down here, so a
finding can always be explained ("27% of SEF invoices were typed in by hand, threshold 30%... ").

The detectors only read the log and the profile. They never see which problems the simulator planted; the
evaluation compares their output with the planted problems afterwards.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import pandas as pd

from .analysis import Analysis

THRESHOLDS = {
    "approval_bottleneck": {"min_tasks": 20, "min_median_wait_h": 8.0, "ratio_to_others": 3.0},
    "po_change_rework": {"max_case_share": 0.08},
    "manual_invoice_entry": {"max_manual_share": 0.40, "min_invoices": 50},
    "manual_three_way_match": {"max_manual_share": 0.50, "min_matches": 50},
    "price_discrepancy_rework": {"max_case_share": 0.08},
    "batched_payment_run": {"max_payment_days_per_week": 1.5, "min_median_wait_h": 24.0},
    "maverick_buying": {"max_case_share": 0.05},
    "duplicate_payments": {"min_cases": 2},
}

LEVERS_FOR = {  # finding -> levers that fix it
    "approval_bottleneck": ["approval_escalation"],
    "po_change_rework": ["po_change_controls"],
    "manual_invoice_entry": ["sef_erp_integration"],
    "manual_three_way_match": ["auto_three_way_match"],
    "price_discrepancy_rework": ["price_catalog_sync"],
    "batched_payment_run": ["daily_payment_run"],
    "maverick_buying": ["no_po_no_pay"],
    "duplicate_payments": ["duplicate_check"],
}


@dataclass
class Finding:
    id: str
    title: str
    severity: str            # high / medium / low
    summary: str             # one sentence in Serbian, numbers from `evidence`
    evidence: dict = field(default_factory=dict)
    levers: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.summary = sr(self.summary)

    def to_dict(self) -> dict:
        return asdict(self)


def sr(text: str) -> str:
    """Serbian decimal comma in generated text: 14.8 -> 14,8 (thousand separators 1.051.000 stay)."""
    return re.sub(r"(?<=\d)\.(?=\d{1,2}(?!\d))", ",", text)


def _cases_with(an: Analysis, activity: str) -> set:
    return set(an.df.loc[an.df["activity"] == activity, "case_id"])


def _sev(value: float, threshold: float, high_factor: float = 2.0) -> str:
    return "high" if value >= threshold * high_factor else "medium"


def _approval_waits(an: Analysis) -> pd.DataFrame:
    """Approval events with waiting measured from their defined predecessor (requisition created, PO changed),
    not from whatever event happens to come before in the log. With imprecise timestamps an approval and the PO
    created minutes later can swap places; the predecessor-based wait is robust to that."""
    p = an.profile
    df = an.df
    out = []
    for appr, pred in ((p.act("requisition_approval"), p.act("requisition")),
                       (p.act("po_change_approval"), p.act("po_change"))):
        a = df[df["activity"] == appr]
        pc = df[df["activity"] == pred].groupby("case_id")["complete"].max()
        if a.empty:
            continue
        a = a.copy()
        ref = a["case_id"].map(pc)
        w = (a["start"] - ref).dt.total_seconds() / 60
        a["wait_min"] = w.where(ref.notna(), a["wait_min"]).clip(lower=0)
        out.append(a)
    return pd.concat(out) if out else df.iloc[0:0]


def detect_approval_bottleneck(an: Analysis) -> Finding | None:
    t = THRESHOLDS["approval_bottleneck"]
    x = _approval_waits(an)
    if x.empty or (x["resource"] == "unknown").all():
        return None
    by = x.groupby("resource")["wait_min"].agg(["median", "size", "mean"])
    by = by[by["size"] >= t["min_tasks"]]
    worst = None
    for res, r in by.iterrows():
        others = x[x["resource"] != res]["wait_min"].median()
        ratio = r["median"] / max(others, 1.0)
        if r["median"] / 60 >= t["min_median_wait_h"] and ratio >= t["ratio_to_others"]:
            if worst is None or r["median"] > worst[1]["median"]:
                worst = (res, r, others, ratio)
    if not worst:
        return None
    res, r, others, ratio = worst
    days = sorted(x[x["resource"] == res]["start"].dt.dayofweek.value_counts().head(3).index.tolist())
    names = ["pon", "uto", "sre", "čet", "pet", "sub", "ned"]
    wait_h = round(r["median"] / 60, 1)
    return Finding(
        "approval_bottleneck", "Usko grlo u odobravanju", "high" if wait_h >= 24 else "medium",
        f"Odobrenja koja čekaju {res} čekaju u medijani {wait_h} h, a kod ostalih odobravalaca "
        f"{round(others / 60, 1)} h ({round(ratio, 1)}x duže).",
        {"resource": res, "tasks": int(r["size"]), "wait_h_median": wait_h,
         "wait_h_median_other_approvers": round(others / 60, 1), "ratio": round(ratio, 1),
         "mean_wait_h": round(r["mean"] / 60, 1), "active_weekdays": [names[d] for d in days],
         "threshold": f"medijana > {t['min_median_wait_h']} h i > {t['ratio_to_others']}x od ostalih"},
        LEVERS_FOR["approval_bottleneck"])


def _rate_finding(an: Analysis, fid: str, activity: str, title: str, what: str) -> Finding | None:
    t = THRESHOLDS[fid]["max_case_share"]
    cases = _cases_with(an, activity)
    share = len(cases) / max(len(an.cases), 1)
    if share <= t:
        return None
    x = an.df[an.df["case_id"].isin(cases)]
    extra_min = x.loc[x["manual"] & (x["activity"] == activity), "service_min"].sum()
    with_c = an.cases.loc[an.cases.index.isin(cases), "cycle_days"].median()
    without = an.cases.loc[~an.cases.index.isin(cases), "cycle_days"].median()
    return Finding(
        fid, title, _sev(share, t),
        f"{what} u {round(100 * share, 1)}% slučajeva ({len(cases)} od {len(an.cases)}); ti slučajevi traju u medijani "
        f"{round(with_c, 1)} dana prema {round(without, 1)} bez toga.",
        {"cases": len(cases), "case_share_pct": round(100 * share, 1), "threshold_pct": round(100 * t, 1),
         "cycle_days_median_with": round(float(with_c), 1), "cycle_days_median_without": round(float(without), 1),
         "activity_hours_per_year": round(float(extra_min / 60 * an.annual_factor))},
        LEVERS_FOR[fid])


def detect_po_change_rework(an: Analysis) -> Finding | None:
    return _rate_finding(an, "po_change_rework", an.profile.act("po_change"), "Ponovni rad: izmene narudžbenica",
                         "Narudžbenica je menjana i ponovo odobravana")


def detect_price_discrepancy(an: Analysis) -> Finding | None:
    return _rate_finding(an, "price_discrepancy_rework", an.profile.act("price_discrepancy"),
                         "Ponovni rad: razlike u ceni", "Faktura nije prošla uparivanje zbog cene")


def _manual_share(an: Analysis, activity: str, mask: pd.Series | None = None) -> tuple[float, int, float]:
    x = an.df[an.df["activity"] == activity]
    if mask is not None:
        x = x[mask.reindex(x.index, fill_value=False)]
    if x.empty:
        return 0.0, 0, 0.0
    manual = x[x["manual"]]
    return len(manual) / len(x), len(x), float(manual["service_min"].sum())


def detect_manual_invoice_entry(an: Analysis) -> Finding | None:
    t = THRESHOLDS["manual_invoice_entry"]
    p = an.profile
    df = an.df
    entry, imp = p.act("invoice_entry"), p.act("invoice_import")
    ch = p.attr.get("channel")
    first_entry = df[df["activity"].isin([entry, imp])].drop_duplicates("case_id")
    if ch in df.columns:
        first_entry = first_entry[first_entry[ch].astype(str).str.startswith(p.e_invoice_channel_prefix)]
    n = len(first_entry)
    if n < t["min_invoices"]:
        return None
    manual = first_entry[first_entry["manual"]]
    share = len(manual) / n
    if share <= t["max_manual_share"]:
        return None
    hours = df.loc[df["manual"] & (df["activity"] == entry), "service_min"].sum() / 60 * an.annual_factor
    return Finding(
        "manual_invoice_entry", "Ručni unos e-faktura", _sev(share, t["max_manual_share"], 1.8),
        f"{round(100 * share, 1)}% e-faktura iz SEF-a ({len(manual)} od {n}) je ručno prekucano u ERP, "
        f"medijana {round(float(manual['service_min'].median()), 1)} min po fakturi.",
        {"e_invoices": n, "manual": len(manual), "manual_share_pct": round(100 * share, 1),
         "service_min_median": round(float(manual["service_min"].median()), 1),
         "entry_hours_per_year_all_channels": round(float(hours)),
         "threshold_pct": round(100 * t["max_manual_share"], 1)},
        LEVERS_FOR["manual_invoice_entry"])


def detect_manual_match(an: Analysis) -> Finding | None:
    t = THRESHOLDS["manual_three_way_match"]
    share, n, minutes = _manual_share(an, an.profile.act("match"))
    if n < t["min_matches"] or share <= t["max_manual_share"]:
        return None
    return Finding(
        "manual_three_way_match", "Ručno trostruko uparivanje", "high" if share > 0.8 else "medium",
        f"{round(100 * share, 1)}% uparivanja narudžbenica / prijemnica / faktura radi se ručno "
        f"({round(minutes / 60 * an.annual_factor)} h godišnje).",
        {"matches": n, "manual_share_pct": round(100 * share, 1),
         "hours_per_year": round(minutes / 60 * an.annual_factor),
         "service_min_median": round(float(an.df.loc[(an.df["activity"] == an.profile.act("match")) & an.df["manual"],
                                                     "service_min"].median()), 1),
         "threshold_pct": round(100 * t["max_manual_share"], 1)},
        LEVERS_FOR["manual_three_way_match"])


def detect_batched_payment_run(an: Analysis) -> Finding | None:
    t = THRESHOLDS["batched_payment_run"]
    pay = an.df[an.df["activity"] == an.profile.act("payment")]
    if len(pay) < 20:
        return None
    weeks = max((pay["complete"].max() - pay["complete"].min()).total_seconds() / 86400 / 7, 1)
    pay_days = pay["complete"].dt.normalize().nunique() / weeks
    wait_h = float(pay["wait_min"].median() / 60)
    if pay_days > t["max_payment_days_per_week"] or wait_h < t["min_median_wait_h"]:
        return None
    names = ["ponedeljak", "utorak", "sreda", "četvrtak", "petak", "subota", "nedelja"]
    top_day = int(pay["complete"].dt.dayofweek.mode().iloc[0])
    ev = {"payment_days_per_week": round(pay_days, 2), "main_payment_day": names[top_day],
          "wait_h_median_before_payment": round(wait_h, 1)}
    disc = _discounts(an)
    if disc:
        ev.update(disc)
    text = (f"Plaćanja izlaze u proseku {round(pay_days, 1)} dana nedeljno (uglavnom {names[top_day]}); odobrene fakture "
            f"čekaju na plaćanje u medijani {round(wait_h, 1)} h.")
    if disc and disc.get("discount_missed_rsd_per_year"):
        text += f" Propušteno je {disc['discount_missed_cases']} popusta za ranije plaćanje (oko {disc['discount_missed_rsd_per_year']:,} RSD godišnje).".replace(",", ".")
    return Finding("batched_payment_run", "Plaćanje u nedeljnim serijama", "high" if disc and disc.get("discount_missed_cases", 0) > 20 else "medium",
                   text, ev, LEVERS_FOR["batched_payment_run"])


def _discounts(an: Analysis) -> dict:
    p = an.profile
    dcol, acol = p.attr.get("discount_pct"), p.attr.get("amount")
    if dcol not in an.cases.columns or acol not in an.cases.columns:
        return {}
    rec = an.df[an.df["activity"] == p.act("invoice_receipt")].groupby("case_id")["complete"].min()
    pay = an.df[an.df["activity"] == p.act("payment")].groupby("case_id")["complete"].min()
    c = an.cases[an.cases[dcol] > 0].join(rec.rename("received")).join(pay.rename("paid")).dropna(subset=["received", "paid"])
    days = (c["paid"] - c["received"]).dt.total_seconds() / 86400
    missed = c[days > p.discount_days]
    value = (missed[acol] * missed[dcol] / 100).sum()
    return {"discount_cases": int(len(c)), "discount_missed_cases": int(len(missed)),
            "discount_missed_rsd_period": round(float(value), -2),
            "discount_missed_rsd_per_year": int(round(float(value * an.annual_factor), -3))}


def detect_maverick(an: Analysis) -> Finding | None:
    t = THRESHOLDS["maverick_buying"]["max_case_share"]
    p = an.profile
    df = an.df
    po = df[df["activity"].isin([p.act("po_create"), p.act("po_retroactive")])].groupby("case_id")["complete"].min()
    inv = df[df["activity"] == p.act("invoice_receipt")].groupby("case_id")["complete"].min()
    j = pd.concat([po.rename("po"), inv.rename("inv")], axis=1).dropna(subset=["inv"])
    # no PO before the invoice: a PO created after it, or no PO and no requisition at all (a case with a
    # requisition but no PO event is more likely a missing event than off-system buying)
    req = set(df.loc[df["activity"] == p.act("requisition"), "case_id"])
    no_po = j["po"].isna() & ~j.index.isin(req)
    late = j[no_po | (j["po"] > j["inv"])]
    share = len(late) / max(len(an.cases), 1)
    if share <= t:
        return None
    acol = p.attr.get("amount")
    spend = float(an.cases.loc[late.index, acol].sum()) if acol in an.cases.columns else None
    ev = {"cases": int(len(late)), "case_share_pct": round(100 * share, 1), "threshold_pct": round(100 * t, 1)}
    if spend is not None:
        ev["spend_rsd_period"] = round(spend, -3)
        ev["spend_share_pct"] = round(100 * spend / float(an.cases[acol].sum()), 1)
    return Finding("maverick_buying", "Nabavka bez narudžbenice", _sev(share, t),
                   f"U {round(100 * share, 1)}% slučajeva ({len(late)}) narudžbenica je napravljena tek posle prijema "
                   f"fakture, bez trebovanja i odobrenja.", ev, LEVERS_FOR["maverick_buying"])


def detect_duplicate_payments(an: Analysis) -> Finding | None:
    t = THRESHOLDS["duplicate_payments"]
    p = an.profile
    pay = an.df[an.df["activity"] == p.act("payment")].groupby("case_id").size()
    dup = pay[pay > 1]
    entries = an.df[an.df["activity"].isin([p.act("invoice_entry"), p.act("invoice_import")])].groupby("case_id").size()
    dup_entered = int((entries > 1).sum())
    if len(dup) < t["min_cases"]:
        return None
    acol = p.attr.get("amount")
    value = float(an.cases.loc[dup.index, acol].sum()) if acol in an.cases.columns else None
    ev = {"paid_twice_cases": int(len(dup)), "entered_twice_cases": dup_entered}
    text = f"{len(dup)} faktura plaćeno je dva puta; {dup_entered} faktura je uneto dva puta."
    if value is not None:
        ev["paid_twice_rsd_period"] = round(value, -2)
        ev["paid_twice_rsd_per_year"] = int(round(value * an.annual_factor, -3))
        text += f" Duplo plaćeno: {round(value):,} RSD u periodu (do povraćaja).".replace(",", ".")
    return Finding("duplicate_payments", "Duple fakture i duplo plaćanje", "high", text, ev, LEVERS_FOR["duplicate_payments"])


DETECTORS = [detect_approval_bottleneck, detect_po_change_rework, detect_manual_invoice_entry, detect_manual_match,
             detect_price_discrepancy, detect_batched_payment_run, detect_maverick, detect_duplicate_payments]


def detect(an: Analysis) -> list[Finding]:
    out = []
    for d in DETECTORS:
        f = d(an)
        if f:
            out.append(f)
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda f: order[f.severity])
