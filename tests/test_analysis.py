import pandas as pd

from pmadvisor import analysis, discovery, findings
from pmadvisor.eval.runner import degrade
from pmadvisor.eventlog import check, load, normalise
from pmadvisor.profile import P2P
from pmadvisor.scenario import ISSUES, build
from pmadvisor.simulate import simulate


def test_demo_findings_match_planted_issues(demo_an, demo_scenario):
    got = {f.id for f in findings.detect(demo_an)}
    assert got == set(demo_scenario.issues) == set(ISSUES)


def test_healthy_company_has_no_findings(healthy_an):
    assert findings.detect(healthy_an) == []


def test_every_finding_has_numbers_and_a_lever(demo_an):
    for f in findings.detect(demo_an):
        assert f.evidence and f.levers
        assert any(ch.isdigit() for ch in f.summary)


def test_kpis_and_variants(demo_an):
    k = analysis.kpis(demo_an)
    assert k["cases"] > 1000 and 5 < k["cycle_days_median"] < 30
    v = analysis.variants(demo_an, 5)
    assert v[0]["share_pct"] >= v[-1]["share_pct"]
    assert abs(v[-1]["cumulative_pct"] - sum(x["share_pct"] for x in v)) < 0.5


def test_dfg_has_start_and_end(demo_an):
    g = analysis.dfg(demo_an)
    ids = {n["id"] for n in g["nodes"]}
    assert "▶ start" in ids and "■ end" in ids
    starts = sum(e["count"] for e in g["edges"] if e["source"] == "▶ start")
    assert starts == len(demo_an.cases)


def test_conformance_finds_retroactive_po(demo_an):
    c = discovery.conformance(demo_an)
    assert 0 < c["fitting_cases_pct"] < 100
    assert any("Create Purchase Order (retroactive)" in p["extra"] for p in c["deviation_patterns"])


def test_conformance_of_healthy_log_is_high(healthy_an):
    assert discovery.conformance(healthy_an)["fitting_cases_pct"] > 80


def test_missing_po_event_is_not_maverick_buying():
    df = simulate(build(9, []))
    po_rows = df[df["activity"] == "Create Purchase Order"].sample(frac=0.08, random_state=1).index
    an = analysis.enrich(normalise(df.drop(index=po_rows)), P2P)
    assert findings.detect_maverick(an) is None


def test_approval_bottleneck_survives_timestamp_jitter():
    s = build(3, ["approval_bottleneck"])
    an = analysis.enrich(degrade(simulate(s), "noisy", 3), P2P)
    f = findings.detect_approval_bottleneck(an)
    assert f is not None and f.evidence["resource"] == "Marko Petrović"


def test_log_without_start_times(tmp_path):
    df = simulate(build(4, ["manual_invoice_entry"])).drop(columns=["start"])
    p = tmp_path / "log.csv"
    df.rename(columns={"case_id": "CaseID", "activity": "Task", "complete": "EndTime"}).to_csv(p, index=False)
    log = load(p, {"CaseID": "case_id", "Task": "activity", "EndTime": "complete"})
    chk = check(log)
    assert chk.ok and any("start" in w for w in chk.warnings)
    an = analysis.enrich(log, P2P)
    assert "manual_invoice_entry" in {f.id for f in findings.detect(an)}


def test_check_reports_missing_columns():
    chk = check(pd.DataFrame({"case_id": ["a"], "activity": ["x"]}))
    assert not chk.ok
