"""Loading, validating and normalising event logs (CSV or XES).

Internal format: one row per event with columns case_id, activity, complete (timestamp), optional start and
resource, plus any case or event attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

XES_MAP = {"case:concept:name": "case_id", "concept:name": "activity", "time:timestamp": "complete",
           "org:resource": "resource", "start_timestamp": "start"}


@dataclass
class LogCheck:
    ok: bool
    rows: int
    cases: int
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load(path: str | Path, mapping: dict[str, str] | None = None) -> pd.DataFrame:
    """Load a CSV or XES log. `mapping` renames source columns to case_id / activity / start / complete /
    resource, e.g. {"CaseID": "case_id", "Task": "activity", "EndTime": "complete"}."""
    path = Path(path)
    if path.suffix.lower() in (".xes", ".gz"):
        import pm4py

        df = pm4py.read_xes(str(path))
        df = df.rename(columns={k: v for k, v in XES_MAP.items() if k in df.columns})
        df.columns = [c[5:] if c.startswith("case:") else c for c in df.columns]
    else:
        df = pd.read_csv(path)
    if mapping:
        df = df.rename(columns=mapping)
    return normalise(df)


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("start", "complete"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=False, errors="coerce")
            if getattr(df[col].dt, "tz", None) is not None:
                df[col] = df[col].dt.tz_convert(None)
    if "start" not in df.columns:
        df["start"] = df["complete"]  # no start times: service time unknown (0), waits measured between events
        df.attrs["has_start"] = False
    else:
        df["start"] = df["start"].fillna(df["complete"])
        df.attrs["has_start"] = True
    if "resource" not in df.columns:
        df["resource"] = "unknown"
    df["case_id"] = df["case_id"].astype(str)
    df["activity"] = df["activity"].astype(str)
    df = df.sort_values(["case_id", "complete", "start"], kind="stable").reset_index(drop=True)
    return df


def check(df: pd.DataFrame) -> LogCheck:
    problems, warnings = [], []
    for col in ("case_id", "activity", "complete"):
        if col not in df.columns:
            problems.append(f"missing column {col}")
    if problems:
        return LogCheck(False, len(df), 0, problems)
    n_bad_ts = int(df["complete"].isna().sum())
    if n_bad_ts:
        problems.append(f"{n_bad_ts} events without a valid timestamp")
    neg = int((df["complete"] < df["start"]).sum())
    if neg:
        warnings.append(f"{neg} events finish before they start")
    if not df.attrs.get("has_start", True):
        warnings.append("no start timestamps: service (effort) times cannot be measured, only waiting between events")
    if (df["resource"] == "unknown").all():
        warnings.append("no resource column: workload and bottleneck-by-person analysis is not available")
    singles = int((df.groupby("case_id").size() == 1).sum())
    if singles:
        warnings.append(f"{singles} cases have a single event")
    return LogCheck(not problems, len(df), int(df["case_id"].nunique()), problems, warnings)


def to_pm4py(df: pd.DataFrame) -> pd.DataFrame:
    import pm4py

    out = df.rename(columns={"case_id": "case:concept:name", "activity": "concept:name",
                             "complete": "time:timestamp", "resource": "org:resource"})
    return pm4py.format_dataframe(out, case_id="case:concept:name", activity_key="concept:name",
                                  timestamp_key="time:timestamp")
