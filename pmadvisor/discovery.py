"""Process discovery and conformance checking with pm4py.

- Discovered model: Inductive Miner (infrequent variant, noise threshold 0.2) on the actual log. It shows the
  process as it really runs, with the rare paths filtered out.
- Normative model: Inductive Miner on the profile's allowed ("to-be") traces.
- Conformance: token-based replay of every case on the normative model. A case that does not fit took a path
  the company did not intend (retroactive PO, paid twice, re-approval loops...). The most frequent deviating
  variants are reported with what is extra or missing compared with the closest allowed trace.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

from .analysis import Analysis
from .eventlog import to_pm4py

warnings.filterwarnings("ignore", category=UserWarning, module="pm4py")


def _pm4py():
    import pm4py

    return pm4py


def discover(an: Analysis, noise: float = 0.2):
    pm4py = _pm4py()
    log = to_pm4py(an.df[["case_id", "activity", "complete", "resource"]])
    return pm4py.discover_petri_net_inductive(log, noise_threshold=noise)


def normative_model(an: Analysis):
    pm4py = _pm4py()
    rows = []
    for i, trace in enumerate(an.profile.normative_traces()):
        for j, act in enumerate(trace):
            rows.append({"case_id": f"N{i}", "activity": act, "complete": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=j),
                         "resource": "model"})
    log = to_pm4py(pd.DataFrame(rows))
    return pm4py.discover_petri_net_inductive(log, noise_threshold=0.0)


def conformance(an: Analysis, top: int = 6) -> dict:
    pm4py = _pm4py()
    net, im, fm = normative_model(an)
    log = to_pm4py(an.df[["case_id", "activity", "complete", "resource"]])
    diag = pm4py.conformance_diagnostics_token_based_replay(log, net, im, fm)
    fits = pd.Series([bool(d["trace_is_fit"]) for d in diag])
    fitness = pm4py.fitness_token_based_replay(log, net, im, fm)
    allowed = [tuple(t) for t in an.profile.normative_traces()]
    allowed_set = set(allowed)
    vc = an.cases["variant"].value_counts()
    deviating = []
    for v, n in vc.items():
        if v in allowed_set:
            continue
        closest = min(allowed, key=lambda t: _edit(v, t))
        extra = _multiset_diff(v, closest)
        missing = _multiset_diff(closest, v)
        order_only = not extra and not missing
        deviating.append({"cases": int(n), "share_pct": round(100 * n / len(an.cases), 1), "extra": extra,
                          "missing": missing, "order_only": order_only})
    by_pattern: dict[tuple, dict] = {}
    for d in deviating:
        key = (tuple(d["extra"]), tuple(d["missing"]), d["order_only"])
        agg = by_pattern.setdefault(key, {"cases": 0, "extra": d["extra"], "missing": d["missing"],
                                          "order_only": d["order_only"]})
        agg["cases"] += d["cases"]
    patterns = sorted(by_pattern.values(), key=lambda d: -d["cases"])[:top]
    for p in patterns:
        p["share_pct"] = round(100 * p["cases"] / len(an.cases), 1)
    return {
        "fitting_cases_pct": round(100 * float(fits.mean()), 1),
        "log_fitness": round(float(fitness.get("log_fitness", 0)), 3),
        "deviation_patterns": patterns,
    }


def bpmn_xml(an: Analysis, path: str | Path, noise: float = 0.2) -> Path:
    """Discovered model as BPMN 2.0 XML (opens in Camunda Modeler or bpmn.io)."""
    pm4py = _pm4py()
    log = to_pm4py(an.df[["case_id", "activity", "complete", "resource"]])
    tree = pm4py.discover_process_tree_inductive(log, noise_threshold=noise)
    bpmn = pm4py.convert_to_bpmn(tree)
    path = Path(path)
    pm4py.write_bpmn(bpmn, str(path), auto_layout=False)  # layout would need the Graphviz binary
    return path


def model_summary(an: Analysis, noise: float = 0.2) -> dict:
    pm4py = _pm4py()
    net, im, fm = discover(an, noise)
    log = to_pm4py(an.df[["case_id", "activity", "complete", "resource"]])
    fit = pm4py.fitness_token_based_replay(log, net, im, fm)
    prec = pm4py.precision_token_based_replay(log, net, im, fm)
    return {"algorithm": f"Inductive Miner infrequent (noise {noise})", "places": len(net.places),
            "transitions": len([t for t in net.transitions if t.label]), "log_fitness": round(float(fit["log_fitness"]), 3),
            "precision": round(float(prec), 3)}


def _edit(a: tuple, b: tuple) -> int:
    n, m = len(a), len(b)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[m]


def _multiset_diff(a: tuple, b: tuple) -> list[str]:
    from collections import Counter

    d = Counter(a) - Counter(b)
    return sorted(d.elements())
