from pmadvisor import worktime
from pmadvisor.scenario import build, demo
from pmadvisor.simulate import simulate
from pmadvisor.whatif import apply
from pmadvisor.worktime import DAY


def test_working_calendar_skips_weekend_and_nights():
    fri_1530 = 4 * DAY + 15 * 60 + 30
    # 60 minutes of work starting Friday 15:30 ends Monday 08:30
    assert worktime.add_open(fri_1530, 60) == 7 * DAY + 8 * 60 + 30
    assert worktime.next_open(5 * DAY) == 7 * DAY + 480  # Saturday -> Monday 08:00


def test_simulation_is_deterministic():
    a = simulate(build(11, ["po_change_rework"]))
    b = simulate(build(11, ["po_change_rework"]))
    assert a.equals(b)


def test_lever_keeps_the_same_cases():
    s = demo()
    base = simulate(s).drop_duplicates("case_id").set_index("case_id")
    after = simulate(apply(s, ["sef_erp_integration", "daily_payment_run"])).drop_duplicates("case_id").set_index("case_id")
    assert list(base.index) == list(after.index)
    assert (base["amount_rsd"] == after["amount_rsd"]).all()
    assert (base["vendor"] == after["vendor"]).all()


def test_weekly_payment_run_pays_only_on_friday():
    df = simulate(build(5, ["batched_payment_run"]))
    pays = df[df["activity"] == "Pay Invoice"]
    assert set(pays["complete"].dt.dayofweek) == {4}


def test_automatic_matching_is_done_by_the_system():
    df = simulate(build(5, []))
    assert set(df.loc[df["activity"] == "Three-way Match", "resource"]) == {"Matching engine"}
    df = simulate(build(5, ["manual_three_way_match"]))
    assert "Matching engine" not in set(df.loc[df["activity"] == "Three-way Match", "resource"])


def test_people_do_not_split_short_tasks_overnight():
    df = simulate(demo())
    approvals = df[df["activity"] == "Approve Invoice"]
    assert ((approvals["complete"] - approvals["start"]).dt.total_seconds() < 3600).all()
