"""Number grounding check for the advisor's report.

Every number in the report must come from a tool result. The check extracts the numbers from the text (Serbian
formatting: 1.251.000, 15,4%, 1,2 miliona), and looks for each one among all numbers the tools returned, allowing
for rounding. Numbers that cannot be found are listed, so a person sees exactly what to verify; the eval reports
the share of grounded numbers.
"""

from __future__ import annotations

import json
import math
import re

NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?:\s*(miliona|milion|mil\.|hiljada|hilj\.))?", re.I)
DATE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.\s?(?:\d{4})?|\b20\d\d-\d\d-\d\d\b|\b\d{1,2}:\d{2}\b|#\d+|\bQ[1-4]\b")


def numbers_in_text(text: str) -> list[tuple[str, float]]:
    clean = DATE.sub(" ", text)
    clean = re.sub(r"(\d)\.(\d{1,2})(?!\d)", r"\1,\2", clean)  # English decimals (24.0) -> Serbian (24,0)
    out = []
    for m in NUM.finditer(clean):
        whole, frac, mult = m.group(1), m.group(2), (m.group(3) or "").lower()
        v = float(whole.replace(".", "") + ("." + frac if frac else ""))
        if mult.startswith("mil"):
            v *= 1_000_000
        elif mult.startswith("hilj"):
            v *= 1_000
        out.append((m.group(0).strip(), v))
    return out


def numbers_in_data(obj) -> list[float]:
    out: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            if not math.isnan(x):
                out.append(float(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            for _, v in numbers_in_text(x):
                out.append(v)

    walk(obj)
    return out


def _matches(v: float, known: list[float]) -> bool:
    for k in known:
        ak = abs(k)
        if abs(v - ak) <= max(0.051, 0.005 * ak):
            return True
        # rounded forms: 1.251.000 -> 1,25 miliona / 1,3 miliona; 15,44 -> 15; 0.153 -> 15,3%
        for scale in (1_000_000, 1_000):
            if ak >= scale and abs(v - round(ak / scale, 1) * scale) < 1e-6 * scale:
                return True
            if ak >= scale and abs(v - round(ak / scale) * scale) < 1e-6 * scale:
                return True
        if abs(v - round(ak)) < 1e-9 or abs(v - round(ak * 100, 1)) < 1e-9:
            return True
        if ak >= 1 and abs(v - round(ak, -1)) < 1e-9 and ak >= 50:
            return True
    return False


def check(text: str, tool_outputs: list, ignore_small: int = 12) -> dict:
    known = numbers_in_data(tool_outputs)
    found = numbers_in_text(text)
    grounded, ungrounded = [], []
    for raw, v in found:
        if v <= ignore_small and float(v).is_integer():
            continue  # list numbers, priorities, "3 mere", months
        (grounded if _matches(v, known) else ungrounded).append(raw)
    n = len(grounded) + len(ungrounded)
    return {"numbers": n, "grounded": len(grounded), "ungrounded": ungrounded,
            "grounded_pct": round(100 * len(grounded) / n, 1) if n else 100.0}


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)
