"""Process profile: maps the activity and attribute names of a specific log to the meanings the P2P detectors and
savings estimators need. The generic analysis (process map, variants, waiting times, rework, automation
candidates) works on any log without a profile; the P2P findings and the savings levers need one.

A profile is a JSON file, so a real company's ERP export can be analysed by writing a profile for its names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Profile:
    name: str = "Purchase-to-Pay"
    system_resources: list[str] = field(default_factory=lambda: [
        "SEF integration", "Matching engine", "Payment run", "ERP", "AP inbox"])
    hourly_cost_rsd: float = 1_100.0       # loaded cost of one hour of office work (assumption, editable)
    discount_days: int = 10                 # "2/10 net 30": discount if paid within 10 days of invoice receipt
    payment_run_hour: int = 10              # hour of the daily payment run in the what-if estimate
    a: dict[str, str] = field(default_factory=lambda: {   # activity roles
        "requisition": "Create Purchase Requisition",
        "requisition_approval": "Approve Purchase Requisition",
        "po_create": "Create Purchase Order",
        "po_send": "Send Purchase Order",
        "po_change": "Change Purchase Order",
        "po_change_approval": "Approve Purchase Order Change",
        "po_retroactive": "Create Purchase Order (retroactive)",
        "goods_receipt": "Receive Goods",
        "invoice_receipt": "Receive Invoice",
        "invoice_entry": "Enter Invoice",
        "invoice_import": "Import Invoice to ERP",
        "match": "Three-way Match",
        "price_discrepancy": "Resolve Price Discrepancy",
        "duplicate_cancel": "Cancel Duplicate Invoice",
        "duplicate_block": "Block Duplicate Invoice",
        "invoice_approval": "Approve Invoice",
        "payment": "Pay Invoice",
        "refund": "Request Refund",
    })
    attr: dict[str, str] = field(default_factory=lambda: {
        "amount": "amount_rsd", "channel": "invoice_channel", "discount_pct": "discount_pct",
        "department": "department", "vendor": "vendor",
    })
    e_invoice_channel_prefix: str = "SEF"
    # waiting before these activities is the vendor's lead time, not internal delay
    external_activities: list[str] = field(default_factory=lambda: ["Receive Goods", "Receive Invoice"])

    def normative_traces(self) -> list[list[str]]:
        """Allowed ("to-be") traces of the main path, used to build the normative model for conformance checking:
        requisition approval only above the threshold, invoice either imported or entered, goods and invoice in
        either order."""
        a = self.a
        out = []
        for approval in (True, False):
            for entry in (a["invoice_entry"], a["invoice_import"]):
                head = [a["requisition"]] + ([a["requisition_approval"]] if approval else []) + [a["po_create"], a["po_send"]]
                inv = [a["invoice_receipt"], entry]
                tail = [a["match"], a["invoice_approval"], a["payment"]]
                out.append(head + [a["goods_receipt"]] + inv + tail)
                out.append(head + inv + [a["goods_receipt"]] + tail)
                out.append(head + [a["invoice_receipt"], a["goods_receipt"], entry] + tail)
        return out

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        p = cls()
        for k, v in data.items():
            if k in ("a", "attr"):
                getattr(p, k).update(v)
            else:
                setattr(p, k, v)
        return p

    def act(self, role: str) -> str:
        return self.a.get(role, f"<{role}>")

    def has_attr(self, df, role: str) -> bool:
        return self.attr.get(role) in df.columns


P2P = Profile()
