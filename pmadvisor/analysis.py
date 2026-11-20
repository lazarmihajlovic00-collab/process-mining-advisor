"""Generic process analysis on any event log: KPIs, activity statistics, directly-follows graph with waiting
times, variants, rework, resource workload and a ranking of automation candidates.

Definitions used everywhere (and in the interview guide):
- service time = complete - start of an event (person effort; 0 for system events and logs without start times)
- waiting time = start of an event - completion of the previous event of the same case
- manual event = an event whose resource is a person (not in the profile's system resources) and service > 0
- annualised = value over the log period x 365 / days covered by case starts
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .profile import Profile


@dataclass
class Analysis:
    df: pd.DataFrame            # enriched events
    cases: pd.DataFrame         # one row per case
    profile: Profile
    annual_factor: float
    period_days: float


def enrich(df: pd.DataFrame, profile: Profile) -> Analysis:
    df = df.sort_values(["case_id", "complete", "start"], kind="stable").reset_index(drop=True).copy()
    prev_complete = df.groupby("case_id")["complete"].shift(1)
    df["prev_activity"] = df.groupby("case_id")["activity"].shift(1)
    df["service_min"] = (df["complete"] - df["start"]).dt.total_seconds().clip(lower=0) / 60
    df["wait_min"] = ((df["start"] - prev_complete).dt.total_seconds() / 60).clip(lower=0).fillna(0)
    person = ~df["resource"].isin(profile.system_resources)
    # without start timestamps there is no service time; then every event by a person counts as manual
    df["manual"] = person & (df["service_min"] > 0) if df.attrs.get("has_start", True) else person
    df["seq"] = df.groupby("case_id").cumcount()

    g = df.groupby("case_id")
    cases = pd.DataFrame({
        "start": g["start"].min(), "end": g["complete"].max(), "events": g.size(),
        "manual_min": g.apply(lambda x: x.loc[x["manual"], "service_min"].sum(), include_groups=False),
        "variant": g["activity"].agg(tuple),
    })
    cases["cycle_days"] = (cases["end"] - cases["start"]).dt.total_seconds() / 86400
    for col in df.columns:
        if col in ("case_id",) or col in ("activity", "start", "complete", "resource", "role", "prev_activity",
                                          "service_min", "wait_min", "manual", "seq"):
            continue
        if df.groupby("case_id")[col].nunique(dropna=False).max() == 1:
            cases[col] = g[col].first()
    span = (cases["start"].max() - cases["start"].min()).total_seconds() / 86400
    period = max(span, 1.0)
    return Analysis(df, cases, profile, 365.0 / period, period)


def kpis(an: Analysis) -> dict:
    c = an.cases
    top_share = c["variant"].value_counts(normalize=True).iloc[0] if len(c) else 0
    manual_h = an.df.loc[an.df["manual"], "service_min"].sum() / 60
    return {
        "cases": int(len(c)), "events": int(len(an.df)), "activities": int(an.df["activity"].nunique()),
        "variants": int(c["variant"].nunique()),
        "period": f"{c['start'].min():%Y-%m-%d} – {c['start'].max():%Y-%m-%d}",
        "period_days": round(an.period_days, 1), "annual_factor": round(an.annual_factor, 3),
        "cycle_days_median": round(float(c["cycle_days"].median()), 1),
        "cycle_days_mean": round(float(c["cycle_days"].mean()), 1),
        "cycle_days_p90": round(float(c["cycle_days"].quantile(0.9)), 1),
        "top_variant_share_pct": round(100 * float(top_share), 1),
        "manual_hours_period": round(float(manual_h), 1),
        "manual_hours_per_year": round(float(manual_h * an.annual_factor)),
        "manual_fte": round(float(manual_h * an.annual_factor / 1_720), 2),  # 1,720 productive hours per FTE-year
    }


def activity_stats(an: Analysis) -> list[dict]:
    df = an.df
    out = []
    for act, x in df.groupby("activity"):
        manual = x[x["manual"]]
        out.append({
            "activity": act, "events": int(len(x)), "cases": int(x["case_id"].nunique()),
            "case_share_pct": round(100 * x["case_id"].nunique() / len(an.cases), 1),
            "repeat_cases": int((x.groupby("case_id").size() > 1).sum()),
            "service_min_median": round(float(x["service_min"].median()), 1),
            "wait_h_median": round(float(x["wait_min"].median() / 60), 1),
            "wait_h_mean": round(float(x["wait_min"].mean() / 60), 1),
            "manual_share_pct": round(100 * len(manual) / len(x), 1),
            "manual_hours_per_year": round(float(manual["service_min"].sum() / 60 * an.annual_factor)),
            "resources": int(x["resource"].nunique()),
            "top_resources": x["resource"].value_counts().head(3).to_dict(),
        })
    return sorted(out, key=lambda r: -r["events"])


def dfg(an: Analysis, min_share: float = 0.0) -> dict:
    """Directly-follows graph with frequency and median waiting time on each edge. Start/end pseudo-nodes."""
    df = an.df
    nxt = df.groupby("case_id")["activity"].shift(-1)
    edges = pd.DataFrame({"a": df["activity"], "b": nxt,
                          "wait_h": df.groupby("case_id")["wait_min"].shift(-1) / 60}).dropna(subset=["b"])
    agg = edges.groupby(["a", "b"]).agg(count=("wait_h", "size"), wait_h_median=("wait_h", "median"),
                                        wait_h_mean=("wait_h", "mean")).reset_index()
    first = df[df["seq"] == 0]["activity"].value_counts()
    last = df.groupby("case_id")["activity"].last().value_counts()
    n = len(an.cases)
    out_edges = [{"source": r.a, "target": r.b, "count": int(r.count), "wait_h_median": round(float(r.wait_h_median), 1),
                  "wait_h_mean": round(float(r.wait_h_mean), 1)} for r in agg.itertuples() if r.count / n >= min_share]
    out_edges += [{"source": "▶ start", "target": a, "count": int(c), "wait_h_median": 0, "wait_h_mean": 0}
                  for a, c in first.items()]
    out_edges += [{"source": a, "target": "■ end", "count": int(c), "wait_h_median": 0, "wait_h_mean": 0}
                  for a, c in last.items()]
    stats = {s["activity"]: s for s in activity_stats(an)}
    nodes = [{"id": a, "events": s["events"], "service_min_median": s["service_min_median"],
              "manual_share_pct": s["manual_share_pct"], "wait_h_median": s["wait_h_median"]} for a, s in stats.items()]
    nodes += [{"id": "▶ start", "events": n}, {"id": "■ end", "events": n}]
    return {"nodes": nodes, "edges": out_edges}


def variants(an: Analysis, top: int = 10) -> list[dict]:
    c = an.cases
    g = c.groupby("variant").agg(cases=("cycle_days", "size"), cycle_days_median=("cycle_days", "median"))
    g = g.sort_values("cases", ascending=False)
    n = len(c)
    out, cum = [], 0
    for v, r in g.head(top).iterrows():
        cum += r.cases
        out.append({"variant": list(v), "steps": len(v), "cases": int(r.cases), "share_pct": round(100 * r.cases / n, 1),
                    "cumulative_pct": round(100 * cum / n, 1), "cycle_days_median": round(float(r.cycle_days_median), 1)})
    return out


def rework(an: Analysis) -> list[dict]:
    """Activities executed more than once in the same case (loops)."""
    df = an.df
    counts = df.groupby(["case_id", "activity"]).size()
    rep = counts[counts > 1].reset_index(name="n")
    out = []
    for act, x in rep.groupby("activity"):
        extra = df[(df["activity"] == act) & df["case_id"].isin(x["case_id"])]
        extra_manual_min = extra.groupby("case_id")["service_min"].apply(lambda s: s.iloc[1:].sum()).sum()
        out.append({"activity": act, "cases": int(len(x)), "case_share_pct": round(100 * len(x) / len(an.cases), 1),
                    "extra_executions": int((x["n"] - 1).sum()),
                    "extra_hours_per_year": round(float(extra_manual_min / 60 * an.annual_factor))})
    return sorted(out, key=lambda r: -r["cases"])


def resources(an: Analysis) -> list[dict]:
    df = an.df
    out = []
    for res, x in df.groupby("resource"):
        days = max(an.period_days * 5 / 7, 1)
        out.append({"resource": res, "events": int(len(x)), "activities": sorted(x["activity"].unique().tolist())[:6],
                    "manual_hours_period": round(float(x.loc[x["manual"], "service_min"].sum() / 60), 1),
                    "busy_share_pct": round(100 * float(x.loc[x["manual"], "service_min"].sum()) / (days * 480), 1),
                    "wait_h_median_before_their_tasks": round(float(x["wait_min"].median() / 60), 1)})
    return sorted(out, key=lambda r: -r["events"])


def automation_candidates(an: Analysis, top: int = 8) -> list[dict]:
    """Manual activities ranked by annual person-hours. Repetitive, high-volume, short and uniform tasks are the
    classic candidates; the coefficient of variation of service time is reported as a hint of uniformity."""
    df = an.df[an.df["manual"]]
    out = []
    for act, x in df.groupby("activity"):
        s = x["service_min"]
        out.append({"activity": act, "manual_events_per_year": round(float(len(x) * an.annual_factor)),
                    "service_min_median": round(float(s.median()), 1),
                    "service_cv": round(float(s.std() / s.mean()), 2) if len(s) > 1 and s.mean() > 0 else None,
                    "hours_per_year": round(float(s.sum() / 60 * an.annual_factor)),
                    "cost_rsd_per_year": round(float(s.sum() / 60 * an.annual_factor * an.profile.hourly_cost_rsd), -3),
                    "people": int(x["resource"].nunique())})
    return sorted(out, key=lambda r: -r["hours_per_year"])[:top]


def waiting_hotspots(an: Analysis, top: int = 6) -> list[dict]:
    """Internal activities with the largest total waiting before them (case-days per year). Waiting for the
    vendor (delivery, invoice) is excluded."""
    df = an.df[~an.df["activity"].isin(an.profile.external_activities)]
    g = df.groupby("activity")["wait_min"].agg(["sum", "median", "size"])
    g["case_days_per_year"] = g["sum"] / 1440 * an.annual_factor
    g = g.sort_values("case_days_per_year", ascending=False).head(top)
    return [{"activity": a, "wait_h_median": round(float(r["median"] / 60), 1), "events": int(r["size"]),
             "waiting_case_days_per_year": round(float(r["case_days_per_year"]))} for a, r in g.iterrows()]


def summary(an: Analysis) -> dict:
    return {"kpis": kpis(an), "automation_candidates": automation_candidates(an), "rework": rework(an),
            "waiting_hotspots": waiting_hotspots(an), "top_variants": variants(an, 5)}
