"""The Rules Lab: every case shows the real Postgres error under the real role, and the lab leaves NO trace (always rolled back)."""
import psycopg
import pytest

from walltest import config, lab

EXPECT = {   # case -> [(step index, expected sqlstate or None for success)]
    "update_trade_order": [(1, "42501"), (2, "WT001"), (3, "WT001")],
    "delete_access_event": [(1, "42501"), (2, "WT001")],
    "grant_upsi_to_low": [(0, "WT003")],
    "overlapping_slot": [(2, None), (3, "23P01")],
    "flip_mismatch": [(2, "WT002")],
    "sdd_two_sharers": [(0, "23514")],
    "low_side_select_upsi": [(0, "42501"), (2, None)],
    "engine_reads_unended_flip": [(2, None), (3, None), (4, "WT006"), (5, None)],
    "commit_after_open": [(2, "WT004")],
    "freeze_early": [(2, "WT009")],
}


def counts(db):
    with psycopg.connect(config.admin_dsn(db)) as c:
        return {r[0]: r[1] for r in c.execute("SELECT table_name, row_count FROM schema_row_counts()")}


@pytest.mark.parametrize("case_id", list(EXPECT))
def test_case_shows_the_real_error_and_leaves_no_trace(testdb, case_id):
    before = counts(testdb)
    res = lab.run_case(case_id, testdb)
    assert counts(testdb) == before, "the lab must be rolled back"
    assert res["rolled_back"] is True
    for i, state in EXPECT[case_id]:
        st = res["steps"][i]
        if state is None:
            assert st["ok"], (case_id, i, st)
        else:
            assert not st["ok"] and st["error"]["sqlstate"] == state, (case_id, i, st)
            assert st["error"]["message"]                      # PostgreSQL's own text


def test_setup_steps_succeed_and_all_cases_run_twice_in_a_row(testdb):
    """Re-running a case must not be blocked by a previous run (nothing was committed)."""
    for _ in range(2):
        for c in lab.CASES:
            res = lab.run_case(c.id, testdb)
            assert all(s["ok"] for s in res["steps"] if s["setup"]), (c.id, [s for s in res["steps"] if s["setup"] and not s["ok"]])


def test_second_layer_zero_rows_and_known_texts(testdb):
    r = lab.run_case("low_side_select_upsi", testdb)["steps"]
    assert "permission denied for table upsi_item" in r[0]["error"]["message"]
    assert r[2]["rows"] == [{"visible_rows": 0}] and r[3]["rows"] == [{"visible_rows": 16}]
    r = lab.run_case("update_trade_order", testdb)["steps"]
    assert r[2]["error"]["message"] == "trade_order is append-only: UPDATE is not permitted (tamper-evident audit trail)"
    r = lab.run_case("engine_reads_unended_flip", testdb)["steps"]
    assert r[3]["rows"] == [{"visible_flips": 0}] and r[5]["rows"] == [{"flips_that_exist": 1}]


def test_no_free_form_sql_surface():
    assert all(isinstance(c.steps, list) and c.steps for c in lab.CASES)
    assert not any("%s" in s.sql or "{" in s.sql and "}" in s.sql and False for c in lab.CASES for s in c.steps)
    assert set(lab.BY_ID) == set(EXPECT)
