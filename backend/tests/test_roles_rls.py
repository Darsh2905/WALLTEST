"""Row-level security and role privileges, exercised with the REAL roles (SET ROLE), never as the owner/superuser."""
import datetime as dt

import psycopg
import pytest
from conftest import as_role, expect_error, one

from walltest import config
from walltest.db import Database

UTC = dt.timezone.utc


# ----- the API login is not a superuser and not an owner ------------------------------------------------------------
def test_api_login_role_attributes(conn):
    r = conn.execute("SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolinherit, rolcanlogin FROM pg_roles WHERE rolname='walltest_api'").fetchone()
    assert r == (False, False, False, False, False, True)
    for role in ("high_side", "low_side", "audit_engine", "compliance", "walltest_owner"):
        a = conn.execute("SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        assert a == (False, False, False), role


def test_api_login_owns_nothing_and_cannot_become_the_owner(testdb):
    with psycopg.connect(config.api_dsn(testdb)) as c:
        assert c.execute("SELECT count(*) FROM pg_class WHERE relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user").fetchone()[0] == 0
        assert c.execute("SELECT NOT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]
        for target in ("walltest_owner", "walltest_admin", "postgres"):
            with pytest.raises(psycopg.Error) as ei:
                with c.transaction():
                    c.execute(f"SET ROLE {target}")
            assert ei.value.sqlstate in ("42501", "22023"), (target, ei.value)
        # NOINHERIT: with no SET ROLE the login has no table privileges at all
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.transaction():
                c.execute("SELECT * FROM upsi_item")


# ----- low_side cannot read upsi_item (privilege layer) --------------------------------------------------------------
@pytest.mark.parametrize("table", ["upsi_item", "canary_variant", "sealed_flip", "agent_note", "access_event", "trade_order", "audit_result", "access_grant"])
def test_low_side_has_no_table_access(conn, table):
    expect_error(conn, f"SELECT * FROM {table}", sqlstate="42501", role="low_side")


def test_low_side_select_upsi_item_error_text(conn):
    e = expect_error(conn, "SELECT * FROM upsi_item", sqlstate="42501", role="low_side")
    assert "permission denied for table upsi_item" in str(e)


async def test_low_side_cannot_read_upsi_item_through_the_api_path(testdb):
    """The API path: the pool logs in as walltest_api and runs each request under SET LOCAL ROLE."""
    db = Database(config.api_dsn(testdb), min_size=1, max_size=2)
    await db.open()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as ei:
            await db.fetch("low_side", "SELECT * FROM upsi_item")
        assert "permission denied for table upsi_item" in str(ei.value)
        rows = await db.fetch("high_side", "SELECT count(*) AS n FROM upsi_item")        # same pool, other role: allowed
        assert rows[0]["n"] == 16
        who = await db.fetch("low_side", "SELECT current_user AS u, session_user AS s")
        assert who[0]["u"] == "low_side" and who[0]["s"] == "walltest_api"
        with pytest.raises(ValueError):
            await db.fetch("walltest_owner", "SELECT 1")                                  # not an allowed request role
        # the role reverts at the end of each request: a pooled connection never leaks a role
        for _ in range(5):
            r = await db.fetch("compliance", "SELECT 1 AS x")
        async with db._pool.connection() as c:
            assert (await (await c.execute("SELECT current_user AS u")).fetchone())["u"] == "walltest_api"
    finally:
        await db.close()


# ----- upsi_item / canary_variant readable only by high_side and audit_engine ---------------------------------------
def test_who_can_read_upsi_item(conn):
    assert as_role(conn, "high_side", "SELECT count(*) FROM upsi_item") == [(16,)]
    assert as_role(conn, "audit_engine", "SELECT count(*) FROM upsi_item") == [(16,)]
    expect_error(conn, "SELECT * FROM upsi_item", sqlstate="42501", role="compliance")
    expect_error(conn, "SELECT * FROM upsi_item", sqlstate="42501", role="low_side")


def test_rls_is_a_second_layer_even_if_a_privilege_leaked(conn):
    """Grant SELECT to low_side (as the owner, in a rolled-back txn): the RLS policy still returns zero rows."""
    conn.execute("GRANT SELECT ON upsi_item, canary_variant, sealed_flip TO low_side")
    for t in ("upsi_item", "canary_variant", "sealed_flip"):
        assert as_role(conn, "low_side", f"SELECT count(*) FROM {t}") == [(0,)], t
    conn.execute("GRANT SELECT ON upsi_item TO compliance")
    assert as_role(conn, "compliance", "SELECT count(*) FROM upsi_item") == [(0,)]


def test_compliance_sdd_report_exposes_no_upsi_text(conn):
    cols = [d.name for d in conn.execute("SELECT * FROM v_sdd_report LIMIT 0").description]
    assert "summary" not in cols
    rows = as_role(conn, "compliance", "SELECT count(*) FROM v_sdd_report")
    assert rows == [(16,)]


def test_canary_variant_hidden_from_high_side_until_the_slot_opens(fx, conn):
    cid = fx.campaign(planned=2)
    past = fx.slot(cid, start=dt.datetime(2020, 6, 1, tzinfo=UTC))
    future = fx.slot(cid, start=dt.datetime.now(UTC) + dt.timedelta(days=3), committed_at=dt.datetime.now(UTC))
    n = as_role(conn, "high_side", "SELECT slot_id, count(*) FROM canary_variant GROUP BY slot_id")
    assert n == [(past["slot_id"], 2)]                       # no pre-exposure of the future slot's variants
    both = dict(as_role(conn, "audit_engine", "SELECT slot_id, count(*) FROM canary_variant GROUP BY slot_id"))
    assert both == {past["slot_id"]: 2, future["slot_id"]: 2}


# ----- sealed_flip: audit_engine only, only after the slot has ended -------------------------------------------------
def test_sealed_flip_invisible_until_the_slot_ends(fx, conn):
    cid = fx.campaign(planned=3)
    ended = fx.slot(cid, start=dt.datetime(2020, 6, 1, tzinfo=UTC))
    running = fx.slot(cid, start=dt.datetime.now(UTC) - dt.timedelta(seconds=30), length=3600, committed_at=dt.datetime.now(UTC) - dt.timedelta(minutes=5))
    future = fx.slot(cid, start=dt.datetime.now(UTC) + dt.timedelta(days=1), committed_at=dt.datetime.now(UTC))
    visible = {r[0] for r in as_role(conn, "audit_engine", "SELECT slot_id FROM sealed_flip")}
    assert visible == {ended["slot_id"]}                     # running and future slots stay sealed, even for the engine
    assert one(conn, "SELECT count(*) FROM sealed_flip") == 3  # the owner/superuser sees all three (bypasses RLS)
    assert as_role(conn, "audit_engine", "SELECT flip_bit FROM sealed_flip WHERE slot_id=%s", (ended["slot_id"],)) == [(ended["flip"],)]


def test_engine_reveal_refuses_a_sealed_flip(fx, conn):
    cid = fx.campaign()
    future = fx.slot(cid, start=dt.datetime.now(UTC) + dt.timedelta(days=1), committed_at=dt.datetime.now(UTC))
    e = expect_error(conn, "SELECT * FROM engine_reveal(%s)", (future["slot_id"],), sqlstate="WT006", role="audit_engine")
    assert "sealed until the slot has ended" in str(e)


def test_engine_reveal_after_the_slot_ends_verifies_the_commitment(fx, conn):
    cid = fx.campaign()
    s = fx.slot(cid, start=dt.datetime(2020, 6, 1, tzinfo=UTC))
    rows = as_role(conn, "audit_engine", "SELECT flip_bit, salt_hex, commitment_ok FROM engine_reveal(%s)", (s["slot_id"],))
    assert rows == [(s["flip"], s["salt"].hex(), True)]


@pytest.mark.parametrize("role", ["high_side", "low_side", "compliance"])
def test_nobody_else_can_read_sealed_flip(fx, conn, role):
    cid = fx.campaign()
    fx.slot(cid, start=dt.datetime(2020, 6, 1, tzinfo=UTC))
    expect_error(conn, "SELECT * FROM sealed_flip", sqlstate="42501", role=role)


def test_only_the_engine_may_insert_slots_and_flips(fx, conn):
    cid = fx.campaign()
    for role in ("compliance", "high_side", "low_side"):
        expect_error(conn, "SELECT engine_commit_slot(%s,%s,1,tstzrange('2020-06-01','2020-06-01 00:00:02','[)'),%s,'2020-05-31',0::smallint,%s,1::smallint,'p','n')",
                     (cid, fx.treatments(cid)[0], "0" * 64, b"s" * 32), sqlstate="42501", role=role)
    fx.slot(cid, role="audit_engine")                         # the engine can


def test_gateway_and_stats_functions_not_executable_by_public(conn):
    for fn, role in (("wt_gate(1,'x','READ',NULL,NULL)", "low_side"), ("wt_context(1)", "low_side"),
                     ("gw_read_canary()", "low_side"), ("gw_place_orders(ARRAY['x'],ARRAY['BUY'],1)", "high_side"),
                     ("wt_impl_place_orders(1,ARRAY['x'],ARRAY['BUY'],1)", "low_side"), ("wt_impl_read_canary(1)", "high_side"),
                     ("wt_caller_agent()", "low_side"), ("evidence_leaves(1)", "low_side"), ("log_evalue_mix(10,5)", "low_side"),
                     ("binom_upper_p(10,5)", "low_side")):
        expect_error(conn, f"SELECT * FROM {fn}", sqlstate="42501", role=role)
