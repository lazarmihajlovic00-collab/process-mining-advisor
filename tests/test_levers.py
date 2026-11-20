from pmadvisor import levers, whatif
from pmadvisor.scenario import build


def test_sef_integration_estimate_matches_simulation(demo_scenario, demo_an):
    c = whatif.compare(demo_scenario, "sef_erp_integration", demo_an)
    assert c["estimated"] > 500
    assert abs(c["error_pct"]) < 3


def test_daily_payment_run_captures_discounts(demo_scenario, demo_an):
    c = whatif.compare(demo_scenario, "daily_payment_run", demo_an)
    assert c["estimated"] > 0 and abs(c["error_pct"]) < 5
    assert c["cycle_days_mean_delta"] < -1  # payments no longer wait for Friday


def test_escalation_estimate_respects_working_hours(demo_scenario, demo_an):
    c = whatif.compare(demo_scenario, "approval_escalation", demo_an)
    assert abs(c["error_pct"]) < 10


def test_po_policy_is_a_control_not_a_saving(demo_an):
    e = levers.estimate(demo_an, "no_po_no_pay")
    assert e.hours_per_year < 0 and e.payback_months is None and e.confidence == "assumed"


def test_estimates_report_assumptions(demo_an):
    for e in levers.estimate_all(demo_an):
        assert e.assumptions, e.lever


def test_levers_do_not_apply_to_a_healthy_process(healthy_an):
    e = levers.estimate(healthy_an, "auto_three_way_match")
    assert e.hours_per_year == 0


def test_combined_simulation_is_not_more_than_the_sum():
    s = build(6, ["manual_invoice_entry", "manual_three_way_match", "price_discrepancy_rework"])
    base = whatif.analyse(s)
    ids = ["sef_erp_integration", "auto_three_way_match", "price_catalog_sync"]
    total = sum(levers.estimate(base, l).hours_per_year for l in ids)
    sim = -whatif.run(s, ids, base)["delta"]["manual_hours_per_year"]
    assert sim <= total * 1.01
