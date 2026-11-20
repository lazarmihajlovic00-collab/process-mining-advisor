"""Evaluation: does the advisor find the problems that were planted, and are its savings estimates right?

1. Detection. Random scenarios (each of the 8 problems planted with probability 0.5, company size and severity
   drawn from ranges). Precision / recall of the detectors against the planted problems, on four kinds of logs:
   clear problems, mild problems (near the thresholds), noisy logs (5% of events missing, timestamps jittered
   by up to 30 minutes) and logs without start timestamps.
2. Estimates. For every planted problem, the lever's log-only estimate vs a re-simulation of the same quarter
   with the lever applied. Also the sum of estimates vs one simulation with all levers together (overlap).
3. AI advisor (optional, --llm). On a few scenarios: does it recommend a lever for each planted problem, does it
   recommend levers for problems that are not there, and are all numbers in its report found in tool results.
"""

from __future__ import annotations

import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .. import analysis, findings, llm, whatif
from ..eventlog import normalise
from ..findings import LEVERS_FOR
from ..profile import P2P
from ..scenario import ISSUES, build, random_issues
from ..simulate import simulate

MECHANICAL = {"sef_erp_integration", "auto_three_way_match", "daily_payment_run", "duplicate_check", "approval_escalation"}


def degrade(df: pd.DataFrame, kind: str, seed: int) -> pd.DataFrame:
    rng = random.Random(f"noise-{seed}")
    df = df.copy()
    if kind == "noisy":
        keep = [i for i in range(len(df)) if rng.random() >= 0.05]
        df = df.iloc[keep].copy()
        jitter = pd.to_timedelta([rng.uniform(-30, 30) for _ in range(len(df))], unit="m")
        df["start"] = df["start"] + jitter
        df["complete"] = df["complete"] + jitter
    elif kind == "no_start":
        df = df.drop(columns=["start"])
    return normalise(df)


def detection(seeds: int) -> dict:
    out = {}
    for kind in ("clear", "mild", "noisy", "no_start"):
        tp, fp, fn = defaultdict(int), defaultdict(int), defaultdict(int)
        for seed in range(1, seeds + 1):
            planted = random_issues(seed)
            s = build(seed, planted, mild=(kind == "mild"))
            df = simulate(s)
            df = degrade(df, kind, seed) if kind in ("noisy", "no_start") else normalise(df)
            got = {f.id for f in findings.detect(analysis.enrich(df, P2P))}
            for i in ISSUES:
                if i in planted and i in got:
                    tp[i] += 1
                elif i in planted:
                    fn[i] += 1
                elif i in got:
                    fp[i] += 1
        per = {}
        for i in ISSUES:
            p = tp[i] / (tp[i] + fp[i]) if tp[i] + fp[i] else None
            r = tp[i] / (tp[i] + fn[i]) if tp[i] + fn[i] else None
            per[i] = {"tp": tp[i], "fp": fp[i], "fn": fn[i], "precision": p, "recall": r}
        T, F, N = sum(tp.values()), sum(fp.values()), sum(fn.values())
        out[kind] = {"precision": T / (T + F) if T + F else None, "recall": T / (T + N) if T + N else None,
                     "tp": T, "fp": F, "fn": N, "per_issue": per}
    return out


def estimates(seeds: int) -> dict:
    rows = []
    combo = []
    for seed in range(1, seeds + 1):
        planted = random_issues(seed) or ["manual_invoice_entry"]
        s = build(seed, planted)
        base = whatif.analyse(s)
        levers = [LEVERS_FOR[i][0] for i in planted]
        for lid in levers:
            c = whatif.compare(s, lid, base)
            rows.append({"seed": seed, **{k: c[k] for k in ("lever", "metric", "estimated", "simulated", "error_pct",
                                                              "confidence", "cycle_days_mean_delta")}})
        mech = [l for l in levers if l in MECHANICAL and l != "approval_escalation"]
        if len(mech) >= 2:
            est_sum = sum(r["estimated"] for r in rows if r["seed"] == seed and r["lever"] in mech and r["metric"] == "manual_hours_per_year")
            sim = whatif.run(s, mech, base)
            combo.append({"seed": seed, "levers": mech, "sum_of_estimates_h": est_sum,
                          "simulated_together_h": -sim["delta"]["manual_hours_per_year"],
                          "cycle_days_mean_delta": sim["delta"]["cycle_days_mean"]})
    by = defaultdict(list)
    for r in rows:
        by[r["lever"]].append(r)
    summary = {}
    for lid, rs in by.items():
        errs = [abs(r["error_pct"]) for r in rs if r["error_pct"] is not None]
        summary[lid] = {"n": len(rs), "metric": rs[0]["metric"], "confidence": rs[0]["confidence"],
                        "mean_abs_error_pct": round(statistics.mean(errs), 1) if errs else None,
                        "max_abs_error_pct": round(max(errs), 1) if errs else None,
                        "mean_estimated": round(statistics.mean(r["estimated"] for r in rs if r["estimated"] is not None), 1),
                        "mean_simulated": round(statistics.mean(r["simulated"] for r in rs if r["simulated"] is not None), 1),
                        "mean_cycle_days_delta": round(statistics.mean(r["cycle_days_mean_delta"] for r in rs), 2)}
    return {"per_lever": summary, "combined": combo, "rows": rows}


def advisor_eval(seeds: int) -> dict:
    from ..advisor import runner as adv
    from ..service import Workspace

    results = []
    for seed in range(101, 101 + seeds):
        planted = random_issues(seed) or ["batched_payment_run"]
        ws = Workspace()
        ws.load_scenario(build(seed, planted), f"Eval scenario {seed}")
        detected = {f["id"] for f in ws.findings()}
        t0 = time.perf_counter()
        res = adv.advise(ws, "eval")
        recs = {r["lever"] for r in ws.recommendations()}
        wanted = {LEVERS_FOR[i][0] for i in planted}
        results.append({"seed": seed, "planted": planted, "detected": sorted(detected), "recommended": sorted(recs),
                        "covered": len(wanted & recs), "wanted": len(wanted), "extra": sorted(recs - wanted),
                        "grounding": res.grounding, "llm_calls": res.llm_calls, "tool_calls": len(res.tool_calls),
                        "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "cost_usd": res.cost_usd,
                        "latency_s": round(time.perf_counter() - t0, 1), "error": res.error, "source": res.source})
        time.sleep(2)
    ok = [r for r in results if not r["error"]]
    for r in results:
        if r["error"]:
            r["error"] = r["error"][:120]
    W, C = sum(r["wanted"] for r in ok), sum(r["covered"] for r in ok)
    nums = sum(r["grounding"].get("numbers", 0) for r in ok)
    grounded = sum(r["grounding"].get("grounded", 0) for r in ok)
    return {"provider": llm.provider(), "model": llm.model_name(), "scenarios": len(results), "completed": len(ok),
            "coverage_pct": round(100 * C / W, 1) if W else None,
            "extra_recommendations": sum(len(r["extra"]) for r in ok),
            "numbers": nums, "grounded_pct": round(100 * grounded / nums, 1) if nums else None,
            "mean_latency_s": round(statistics.mean(r["latency_s"] for r in ok), 1) if ok else None,
            "mean_llm_calls": round(statistics.mean(r["llm_calls"] for r in ok), 1) if ok else None,
            "mean_tokens_in": round(statistics.mean(r["input_tokens"] for r in ok)) if ok else None,
            "mean_tokens_out": round(statistics.mean(r["output_tokens"] for r in ok)) if ok else None,
            "total_cost_usd": round(sum(r["cost_usd"] for r in ok), 4), "results": results}


def _pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def report_md(det: dict, est: dict, adv: dict | None, seeds: int, est_seeds: int) -> str:
    L = ["# Evaluation report", "",
         f"Generated by `pma eval`. Synthetic purchase-to-pay logs from the built-in simulator; each of the 8 problems "
         f"is planted with probability 0.5 per scenario. Detection: {seeds} scenarios per log type. Estimates: "
         f"{est_seeds} scenarios.", "",
         "## 1. Detection of planted problems", "",
         "| Log type | Precision | Recall | TP | FP | FN |", "|---|---|---|---|---|---|"]
    names = {"clear": "clear problems", "mild": "mild problems (near thresholds)",
             "noisy": "noisy (5% events missing, ±30 min jitter)", "no_start": "no start timestamps"}
    for k, d in det.items():
        L.append(f"| {names[k]} | {_pct(d['precision'])} | {_pct(d['recall'])} | {d['tp']} | {d['fp']} | {d['fn']} |")
    L += ["", "Recall per problem:", "", "| Problem | " + " | ".join(names[k] for k in det) + " |",
          "|---|" + "---|" * len(det)]
    for i in ISSUES:
        cells = []
        for k in det:
            p = det[k]["per_issue"][i]
            cells.append(f"{_pct(p['recall'])} ({p['fp']} FP)")
        L.append(f"| {i} | " + " | ".join(cells) + " |")
    L += ["", "## 2. Savings estimate (log only) vs re-simulation with the lever applied", "",
          "| Lever | Metric | Kind | n | Mean estimated | Mean simulated | Mean abs error | Max abs error | Cycle time Δ (days, simulated) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for lid, s in sorted(est["per_lever"].items()):
        L.append(f"| {lid} | {s['metric']} | {s['confidence']} | {s['n']} | {s['mean_estimated']} | {s['mean_simulated']} | "
                 f"{s['mean_abs_error_pct']}% | {s['max_abs_error_pct']}% | {s['mean_cycle_days_delta']} |")
    if est["combined"]:
        over = [100 * (c["sum_of_estimates_h"] - c["simulated_together_h"]) / c["simulated_together_h"]
                for c in est["combined"] if c["simulated_together_h"]]
        diff = [100 * (c["sum_of_estimates_h"] - c["simulated_together_h"]) / c["simulated_together_h"]
                for c in est["combined"] if c["simulated_together_h"]]
        L += ["", "Several mechanical levers together: sum of separate log estimates vs one simulation with all of them.", "",
              "| Scenario | Levers | Sum of estimates (h/year) | Simulated together (h/year) | Difference | Cycle time Δ (days) |",
              "|---|---|---|---|---|---|"]
        for c, d in zip(est["combined"], diff):
            L.append(f"| {c['seed']} | {', '.join(c['levers'])} | {c['sum_of_estimates_h']:.0f} | {c['simulated_together_h']:.0f} | "
                     f"{d:+.1f}% | {c['cycle_days_mean_delta']} |")
        L += ["", f"Mean difference {statistics.mean(diff):+.1f}% (positive = the sum overstates the combined saving, "
              f"because levers remove the same steps)."]
    if adv:
        L += ["", "## 3. AI advisor", "",
              f"Provider `{adv['provider']}`, model `{adv['model']}`, {adv['completed']} of {adv['scenarios']} scenarios completed.", "",
              "| Metric | Value |", "|---|---|",
              f"| Planted problems with a matching recommendation | {adv['coverage_pct']}% |",
              f"| Recommendations for problems that were not planted | {adv['extra_recommendations']} |",
              f"| Numbers in reports found in tool results | {adv['grounded_pct']}% of {adv['numbers']} |",
              f"| Mean latency per report | {adv['mean_latency_s']} s |",
              f"| Mean LLM calls per report | {adv['mean_llm_calls']} |",
              f"| Mean tokens in / out | {adv['mean_tokens_in']} / {adv['mean_tokens_out']} |",
              f"| Cost | {adv['total_cost_usd']} USD |", ""]
        for r in adv["results"]:
            g = r["grounding"]
            L.append(f"- scenario {r['seed']}: planted {len(r['planted'])}, recommended {len(r['recommended'])}, covered "
                     f"{r['covered']}/{r['wanted']}, extra {r['extra'] or '-'}, grounded {g.get('grounded')}/{g.get('numbers')}"
                     f"{', unverified ' + str(g.get('ungrounded')) if g.get('ungrounded') else ''}"
                     f"{', error ' + r['error'] if r['error'] else ''}")
    L += ["", "## How to read this", "",
          "- The detectors and the simulator were written by the same builder, so section 1 shows that the detection "
          "logic works and where it breaks (mild problems, missing data), not that it generalises to every company. "
          "A real log needs its own profile and a check of the thresholds.",
          "- For mechanical levers the estimate removes exactly the manual minutes the simulator removes, so a small "
          "error is expected by construction; the interesting rows are the ones where queues and calendars interact "
          "(approval escalation) and the combined effect.",
          "- Levers marked `assumed` use an assumed reduction rate (for example 50% fewer price discrepancies). The "
          "simulation applies the same assumption, so it only checks the arithmetic, not the assumption.",
          "- Money: hours x 1,100 RSD per hour (assumption), annualised from one quarter."]
    return "\n".join(L) + "\n"


def main(seeds: int = 40, use_llm: bool = False, llm_seeds: int = 6, out: str = "docs", est_seeds: int | None = None,
         llm_only: bool = False):
    est_seeds = est_seeds or max(5, seeds // 2)
    t0 = time.time()
    prev = Path(out, "eval_report.json")
    if llm_only and prev.exists():  # reuse detection/estimates, re-run only the AI advisor part
        old = json.loads(prev.read_text(encoding="utf-8"))
        det, est = old["detection"], old["estimates"]
        seeds, est_seeds = old.get("seeds", seeds), old.get("est_seeds", est_seeds)
        use_llm = True
    else:
        print(f"detection on {seeds} scenarios x 4 log types ...")
        det = detection(seeds)
        print(f"estimates vs simulation on {est_seeds} scenarios ...")
        est = estimates(est_seeds)
    adv = None
    if use_llm:
        if llm.provider() is None:
            print("no GEMINI_API_KEY / ANTHROPIC_API_KEY: skipping the AI advisor evaluation")
        else:
            print(f"AI advisor ({llm.provider()} {llm.model_name()}) on {llm_seeds} scenarios ...")
            adv = advisor_eval(llm_seeds)
    Path(out).mkdir(parents=True, exist_ok=True)
    md = report_md(det, est, adv, seeds, est_seeds)
    Path(out, "eval_report.md").write_text(md, encoding="utf-8")
    Path(out, "eval_report.json").write_text(json.dumps({"detection": det, "estimates": est, "advisor": adv,
                                                         "seeds": seeds, "est_seeds": est_seeds},
                                                        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(md)
    print(f"done in {time.time() - t0:.0f} s -> {out}/eval_report.md")
