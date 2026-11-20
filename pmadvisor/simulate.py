"""Discrete-event simulator of the purchase-to-pay process that writes a realistic event log.

Waiting times are not drawn from distributions; they emerge from people with limited time: AP clerks and buyers
work 08-16, managers look at approvals only in two daily windows, payments go out only on payment-run days.
That is what makes the what-if check meaningful: when a fix removes work or a batch, the simulator recomputes
the queues instead of subtracting a number.

Random draws are made per case and per named quantity up front, so the same case gets the same amounts, vendor,
delays and effort in the baseline run and in every what-if run.
"""

from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass

import pandas as pd

from .scenario import APPROVERS, BOTTLENECK_DEPT, DEPARTMENTS, DEPUTY_APPROVER, Scenario
from .worktime import DAY, add_open, next_open, to_dt

OFFICE = ((480, 960),)          # 08:00-16:00
WAREHOUSE = ((420, 900),)       # 07:00-15:00
APPROVAL_WINDOWS = ((540, 600), (840, 900))  # managers check approvals 09-10 and 14-15
FINANCE_WINDOW = ((780, 840),)  # invoices approved 13-14
PAYMENT_RUN = ((600, 601),)     # bank file at 10:00
ALWAYS = ((0, DAY),)
ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)

# median effort in minutes (person time, not waiting)
EFFORT = {
    "Create Purchase Requisition": 6, "Approve Purchase Requisition": 3, "Create Purchase Order": 12,
    "Change Purchase Order": 10, "Approve Purchase Order Change": 3, "Receive Goods": 9,
    "Enter Invoice": 11, "Three-way Match": 8, "Resolve Price Discrepancy": 22, "Cancel Duplicate Invoice": 6,
    "Approve Invoice": 2, "Request Refund": 25, "Create Purchase Order (retroactive)": 15,
}

SYSTEM_RESOURCES = ("SEF integration", "Matching engine", "Payment run", "ERP", "AP inbox")

DOMESTIC_VENDORS = [
    "Bačka Pak d.o.o.", "Srem Logistik d.o.o.", "Fruška Ambalaža d.o.o.", "Tisa Metal d.o.o.",
    "Novosadska Kancelarija d.o.o.", "Dunav Gume d.o.o.", "Begej Hemija d.o.o.", "Panon IT d.o.o.",
    "Vojvodina Rezervni Delovi d.o.o.", "Petrovaradin Servis d.o.o.", "Sombor Paleta d.o.o.",
    "Zrenjanin Elektro d.o.o.", "Kikinda Alat d.o.o.", "Subotica Print d.o.o.", "Vršac Folija d.o.o.",
    "Palić Čišćenje d.o.o.", "Ruma Transport d.o.o.", "Inđija Oprema d.o.o.", "Šabac Kabl d.o.o.",
    "Beočin Građevina d.o.o.", "Futog Hladnjače d.o.o.", "Bečej Ulja d.o.o.", "Apatin Lanci d.o.o.",
    "Kula Boje d.o.o.", "Vrbas Etikete d.o.o.", "Temerin Zaštita d.o.o.", "Titel Viljuškari d.o.o.",
    "Kovin Gorivo d.o.o.", "Pančevo Signal d.o.o.", "Stara Pazova Klima d.o.o.",
]
FOREIGN_VENDORS = ["Alpen Verpackung GmbH", "Drava Logistics Kft.", "Adria Tools d.o.o. (HR)",
                   "Carpathia Parts SRL", "Balaton Office Kft.", "Mura Pallets d.o.o. (SI)"]


@dataclass
class Resource:
    name: str
    role: str
    windows: tuple
    days: tuple
    queue: bool = True
    free_at: float = 0.0


@dataclass
class Task:
    activity: str
    role: str                 # resource pool, or "@<name>" for a named person / system
    effort: float = 0.0
    ready: float | None = None  # absolute earliest start; None = when the previous task of the case completed
    escalate_to: str | None = None


def _lognorm(rng: random.Random, median: float, sigma: float = 0.35) -> float:
    return median * math.exp(rng.gauss(0, sigma))


def _resources(s: Scenario) -> dict[str, list[Resource]]:
    pools: dict[str, list[Resource]] = {}

    def add(r: Resource):
        pools.setdefault(r.role, []).append(r)
        pools.setdefault("@" + r.name, []).append(r)

    for i, name in enumerate(["Ivana Popović", "Stefan Nikolić", "Tamara Lukić"][: s.buyers]):
        add(Resource(name, "buyer", OFFICE, (0, 1, 2, 3, 4)))
    for name in ["Goran Savić", "Nenad Tomić", "Bojan Jovanović"]:
        add(Resource(name, "warehouse", WAREHOUSE, (0, 1, 2, 3, 4)))
    for name in ["Sanja Pavlović", "Maja Ristić", "Vesna Babić"][: s.ap_clerks]:
        add(Resource(name, "ap_clerk", OFFICE, (0, 1, 2, 3, 4)))
    for dept, name in APPROVERS.items():
        days = s.bottleneck_days if dept == BOTTLENECK_DEPT else (0, 1, 2, 3, 4)
        add(Resource(name, "approver", APPROVAL_WINDOWS, days))
    add(Resource(DEPUTY_APPROVER, "approver", APPROVAL_WINDOWS, (0, 1, 2, 3, 4)))
    add(Resource("Nikola Marković", "finance_manager", FINANCE_WINDOW, (0, 1, 2, 3, 4)))
    for dept in DEPARTMENTS:  # requesters: many people, no queue
        add(Resource(f"Trebovalac ({dept})", "requester", OFFICE, (0, 1, 2, 3, 4), queue=False))
    for name in SYSTEM_RESOURCES:
        windows, days = (PAYMENT_RUN, s.payment_days) if name == "Payment run" else (ALWAYS, ALL_DAYS)
        add(Resource(name, "system", windows, days, queue=False))
    return pools


def _arrivals(s: Scenario) -> list[float]:
    rng = random.Random(f"arrivals-{s.seed}")
    out = []
    for d in range(s.weeks * 7):
        if d % 7 >= 5:
            continue
        n = sum(1 for _ in range(int(s.cases_per_day * 3)) if rng.random() < 1 / 3)  # ~binomial(cases_per_day)
        for _ in range(n):
            out.append(d * DAY + 480 + rng.random() * 480)
    return sorted(out)


def _case_draws(seed: int, idx: int) -> dict:
    """Every random quantity a case can ever need, drawn up front in a fixed order."""
    rng = random.Random(f"case-{seed}-{idx}")
    r = {k: rng.random() for k in ("maverick", "po_change", "approval", "discrepancy", "duplicate", "dup_caught",
                                   "sef_api", "dept", "vendor", "foreign")}
    r["amount"] = round(math.exp(rng.gauss(math.log(85_000), 1.0)), -2)
    r["lead_days"] = rng.uniform(2, 9)
    r["invoice_offset_days"] = rng.gauss(0.8, 2.0)
    r["credit_note_days"] = rng.uniform(1, 4)
    r["dup_delay_days"] = rng.uniform(1, 6)
    r["refund_delay_days"] = rng.uniform(7, 25)
    r["vendor_idx"] = rng.randrange(1000)
    r["effort"] = {a: _lognorm(rng, m) for a, m in EFFORT.items()}
    r["effort"]["Enter Invoice#2"] = _lognorm(rng, EFFORT["Enter Invoice"])
    r["effort"]["Three-way Match#2"] = _lognorm(rng, EFFORT["Three-way Match"])
    r["invoice_minute"] = rng.random() * DAY
    return r


def _pick(weights: dict[str, float], u: float) -> str:
    acc = 0.0
    for k, w in weights.items():
        acc += w
        if u < acc:
            return k
    return list(weights)[-1]


def _case_process(s: Scenario, idx: int, arrival: float, attrs: dict, r: dict):
    """Generator: yields Tasks, receives each task's completion time."""
    e = r["effort"]
    approver = "@" + APPROVERS[attrs["department"]]
    escalate = DEPUTY_APPROVER if (s.approval_escalation_hours and attrs["department"] == BOTTLENECK_DEPT) else None
    maverick = r["maverick"] < s.maverick_rate

    if not maverick:
        t = yield Task("Create Purchase Requisition", f"@Trebovalac ({attrs['department']})", e["Create Purchase Requisition"], ready=arrival)
        if attrs["amount_rsd"] >= s.approval_threshold_rsd:
            t = yield Task("Approve Purchase Requisition", approver, e["Approve Purchase Requisition"], escalate_to=escalate)
        t = yield Task("Create Purchase Order", "buyer", e["Create Purchase Order"])
        if r["po_change"] < s.po_change_rate:
            t = yield Task("Change Purchase Order", "buyer", e["Change Purchase Order"])
            t = yield Task("Approve Purchase Order Change", approver, e["Approve Purchase Order Change"], escalate_to=escalate)
        t = yield Task("Send Purchase Order", "@ERP", 0.0)
        ordered = t
    else:
        ordered = arrival  # ordered by phone, no requisition, no PO

    goods_at = next_open(ordered + r["lead_days"] * DAY, WAREHOUSE)
    inv_at = max(ordered + 60, goods_at + r["invoice_offset_days"] * DAY)
    channel = attrs["invoice_channel"]
    inbox = "@SEF integration" if channel.startswith("SEF") else "@AP inbox"

    # invoice branch and goods branch; emit in time order
    done: dict[str, float] = {}

    def invoice_branch():
        t = yield Task("Receive Invoice", inbox, 0.0, ready=inv_at)
        done["received"] = t
        if channel == "SEF (API import)":
            t = yield Task("Import Invoice to ERP", "@SEF integration", 0.0)
        else:
            t = yield Task("Enter Invoice", "ap_clerk", e["Enter Invoice"])
        done["entered"] = t
        if r["duplicate"] < s.duplicate_rate:
            when = t + r["dup_delay_days"] * DAY
            if s.duplicate_check:
                t2 = yield Task("Block Duplicate Invoice", "@ERP", 0.0, ready=when)
            else:
                t2 = yield Task("Enter Invoice", "ap_clerk", e["Enter Invoice#2"], ready=when)
                done["dup_entered"] = t2

    def goods_branch():
        t = yield Task("Receive Goods", "warehouse", e["Receive Goods"], ready=goods_at)
        done["goods"] = t

    first, second = (invoice_branch, goods_branch) if inv_at <= goods_at else (goods_branch, invoice_branch)
    for branch in (first, second):
        g = branch()
        try:
            task = next(g)
            while True:
                t = yield task
                task = g.send(t)
        except StopIteration:
            pass

    ready = max(done["entered"], done["goods"])
    if maverick:
        t = yield Task("Create Purchase Order (retroactive)", "buyer", e["Create Purchase Order (retroactive)"], ready=ready)
        ready = t

    def match(n: int):
        if s.manual_match:
            return Task("Three-way Match", "ap_clerk", e["Three-way Match" if n == 1 else "Three-way Match#2"])
        return Task("Three-way Match", "@Matching engine", 0.5)

    first_match = match(1)
    first_match.ready = ready
    t = yield first_match
    if r["discrepancy"] < s.price_discrepancy_rate:
        t = yield Task("Resolve Price Discrepancy", "buyer", e["Resolve Price Discrepancy"])
        second = match(2)
        second.ready = t + r["credit_note_days"] * DAY
        t = yield second
    duplicate_unpaid_check = "dup_entered" in done
    caught = duplicate_unpaid_check and r["dup_caught"] < s.duplicate_catch_rate
    if caught:
        t = yield Task("Cancel Duplicate Invoice", "ap_clerk", e["Cancel Duplicate Invoice"], ready=max(t, done["dup_entered"]))
    t = yield Task("Approve Invoice", "@Nikola Marković", e["Approve Invoice"])
    t = yield Task("Pay Invoice", "@Payment run", 0.0)
    if duplicate_unpaid_check and not caught:
        t = yield Task("Pay Invoice", "@Payment run", 0.0, ready=t + 1)
        yield Task("Request Refund", "ap_clerk", e["Request Refund"], ready=t + r["refund_delay_days"] * DAY)


def _vendor(r: dict) -> tuple[str, str, float]:
    if r["foreign"] < 0.10:
        return FOREIGN_VENDORS[r["vendor_idx"] % len(FOREIGN_VENDORS)], "foreign", 0.0
    name = DOMESTIC_VENDORS[r["vendor_idx"] % len(DOMESTIC_VENDORS)]
    discount = 2.0 if (r["vendor_idx"] % len(DOMESTIC_VENDORS)) % 10 < 3 else 0.0  # 9 of 30 offer 2/10 net 30
    return name, "domestic", discount


def simulate(s: Scenario) -> pd.DataFrame:
    pools = _resources(s)
    heap: list = []
    seq = 0
    cases = {}
    for idx, arrival in enumerate(_arrivals(s)):
        r = _case_draws(s.seed, idx)
        vendor, origin, discount = _vendor(r)
        if origin == "foreign":
            channel = "Email PDF"
        else:
            channel = "SEF (API import)" if r["sef_api"] < s.sef_api_share else "SEF (manual entry)"
        attrs = {"case_id": f"PR-2026-{idx + 1:05d}", "department": _pick(DEPARTMENTS, r["dept"]), "vendor": vendor,
                 "vendor_origin": origin, "amount_rsd": r["amount"], "invoice_channel": channel,
                 "discount_pct": discount}
        gen = _case_process(s, idx, arrival, attrs, r)
        task = next(gen)
        cases[idx] = attrs
        heapq.heappush(heap, (task.ready if task.ready is not None else arrival, seq, idx, gen, task))
        seq += 1

    rows = []
    while heap:
        ready, _, idx, gen, task = heapq.heappop(heap)
        start, res = _assign(pools, task, ready, s)
        end = add_open(start, task.effort, res.windows, res.days) if task.effort > 0 else start
        if res.queue:
            res.free_at = end
        rows.append({**cases[idx], "activity": task.activity, "start": start, "complete": end,
                     "resource": res.name, "role": res.role})
        try:
            nxt = gen.send(end)
        except StopIteration:
            continue
        heapq.heappush(heap, (nxt.ready if nxt.ready is not None else end, seq, idx, gen, nxt))
        seq += 1

    df = pd.DataFrame(rows)
    df["start"] = df["start"].map(to_dt).dt.round("s")
    df["complete"] = df["complete"].map(to_dt).dt.round("s")
    df = df.sort_values(["case_id", "complete", "start"], kind="stable").reset_index(drop=True)
    cols = ["case_id", "activity", "start", "complete", "resource", "role", "department", "vendor",
            "vendor_origin", "amount_rsd", "invoice_channel", "discount_pct"]
    return df[cols]


def _earliest(res: Resource, ready: float, effort: float = 0.0) -> float:
    """First moment the resource can start; a task that does not fit into the rest of the current window starts in
    the next window (people do not start a task at 15:58 and finish it the next morning)."""
    t = next_open(max(ready, res.free_at) if res.queue else ready, res.windows, res.days)
    for _ in range(50):
        m = t % DAY
        end = next((e for s, e in res.windows if s <= m < e), None)
        if end is None or effort <= 0 or t - m + end - t >= effort or effort > max(e - s for s, e in res.windows):
            return t
        t = next_open(t - m + end, res.windows, res.days)
    return t


def _assign(pools: dict[str, list[Resource]], task: Task, ready: float, s: Scenario) -> tuple[float, Resource]:
    cands = pools[task.role]
    best = min(cands, key=lambda r: _earliest(r, ready, task.effort))
    start = _earliest(best, ready, task.effort)
    if task.escalate_to and s.approval_escalation_hours:
        deputy = pools["@" + task.escalate_to][0]
        limit = ready + s.approval_escalation_hours * 60
        if start > limit:
            d_start = _earliest(deputy, limit, task.effort)
            if d_start < start:
                return d_start, deputy
    return start, best
