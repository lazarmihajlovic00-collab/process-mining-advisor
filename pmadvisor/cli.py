"""Command line: pma simulate | analyze | advise | whatif | eval | serve | bpmn"""

from __future__ import annotations

import argparse
import json
import sys



def _ws(args):
    from .profile import P2P, Profile
    from .scenario import build, demo
    from .service import Workspace
    from .store import Store

    ws = Workspace(Store(getattr(args, "db", ":memory:") or ":memory:"),
                   Profile.load(args.profile) if getattr(args, "profile", None) else P2P)
    if getattr(args, "log", None):
        ws.load_file(args.log)
    elif getattr(args, "issues", None) is not None:
        ws.load_scenario(build(args.seed, [i for i in args.issues.split(",") if i]))
    else:
        ws.load_scenario(demo())
    return ws


def _rsd(x):
    return "-" if x is None else f"{x:,.0f}".replace(",", ".")


def cmd_simulate(args):
    from .scenario import build, demo
    from .simulate import simulate

    s = demo() if args.issues is None else build(args.seed, [i for i in args.issues.split(",") if i])
    df = simulate(s)
    df.to_csv(args.out, index=False)
    print(f"{len(df)} events, {df['case_id'].nunique()} cases -> {args.out}")
    print("planted issues:", ", ".join(s.issues) or "none")


def cmd_analyze(args):
    from .eventlog import check

    ws = _ws(args)
    run = ws.run()
    if run.source == "file":
        print("log check:", json.dumps(check(run.an.df).__dict__, ensure_ascii=False))
    k = ws.kpis()
    print(f"\n{run.label}: {k['cases']} cases, {k['events']} events, {k['variants']} variants, period {k['period']}")
    print(f"cycle time median {k['cycle_days_median']} d, p90 {k['cycle_days_p90']} d; manual work "
          f"{k['manual_hours_per_year']} h/year ({k['manual_fte']} FTE)")
    c = ws.conformance()
    print(f"conformance: {c['fitting_cases_pct']}% of cases follow the normative process (fitness {c['log_fitness']})")
    print("\nFINDINGS")
    for f in ws.findings():
        print(f"  [{f['severity']}] {f['title']}: {f['summary']}")
    print("\nLEVERS (estimated from the log)")
    print(f"  {'lever':24s} {'h/year':>7s} {'net RSD/year':>14s} {'payback':>8s}  confidence")
    for e in sorted(ws.estimates(), key=lambda e: -e["net_rsd_per_year"]):
        if e["applicable"]:
            print(f"  {e['lever']:24s} {e['hours_per_year']:>7.0f} {_rsd(e['net_rsd_per_year']):>14s} "
                  f"{(str(e['payback_months']) + ' m') if e['payback_months'] else '-':>8s}  {e['confidence']}")


def cmd_advise(args):
    from . import llm
    from .advisor import runner

    ws = _ws(args)
    print(f"advisor: {llm.provider() or 'template (no API key)'} {llm.model_name() or ''}\n")
    res = runner.advise(ws, "cli", args.question)
    print(res.text)
    print("\nPENDING recommendations (a person must accept or reject):")
    for r in ws.recommendations():
        print(f"  #{r['id']} P{r['priority']} {r['title']}  net {_rsd(r['estimate']['net_rsd_per_year'])} RSD/year  [{r['status']}]")
    if res.source != "template":
        g = res.grounding
        print(f"\ngrounding: {g['grounded']}/{g['numbers']} numbers found in tool results; unverified: {g['ungrounded']}")
        print(f"LLM calls {res.llm_calls}, tokens in/out {res.input_tokens}/{res.output_tokens}, {res.latency_ms / 1000:.1f} s")
    if res.error:
        print("error:", res.error)


def cmd_whatif(args):
    ws = _ws(args)
    levers = [l for l in args.levers.split(",") if l]
    w = ws.whatif(levers)
    print(f"{'metric':34s} {'before':>12s} {'after':>12s}")
    for k in w["before"]:
        print(f"{k:34s} {str(w['before'][k]):>12s} {str(w['after'][k]):>12s}")
    print("\nsum of log estimates:", json.dumps(ws.package(levers)["total"], ensure_ascii=False))


def cmd_bpmn(args):
    from . import discovery

    ws = _ws(args)
    print(discovery.bpmn_xml(ws.run().an, args.out))


def cmd_eval(args):
    from .eval import runner

    runner.main(seeds=args.seeds, use_llm=args.llm, llm_seeds=args.llm_seeds, out=args.out)


def cmd_serve(args):
    import uvicorn

    uvicorn.run("pmadvisor.api:create_app", factory=True, host=args.host, port=args.port)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="pma", description="Process-mining automation advisor")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--log", help="CSV or XES event log (default: simulated demo company)")
        sp.add_argument("--profile", help="profile JSON mapping the log's activity names")
        sp.add_argument("--seed", type=int, default=7)
        sp.add_argument("--issues", default=None, help="comma-separated planted issues for a simulated log")

    s = sub.add_parser("simulate", help="write a synthetic P2P event log")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--issues", default=None)
    s.add_argument("--out", default="data/p2p_event_log.csv")
    s.set_defaults(fn=cmd_simulate)
    a = sub.add_parser("analyze", help="KPIs, conformance, findings, savings")
    common(a)
    a.set_defaults(fn=cmd_analyze)
    ad = sub.add_parser("advise", help="AI advisor report + PENDING recommendations")
    common(ad)
    ad.add_argument("question", nargs="?", default=None)
    ad.set_defaults(fn=cmd_advise)
    w = sub.add_parser("whatif", help="re-simulate with levers")
    common(w)
    w.add_argument("--levers", required=True)
    w.set_defaults(fn=cmd_whatif)
    b = sub.add_parser("bpmn", help="export the discovered model as BPMN")
    common(b)
    b.add_argument("--out", default="data/discovered_model.bpmn")
    b.set_defaults(fn=cmd_bpmn)
    e = sub.add_parser("eval", help="evaluation report -> docs/eval_report.md")
    e.add_argument("--seeds", type=int, default=40)
    e.add_argument("--llm", action="store_true", help="also evaluate the AI advisor (needs GEMINI_API_KEY or ANTHROPIC_API_KEY)")
    e.add_argument("--llm-seeds", type=int, default=6)
    e.add_argument("--out", default="docs")
    e.set_defaults(fn=cmd_eval)
    sv = sub.add_parser("serve", help="dashboard + API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    args.fn(args)



if __name__ == "__main__":
    main()
