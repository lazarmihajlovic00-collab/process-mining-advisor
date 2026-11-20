"""What-if check: apply a lever in the simulator and re-run the same quarter (same cases, same random draws).

This is how the savings estimates are validated. The estimate in `levers.py` reads only the baseline log; the
what-if run lets the queues, payment runs and approval windows react to the change. The difference between the
two shows how far a log-only estimate can be trusted.

Only possible for logs produced by the built-in simulator; for a real company's log the same check needs a
simulation model calibrated on that log (next step, see README).
"""

from __future__ import annotations

import copy

from . import analysis, findings, levers
from .eventlog import normalise
from .profile import P2P, Profile
from .scenario import BOTTLENECK_DEPT, Scenario
from .simulate import simulate


def analyse(s: Scenario, profile: Profile = P2P) -> analysis.Analysis:
    return analysis.enrich(normalise(simulate(s)), profile)


def measure(an: analysis.Analysis) -> dict:
    k = analysis.kpis(an)
    disc = findings._discounts(an) or {}
    p = an.profile
    pay = an.df[an.df["activity"] == p.act("payment")].groupby("case_id").size()
    appr = an.df[an.df["activity"].isin([p.act("requisition_approval"), p.act("po_change_approval")])]
    dept_col = p.attr.get("department")
    if dept_col in appr.columns:
        appr = appr[appr[dept_col] == BOTTLENECK_DEPT]
    retro = an.df[an.df["activity"] == p.act("po_retroactive")]["case_id"].nunique()
    return {
        "manual_hours_per_year": round(an.df.loc[an.df["manual"], "service_min"].sum() / 60 * an.annual_factor),
        "cycle_days_mean": k["cycle_days_mean"], "cycle_days_median": k["cycle_days_median"],
        "discount_missed_rsd_per_year": disc.get("discount_missed_rsd_per_year", 0),
        "paid_twice_per_year": round(int((pay > 1).sum()) * an.annual_factor),
        "approval_wait_h_mean_logistika": round(float(appr["wait_min"].mean() / 60), 1) if len(appr) else None,
        "maverick_cases_per_year": round(retro * an.annual_factor),
    }


def apply(s: Scenario, lever_ids: list[str]) -> Scenario:
    s2 = copy.deepcopy(s)
    for lid in lever_ids:
        levers.LEVERS[lid].apply(s2)
    return s2


def run(s: Scenario, lever_ids: list[str], base: analysis.Analysis | None = None) -> dict:
    base = base or analyse(s)
    after = analyse(apply(s, lever_ids))
    mb, ma = measure(base), measure(after)
    delta = {k: (round(ma[k] - mb[k], 1) if isinstance(mb[k], (int, float)) and isinstance(ma[k], (int, float)) else None)
             for k in mb}
    return {"levers": lever_ids, "before": mb, "after": ma, "delta": delta}


def compare(s: Scenario, lever_id: str, base: analysis.Analysis | None = None) -> dict:
    """Estimated (from the baseline log) vs simulated effect of one lever, on the lever's primary metric."""
    base = base or analyse(s)
    est = levers.estimate(base, lever_id)
    sim = run(s, [lever_id], base)
    d = sim["delta"]
    if lever_id == "daily_payment_run":
        metric, e_val, s_val = "discount_rsd_per_year", est.cash_rsd_per_year, -d["discount_missed_rsd_per_year"]
    elif lever_id == "approval_escalation":
        metric = "approval_wait_h_mean_logistika"
        e_val = est.effects.get("approval_wait_h_mean_after") if est.applicable else None
        s_val = sim["after"]["approval_wait_h_mean_logistika"]
    else:
        metric, e_val, s_val = "manual_hours_per_year", est.hours_per_year, -d["manual_hours_per_year"]
    err = None
    if e_val is not None and s_val not in (None, 0):
        err = round(100 * (e_val - s_val) / abs(s_val), 1)
    return {"lever": lever_id, "metric": metric, "estimated": e_val, "simulated": s_val, "error_pct": err,
            "confidence": est.confidence, "cycle_days_mean_delta": d["cycle_days_mean"], "estimate": est.to_dict()}


