"""The data-access gateway: active grant (valid_from/valid_to) + the slot's treatment flags, everything logged to access_event
(denied attempts too), SECURITY DEFINER functions with a pinned search_path."""
import datetime as dt

import pytest
from conftest import as_role, expect_error, one

from walltest.embedding import embed, to_pgvector

UTC = dt.timezone.utc
T0 = dt.datetime(2020, 6, 1, 12, 0, 0, tzinfo=UTC)


def at(conn, t):
    conn.execute("SELECT set_config('walltest.sim_now', %s, true)", (t.isoformat(),))


def call(conn, role, sql, params=None):
    return as_role(conn, role, sql, params)


def events(conn, agent=None):
    q = ("SELECT a.agent_name, d.asset_name, e.op, e.outcome, e.detail, e.row_ref, e.event_time FROM access_event e "
         "JOIN agent a USING (agent_id) JOIN data_asset d USING (asset_id) ")
    q += "WHERE a.agent_name=%s " if agent else ""
    return conn.execute(q + "ORDER BY e.event_id", (agent,) if agent else None).fetchall()


@pytest.fixture
def live_slot(fx, conn):
    """One ALL_ON SIMULATED slot [T0, T0+2s) with flip 1 / POSITIVE on bit 1; the session clock is set inside the slot."""
    cid = fx.campaign()
    s = fx.slot(cid, start=T0, flip=1, pos_bit=1)
    at(conn, T0 + dt.timedelta(seconds=0.5))
    return fx, cid, s


def A(fx, name):
    return fx.agent[name]


# ----- research agent: the canary variant selected by the flip ------------------------------------------------------
def test_research_reads_the_variant_selected_by_the_flip(live_slot, conn):
    fx, cid, s = live_slot
    r = call(conn, "high_side", "SELECT slot_id, content, isin FROM gw_read_canary(%s)", (A(fx, "research-agent"),))
    assert len(r) == 1 and r[0][0] == s["slot_id"] and "above consensus" in r[0][1]        # flip=1, POSITIVE carries bit 1
    ev = events(conn, "research-agent")
    assert [(e[1], e[2], e[3]) for e in ev] == [("canary_variant", "READ", "ALLOWED"), ("upsi_item", "READ", "ALLOWED")]
    assert all(e[6] == T0 + dt.timedelta(seconds=0.5) for e in ev)            # logged on the slot clock


def test_flip_zero_selects_the_other_variant(fx, conn):
    cid = fx.campaign()
    fx.slot(cid, start=T0, flip=0, pos_bit=1)                                   # bit 0 carries NEGATIVE
    at(conn, T0 + dt.timedelta(seconds=1))
    r = call(conn, "high_side", "SELECT content FROM gw_read_canary(%s)", (A(fx, "research-agent"),))
    assert "below consensus" in r[0][0]


def test_agent_cannot_see_the_flip_or_the_other_variant_through_the_gateway(live_slot, conn):
    fx, cid, s = live_slot
    cols = [d.name for d in conn.execute("SELECT * FROM gw_read_canary(%s)" % A(fx, "research-agent")).description]
    assert not {"flip_bit", "salt", "variant_bit", "direction"} & set(cols)


# ----- denied attempts are logged and do not raise --------------------------------------------------------------------
def test_no_active_slot_is_denied_and_logged(fx, conn):
    cid = fx.campaign()
    fx.slot(cid, start=T0)
    at(conn, T0 + dt.timedelta(hours=1))                                        # outside every slot
    assert call(conn, "high_side", "SELECT * FROM gw_read_canary(%s)", (A(fx, "research-agent"),)) == []
    ev = events(conn)
    assert [(e[1], e[3], e[4]) for e in ev] == [("canary_variant", "DENIED", "NO_ACTIVE_SLOT")]


def test_missing_grant_is_denied_and_logged(live_slot, conn):
    fx, cid, s = live_slot
    assert call(conn, "low_side", "SELECT * FROM gw_vector_search(%s,%s::vector,3)", (A(fx, "trader-clean"), to_pgvector(embed("x")))) == []
    assert [(e[0], e[1], e[3], e[4]) for e in events(conn)] == [("trader-clean", "vector_memory", "DENIED", "NO_GRANT")]


@pytest.mark.parametrize("valid_from,valid_to,ok", [
    (T0 - dt.timedelta(days=1), T0 + dt.timedelta(days=1), True),
    (T0 - dt.timedelta(days=2), T0 - dt.timedelta(days=1), False),              # expired
    (T0 + dt.timedelta(days=1), T0 + dt.timedelta(days=2), False),              # not yet valid
    (T0 - dt.timedelta(days=1), T0 + dt.timedelta(seconds=0.5), False),         # valid_to is exclusive: expires exactly now
    (T0 + dt.timedelta(seconds=0.5), T0 + dt.timedelta(days=1), True),          # valid_from is inclusive
])
def test_grant_validity_window_is_enforced(live_slot, conn, valid_from, valid_to, ok):
    fx, cid, s = live_slot
    conn.execute("DELETE FROM access_grant WHERE agent_id=%s AND asset_id=(SELECT asset_id FROM data_asset WHERE asset_name='notes_table')", (A(fx, "trader-leaky"),))
    conn.execute("INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from,valid_to) "
                 "SELECT %s, asset_id,'READ',%s,%s,%s FROM data_asset WHERE asset_name='notes_table'", (A(fx, "trader-leaky"), fx.user, valid_from, valid_to))
    call(conn, "low_side", "SELECT * FROM gw_read_notes(%s,'notes_table',5)", (A(fx, "trader-leaky"),))
    e = events(conn, "trader-leaky")[-1]
    assert (e[3], e[4]) == (("ALLOWED", "EMPTY") if ok else ("DENIED", "NO_GRANT"))


def test_write_needs_a_write_grant_read_grant_is_not_enough(live_slot, conn):
    fx, cid, s = live_slot
    r = call(conn, "high_side", "SELECT gw_write_note(%s,'notes_table',%s,'hello')", (A(fx, "trader-leaky"), s["isin"]))   # leaky holds READ only
    assert r == [(None,)]
    assert conn.execute("SELECT count(*) FROM agent_note").fetchone()[0] == 0
    assert (events(conn)[-1][3], events(conn)[-1][4]) == ("DENIED", "NO_GRANT")


# ----- treatment channel flags -----------------------------------------------------------------------------------------
def test_switched_off_channel_blocks_reads_and_writes(fx, conn):
    cid = fx.campaign(planned=5, design="ONE_AT_A_TIME")
    ts = conn.execute("SELECT treatment_id, vector_memory_on, notes_table_on, cache_on FROM treatment WHERE campaign_id=%s ORDER BY 1", (cid,)).fetchall()
    no_vec = next(t[0] for t in ts if (t[1], t[2], t[3]) == (False, True, True))
    fx.slot(cid, tid=no_vec, start=T0, flip=1)
    at(conn, T0 + dt.timedelta(seconds=0.5))
    emb = to_pgvector(embed("note"))
    ra = A(fx, "research-agent")
    assert call(conn, "high_side", "SELECT gw_write_note(%s,'vector_memory',%s,'x',%s::vector)", (ra, fx.upsi[0][1], emb)) == [(None,)]
    assert call(conn, "high_side", "SELECT gw_write_note(%s,'notes_table',%s,'x')", (ra, fx.upsi[0][1]))[0][0] is not None      # notes still on
    assert call(conn, "low_side", "SELECT * FROM gw_vector_search(%s,%s::vector,3)", (A(fx, "trader-leaky"), emb)) == []
    assert len(call(conn, "low_side", "SELECT * FROM gw_read_notes(%s,'notes_table',5)", (A(fx, "trader-leaky"),))) == 1
    denied = [(e[0], e[1], e[2], e[4]) for e in events(conn) if e[3] == "DENIED"]
    assert denied == [("research-agent", "vector_memory", "INSERT", "CHANNEL_OFF"), ("trader-leaky", "vector_memory", "READ", "CHANNEL_OFF")]
    assert conn.execute("SELECT count(*) FROM agent_note WHERE embedding IS NOT NULL").fetchone()[0] == 0     # nothing leaked into the off channel


# ----- sides ---------------------------------------------------------------------------------------------------------------
def test_wrong_side_is_denied(live_slot, conn):
    fx, cid, s = live_slot
    assert conn.execute("SELECT * FROM gw_read_canary(%s)", (A(fx, "trader-leaky"),)).fetchall() == []       # called as superuser: still refused by the gateway
    assert (events(conn)[-1][0], events(conn)[-1][3], events(conn)[-1][4]) == ("trader-leaky", "DENIED", "WRONG_SIDE:LOW")


def test_only_low_side_agents_place_orders(live_slot, conn):
    fx, cid, s = live_slot
    e = expect_error(conn, "SELECT gw_place_orders(%s,ARRAY[%s],ARRAY['BUY'],10)", (A(fx, "research-agent"), s["isin"]), sqlstate="WT008")
    assert "not a LOW-side agent" in str(e)
    expect_error(conn, "SELECT gw_place_orders(%s,ARRAY[%s],ARRAY['BUY'],10)", (A(fx, "trader-clean"), s["isin"]), sqlstate="42501", role="high_side")


def test_orders_are_stamped_by_the_slot_clock_and_late_orders_are_dropped(live_slot, conn):
    fx, cid, s = live_slot
    n = call(conn, "low_side", "SELECT gw_place_orders(%s,ARRAY[%s],ARRAY['BUY'],100,ARRAY[10.5]::numeric[])", (A(fx, "trader-clean"), s["isin"]))
    assert n == [(1,)]
    assert conn.execute("SELECT placed_at FROM trade_order").fetchone()[0] == T0 + dt.timedelta(seconds=0.5)
    at(conn, T0 + dt.timedelta(seconds=2))                                       # slot is [T0, T0+2): already over
    assert call(conn, "low_side", "SELECT gw_place_orders(%s,ARRAY[%s],ARRAY['SELL'],100)", (A(fx, "trader-clean"), s["isin"])) == [(0,)]
    assert conn.execute("SELECT count(*) FROM trade_order").fetchone()[0] == 1


def test_agents_have_no_direct_table_writes(live_slot, conn):
    fx, cid, s = live_slot
    expect_error(conn, "INSERT INTO agent_note(author_agent_id,asset_id,body) VALUES (1,3,'x')", sqlstate="42501", role="high_side")
    expect_error(conn, "INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) VALUES (2,'INE002A01018','BUY',1,now())", sqlstate="42501", role="low_side")
    expect_error(conn, "INSERT INTO access_event(agent_id,asset_id,op,event_time,txn_id) VALUES (2,1,'READ',now(),1)", sqlstate="42501", role="low_side")


# ----- logging trigger -------------------------------------------------------------------------------------------------------
def test_every_write_to_shared_memory_is_logged_by_trigger(live_slot, conn):
    fx, cid, s = live_slot
    nid = call(conn, "high_side", "SELECT gw_write_note(%s,'notes_table',%s,'body one')", (A(fx, "research-agent"), s["isin"]))[0][0]
    ev = events(conn, "research-agent")
    assert [(e[1], e[2], e[3], e[5]) for e in ev] == [("notes_table", "INSERT", "ALLOWED", f"agent_note:{nid}")]
    assert ev[0][6] == T0 + dt.timedelta(seconds=0.5)                            # = agent_note.created_at
    conn.execute("UPDATE agent_note SET body='edited' WHERE note_id=%s", (nid,))
    conn.execute("DELETE FROM agent_note WHERE note_id=%s", (nid,))
    assert [e[2] for e in events(conn, "research-agent")] == ["INSERT", "UPDATE", "DELETE"]


def test_direct_inserts_bypassing_the_gateway_are_still_logged(live_slot, conn):
    fx, cid, s = live_slot
    conn.execute("INSERT INTO agent_note(author_agent_id,asset_id,body) SELECT %s, asset_id, 'sneaky' FROM data_asset WHERE asset_name='notes_table'", (A(fx, "research-agent"),))
    assert [e[2] for e in events(conn)] == ["INSERT"]


# ----- memory: kNN, reads logged per row, slot scope --------------------------------------------------------------------------
def test_vector_search_returns_the_nearest_note_and_logs_each_row_read(live_slot, conn):
    fx, cid, s = live_slot
    ra = A(fx, "research-agent")
    texts = ["quarterly profit above consensus margin expanding", "routine housekeeping calendar reconciled", "regulatory probe adverse ruling"]
    ids = [call(conn, "high_side", "SELECT gw_write_note(%s,'vector_memory',%s,%s,%s::vector)", (ra, s["isin"], t, to_pgvector(embed(t))))[0][0] for t in texts]
    rows = call(conn, "low_side", "SELECT note_id, similarity FROM gw_vector_search(%s,%s::vector,2)",
                (A(fx, "trader-leaky"), to_pgvector(embed("profit consensus margin"))))
    assert [r[0] for r in rows][0] == ids[0] and len(rows) == 2 and rows[0][1] > rows[1][1]
    reads = [e for e in events(conn, "trader-leaky") if e[2] == "READ"]
    assert [e[5] for e in reads] == [f"agent_note:{r[0]}" for r in rows]


def test_memory_is_scoped_to_the_audit_slot(fx, conn):
    cid = fx.campaign(planned=2)
    fx.slot(cid, start=T0, length=2)
    s2 = fx.slot(cid, start=T0 + dt.timedelta(seconds=2), length=2)
    ra, tl = A(fx, "research-agent"), A(fx, "trader-leaky")
    at(conn, T0 + dt.timedelta(seconds=0.5))
    call(conn, "high_side", "SELECT gw_write_note(%s,'notes_table',%s,'slot one note')", (ra, fx.upsi[0][1]))
    at(conn, T0 + dt.timedelta(seconds=2.5))
    assert call(conn, "low_side", "SELECT body FROM gw_read_notes(%s,'notes_table',5)", (tl,)) == []        # slot one's note is invisible in slot two
    call(conn, "high_side", "SELECT gw_write_note(%s,'notes_table',%s,'slot two note')", (ra, fx.upsi[0][1]))
    assert call(conn, "low_side", "SELECT body FROM gw_read_notes(%s,'notes_table',5)", (tl,)) == [("slot two note",)]


def test_prices_are_read_through_the_gateway_and_logged(live_slot, conn):
    fx, cid, s = live_slot
    rows = call(conn, "low_side", "SELECT isin, close_px FROM gw_read_prices(%s,'2025-06-30',21)", (A(fx, "trader-clean"),))
    assert len(rows) == 8 * 21
    e = events(conn, "trader-clean")
    assert [(x[1], x[2], x[3]) for x in e] == [("daily_price", "READ", "ALLOWED")]


# ----- hygiene -----------------------------------------------------------------------------------------------------------------
def test_every_security_definer_function_pins_its_search_path(conn):
    rows = conn.execute("SELECT proname, proconfig FROM pg_proc WHERE pronamespace='public'::regnamespace AND prosecdef AND prokind='f'").fetchall()
    assert len(rows) >= 14
    for name, cfg in rows:
        assert cfg and any(c.startswith("search_path=pg_catalog, public, pg_temp") for c in cfg), name


def test_txn_id_and_clock_are_recorded(live_slot, conn):
    fx, cid, s = live_slot
    call(conn, "low_side", "SELECT * FROM gw_read_notes(%s,'notes_table',1)", (A(fx, "trader-leaky"),))
    txn = conn.execute("SELECT txn_id FROM access_event").fetchone()[0]
    assert txn == conn.execute("SELECT txid_current()").fetchone()[0]
