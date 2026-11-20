"""Deterministic advisor (no LLM): one recommendation per finding, ordered by net annual saving, and a report
assembled from the same numbers. It is the fallback without an API key and the baseline in the evaluation."""

from __future__ import annotations

from ..findings import sr
from ..service import Workspace


def _rsd(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:,.0f}".replace(",", ".") + " RSD"


def priority(est: dict, severity: str) -> int:
    if est["kind"] == "control" or est["net_rsd_per_year"] <= 0:
        return 2 if severity == "high" else 3
    pb = est["payback_months"]
    if pb is not None and pb <= 12 and severity == "high":
        return 1
    return 2 if pb is not None and pb <= 24 else 3


def plan(ws: Workspace) -> list[tuple[dict, dict, int]]:
    est = {e["lever"]: e for e in ws.estimates()}
    out = []
    for f in ws.findings():
        for lid in f["levers"]:
            e = est[lid]
            out.append((f, e, priority(e, f["severity"])))
    return sorted(out, key=lambda x: (x[2], -x[1]["net_rsd_per_year"]))


def advise(ws: Workspace, user: str):
    from .runner import AdvisorResult

    run = ws.run()
    res = AdvisorResult("", "template", None)
    items = plan(ws)
    for f, e, prio in items:
        rationale = f"{f['summary']} Procena: {e['hours_per_year']} h i {_rsd(e['net_rsd_per_year'])} neto godišnje."
        rec = ws.propose(e["lever"], prio, rationale, "template", "system", run)
        if not rec.get("duplicate"):
            res.recommendation_ids.append(rec["id"])
    k = ws.kpis(run)
    lines = ["## Sažetak",
             f"Analizirano je {k['cases']} slučajeva nabavke ({k['period']}). Medijana trajanja od trebovanja do plaćanja je "
             f"{k['cycle_days_median']} dana, a ručni rad iznosi oko {k['manual_hours_per_year']} h godišnje "
             f"({k['manual_fte']} FTE). Detektori su našli {len(ws.findings(run))} problema.",
             "", "## Preporuke po prioritetu"]
    for f, e, prio in items:
        conf = "izmereno iz loga" if e["confidence"] == "measured" else "pretpostavka"
        if "approval_wait_h_mean_after" in e["effects"]:
            fx = e["effects"]
            effect = (f"Procena: srednje čekanje na odobrenje {fx['approval_wait_h_mean_now']} → "
                      f"{fx['approval_wait_h_mean_after']} h; ušteda je u vremenu, ne u satima rada.")
        elif e["net_rsd_per_year"] <= 0:
            effect = (f"Procena: {e['hours_per_year']} h godišnje, neto {_rsd(e['net_rsd_per_year'])}; ovo je kontrolna "
                      f"mera (rizik i usklađenost), ne ušteda.")
        else:
            effect = (f"Procena: {e['hours_per_year']} h godišnje, neto {_rsd(e['net_rsd_per_year'])} godišnje, povrat "
                      f"{e['payback_months']} meseci.")
        lines.append(f"- **P{prio}: {e['title']}** ({conf}). {f['summary']} {effect}")
    if run.scenario is not None and items:
        mech = [e["lever"] for _, e, p in items if e["confidence"] == "measured" and e["kind"] != "control"]
        if mech:
            w = ws.whatif(mech, run)
            b, a = w["before"], w["after"]
            lines += ["", "## Šta pokazuje simulacija",
                      f"Sa merama {', '.join(mech)}: ručni rad {b['manual_hours_per_year']} → {a['manual_hours_per_year']} h "
                      f"godišnje, prosečno trajanje {b['cycle_days_mean']} → {a['cycle_days_mean']} dana, propušteni popusti "
                      f"{_rsd(b['discount_missed_rsd_per_year'])} → {_rsd(a['discount_missed_rsd_per_year'])} godišnje."]
    lines += ["", "## Rizici i šta proveriti",
              "- Procene troška uvođenja su grube; zatražiti ponudu za integraciju.",
              "- Mere označene kao pretpostavka treba proveriti pilotom na jednom odeljenju."]
    res.text = sr("\n".join(lines))
    res.grounding = {"numbers": 0, "grounded": 0, "ungrounded": [], "grounded_pct": 100.0}
    return res
