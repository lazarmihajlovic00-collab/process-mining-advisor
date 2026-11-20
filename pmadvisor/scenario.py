"""Scenario configuration for the synthetic purchase-to-pay (P2P) company, with planted inefficiencies.

The fictional company is Panonija Trade d.o.o., a wholesale distributor in Novi Sad. Each scenario switches a
set of known problems ("issues") on or off. Because we know which problems were planted, we can measure whether
the analysis finds them (recall), whether it raises false alarms (precision), and whether its savings estimates
match a re-simulation with the fix applied.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass

ISSUES = (
    "approval_bottleneck",       # one department manager approves only on Mondays and Thursdays
    "po_change_rework",          # many purchase orders are changed and re-approved after creation
    "manual_invoice_entry",      # SEF e-invoices are re-typed into the ERP by hand (no integration)
    "manual_three_way_match",    # PO / goods receipt / invoice matched by hand for every invoice
    "price_discrepancy_rework",  # many invoices fail the match on price and go back to the buyer
    "batched_payment_run",       # one payment run per week (Friday), early-payment discounts are lost
    "maverick_buying",           # goods ordered without a PO; PO created after the invoice arrives
    "duplicate_payments",        # duplicate invoices entered and some paid twice
)

ISSUE_LABELS_SR = {
    "approval_bottleneck": "Usko grlo u odobravanju trebovanja",
    "po_change_rework": "Ponovni rad: izmene narudžbenica",
    "manual_invoice_entry": "Ručni unos e-faktura iz SEF-a",
    "manual_three_way_match": "Ručno trostruko uparivanje",
    "price_discrepancy_rework": "Ponovni rad: razlike u ceni na fakturi",
    "batched_payment_run": "Plaćanje jednom nedeljno, propušteni popusti",
    "maverick_buying": "Nabavka bez narudžbenice (PO naknadno)",
    "duplicate_payments": "Duple fakture i duplo plaćanje",
}

DEPARTMENTS = {"Logistika": 0.35, "Prodaja": 0.25, "Održavanje": 0.22, "Administracija": 0.18}

APPROVERS = {  # department -> manager
    "Logistika": "Marko Petrović",
    "Prodaja": "Jelena Ilić",
    "Održavanje": "Dragan Kovačević",
    "Administracija": "Ana Stojanović",
}
DEPUTY_APPROVER = "Milica Đorđević"  # finance controller, used when approvals are escalated
BOTTLENECK_DEPT = "Logistika"


@dataclass
class Scenario:
    seed: int = 7
    name: str = "Panonija Trade, Q3 2026"
    weeks: int = 13
    cases_per_day: float = 28.0
    ap_clerks: int = 2
    buyers: int = 3
    issues: tuple[str, ...] = ()

    # behaviour parameters (set from issues by `build`, can be overridden)
    approval_threshold_rsd: float = 30_000
    bottleneck_days: tuple[int, ...] = (0, 1, 2, 3, 4)
    po_change_rate: float = 0.03
    sef_api_share: float = 0.95
    manual_match: bool = False
    price_discrepancy_rate: float = 0.04
    payment_days: tuple[int, ...] = (0, 1, 2, 3, 4)
    maverick_rate: float = 0.015
    duplicate_rate: float = 0.003
    duplicate_catch_rate: float = 1.0

    # levers (fixes) applied in what-if runs
    duplicate_check: bool = False
    approval_escalation_hours: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["issues"] = list(self.issues)
        return d


def build(seed: int, issues: tuple[str, ...] | list[str], *, vary: bool = True, mild: bool = False,
          **overrides) -> Scenario:
    """A scenario where exactly `issues` are planted. With vary=True the size of the company and the severity of
    each planted problem are drawn from realistic ranges. mild=True plants weaker versions of the problems, close
    to the detection thresholds, to see where detection stops working."""
    rng = random.Random(f"scenario-{seed}")
    s = Scenario(seed=seed, issues=tuple(sorted(issues)))
    if vary:
        s.cases_per_day = round(rng.uniform(20, 34), 1)
        s.ap_clerks = rng.choice([2, 2, 3])
        s.buyers = rng.choice([2, 3, 3])
        s.po_change_rate = round(rng.uniform(0.01, 0.05), 3)
        s.price_discrepancy_rate = round(rng.uniform(0.02, 0.06), 3)
        s.sef_api_share = round(rng.uniform(0.85, 1.0), 2)
        s.maverick_rate = round(rng.uniform(0.0, 0.03), 3)
        s.payment_days = rng.choice([(0, 1, 2, 3, 4), (1, 3), (0, 2, 4)])
    for issue in issues:
        if issue not in ISSUES:
            raise ValueError(f"unknown issue {issue}")
    def u(lo, hi, fixed, mlo=None, mhi=None):
        if not vary:
            return fixed
        return round(rng.uniform(mlo, mhi) if mild else rng.uniform(lo, hi), 3)

    if "approval_bottleneck" in issues:
        s.bottleneck_days = (0, 2, 4) if mild else (0, 3)
    if "po_change_rework" in issues:
        s.po_change_rate = u(0.10, 0.22, 0.15, 0.06, 0.12)
    if "manual_invoice_entry" in issues:
        s.sef_api_share = u(0.0, 0.3, 0.0, 0.4, 0.7)
    if "manual_three_way_match" in issues:
        s.manual_match = True
    if "price_discrepancy_rework" in issues:
        s.price_discrepancy_rate = u(0.11, 0.22, 0.16, 0.06, 0.12)
    if "batched_payment_run" in issues:
        s.payment_days = (4,)
    if "maverick_buying" in issues:
        s.maverick_rate = u(0.07, 0.15, 0.10, 0.035, 0.08)
    if "duplicate_payments" in issues:
        s.duplicate_rate = u(0.015, 0.04, 0.025, 0.004, 0.012)
        s.duplicate_catch_rate = 0.6
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def demo() -> Scenario:
    """The demo company: most problems present, the way a first process-mining project usually looks."""
    return build(7, ["approval_bottleneck", "manual_invoice_entry", "manual_three_way_match",
                     "price_discrepancy_rework", "batched_payment_run", "maverick_buying", "duplicate_payments",
                     "po_change_rework"], vary=False)


def random_issues(seed: int) -> list[str]:
    rng = random.Random(f"issues-{seed}")
    return [i for i in ISSUES if rng.random() < 0.5]
