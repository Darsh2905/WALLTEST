"""The data-access gateway: active grant (valid_from/valid_to) + the slot's treatment flags, everything logged to access_event
(denied attempts too), SECURITY DEFINER functions with a pinned search_path.

v2 (migration 015): agents authenticate by their OWN database role. Gateway functions take no agent id; every test acts as the
agent's role (wt_agent_<name>), and the impersonation tests at the end attack exactly that."""
import datetime as dt

import psycopg
import pytest
from conftest import as_role, expect_error, one

from walltest import roles
from walltest.embedding import embed, to_pgvector
from walltest.roles import role_name

UTC = dt.timezone.utc
T0 = dt.datetime(2020, 6, 1, 12, 0, 0, tzinfo=UTC)


def at(conn, t):
    conn.execute("SELECT set_config('walltest.sim_now', %s, true)", (t.isoformat(),))


def call(conn, agent, sql, params=None):
    """Run `sql` as the agent's own database role."""
    return as_role(conn, role_name(agent), sql, params)


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
    r = call(conn, "research-agent", "SELECT slot_id, content, isin FROM gw_read_canary()")
    assert len(r) == 1 and r[0][0] == s["slot_id"] and "above consensus" in r[0][1]        # flip=1, POSITIVE carries bit 1
    ev = events(conn, "research-agent")
    assert [(e[1], e[2], e[3]) for e in ev] == [("canary_variant", "READ", "ALLOWED"), ("upsi_item", "READ", "ALLOWED")]
    assert all(e[6] == T0 + dt.timedelta(seconds=0.5) for e in ev)            # logged on the slot clock


def test_flip_zero_selects_the_other_variant(fx, conn):
    cid = fx.campaign()
    fx.slot(cid, start=T0, flip=0, pos_bit=1)                                   # bit 0 carries NEGATIVE
    at(conn, T0 + dt.timedelta(seconds=1))
    r = call(conn, "research-agent", "SELECT content FROM gw_read_canary()")
    assert "below consensus" in r[0][0]


def test_agent_cannot_see_the_flip_or_the_other_variant_through_the_gateway(live_slot, conn):
    with conn.transaction():
        conn.execute(f"SET LOCAL ROLE {role_name('research-agent')}")
        cols = [d.name for d in conn.execute("SELECT * FROM gw_read_canary()").description]
        conn.execute("RESET ROLE")
    assert not {"flip_bit", "salt", "variant_bit", "direction"} & set(cols)


# ----- denied attempts are logged and do not raise --------------------------------------------------------------------
def test_no_active_slot_is_denied_and_logged(fx, conn):
    cid = fx.campaign()
    fx.slot(cid, start=T0)
    at(conn, T0 + dt.timedelta(hours=1))                                        # outside every slot
    assert call(conn, "research-agent", "SELECT * FROM gw_read_canary()") == []
    ev = events(conn)
    assert [(e[1], e[3], e[4]) for e in ev] == [("canary_variant", "DENIED", "NO_ACTIVE_SLOT")]


def test_missing_grant_is_denied_and_logged(live_slot, conn):
    assert call(conn, "trader-clean", "SELECT * FROM gw_vector_search(%s::vector,3)", (to_pgvector(embed("x")),)) == []
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
    call(conn, "trader-leaky", "SELECT * FROM gw_read_notes('notes_table',5)")
    e = events(conn, "trader-leaky")[-1]
    assert (e[3], e[4]) == (("ALLOWED", "EMPTY") if ok else ("DENIED", "NO_GRANT"))


def test_write_needs_a_write_grant_read_grant_is_not_enough(live_slot, conn):
    fx, cid, s = live_slot
    ra = A(fx, "research-agent")
    conn.execute("DELETE FROM access_grant WHERE agent_id=%s AND asset_id=(SELECT asset_id FROM data_asset WHERE asset_name='notes_table')", (ra,))
    conn.execute("INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from) "
                 "SELECT %s, asset_id,'READ',%s,'2020-01-01' FROM data_asset WHERE asset_name='notes_table'", (ra, fx.user))
    r = call(conn, "research-agent", "SELECT gw_write_note('notes_table',%s,'hello')", (s["isin"],))       # holds READ only now
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
    assert call(conn, "research-agent", "SELECT gw_write_note('vector_memory',%s,'x',%s::vector)", (fx.upsi[0][1], emb)) == [(None,)]
    assert call(conn, "research-agent", "SELECT gw_write_note('notes_table',%s,'x')", (fx.upsi[0][1],))[0][0] is not None      # notes still on
    assert call(conn, "trader-leaky", "SELECT * FROM gw_vector_search(%s::vector,3)", (emb,)) == []
    assert len(call(conn, "trader-leaky", "SELECT * FROM gw_read_notes('notes_table',5)")) == 1
    denied = [(e[0], e[1], e[2], e[4]) for e in events(conn) if e[3] == "DENIED"]
    assert denied == [("research-agent", "vector_memory", "INSERT", "CHANNEL_OFF"), ("trader-leaky", "vector_memory", "READ", "CHANNEL_OFF")]
    assert conn.execute("SELECT count(*) FROM agent_note WHERE embedding IS NOT NULL").fetchone()[0] == 0     # nothing leaked into the off channel


# ----- sides ---------------------------------------------------------------------------------------------------------------
def test_wrong_side_is_refused_by_privileges_and_by_the_gateway(live_slot, conn):
    fx, cid, s = live_slot
    expect_error(conn, "SELECT * FROM gw_read_canary()", sqlstate="42501", role=role_name("trader-leaky"))      # LOW role: no EXECUTE
    # defence in depth: the implementation still checks the side even when reached by a privileged caller
    assert conn.execute("SELECT * FROM wt_impl_read_canary(%s)", (A(fx, "trader-leaky"),)).fetchall() == []
    assert (events(conn)[-1][0], events(conn)[-1][3], events(conn)[-1][4]) == ("trader-leaky", "DENIED", "WRONG_SIDE:LOW")


def test_only_low_side_agents_place_orders(live_slot, conn):
    fx, cid, s = live_slot
    expect_error(conn, "SELECT gw_place_orders(ARRAY[%s],ARRAY['BUY'],10)", (s["isin"],), sqlstate="42501", role=role_name("research-agent"))
    e = expect_error(conn, "SELECT wt_impl_place_orders(%s,ARRAY[%s],ARRAY['BUY'],10)", (A(fx, "research-agent"), s["isin"]), sqlstate="WT008")
    assert "not a LOW-side agent" in str(e)


def test_orders_are_stamped_by_the_slot_clock_and_late_orders_are_dropped(live_slot, conn):
    fx, cid, s = live_slot
    n = call(conn, "trader-clean", "SELECT gw_place_orders(ARRAY[%s],ARRAY['BUY'],100,ARRAY[10.5]::numeric[])", (s["isin"],))
    assert n == [(1,)]
    assert conn.execute("SELECT placed_at, agent_id FROM trade_order").fetchone() == (T0 + dt.timedelta(seconds=0.5), A(fx, "trader-clean"))
    at(conn, T0 + dt.timedelta(seconds=2))                                       # slot is [T0, T0+2): already over
    assert call(conn, "trader-clean", "SELECT gw_place_orders(ARRAY[%s],ARRAY['SELL'],100)", (s["isin"],)) == [(0,)]
    assert conn.execute("SELECT count(*) FROM trade_order").fetchone()[0] == 1


def test_single_order_wrapper(live_slot, conn):
    fx, cid, s = live_slot
    assert call(conn, "trader-leaky", "SELECT gw_place_order(%s,'SELL',5)", (s["isin"],)) == [(1,)]
    assert conn.execute("SELECT agent_id, side, quantity FROM trade_order").fetchone() == (A(fx, "trader-leaky"), "SELL", 5)


def test_agents_have_no_direct_table_writes(live_slot, conn):
    expect_error(conn, "INSERT INTO agent_note(author_agent_id,asset_id,body) VALUES (1,3,'x')", sqlstate="42501", role=role_name("research-agent"))
    expect_error(conn, "INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) VALUES (2,'INE002A01018','BUY',1,now())", sqlstate="42501", role=role_name("trader-leaky"))
    expect_error(conn, "INSERT INTO access_event(agent_id,asset_id,op,event_time,txn_id) VALUES (2,1,'READ',now(),1)", sqlstate="42501", role=role_name("trader-leaky"))


# ----- impersonation (the v1 hole that per-agent roles close) ---------------------------------------------------------------------
def test_an_agent_cannot_act_as_another_agent(live_slot, conn):
    fx, cid, s = live_slot
    leaky = role_name("trader-leaky")
    # v1 attack: pass someone else's agent id. The id-taking implementations are no longer executable by any agent role.
    expect_error(conn, "SELECT wt_impl_place_orders(%s,ARRAY[%s],ARRAY['BUY'],10)", (A(fx, "trader-clean"), s["isin"]), sqlstate="42501", role=leaky)
    expect_error(conn, "SELECT * FROM wt_impl_read_notes(%s,'notes_table',5)", (A(fx, "trader-clean"),), sqlstate="42501", role=leaky)
    # (switching roles is tested with a REAL agent login below: this harness is a superuser session, which may SET any role)
    # a custom GUC cannot stand in for the role either: the gateway reads the real `role` setting
    with conn.transaction():
        conn.execute(f"SET LOCAL ROLE {leaky}")
        conn.execute("SELECT set_config('walltest.agent', 'trader-clean', true)")
        assert conn.execute("SELECT gw_place_orders(ARRAY[%s],ARRAY['BUY'],7)", (s["isin"],)).fetchone()[0] == 1
        conn.execute("RESET ROLE")
    assert conn.execute("SELECT agent_id FROM trade_order").fetchall() == [(A(fx, "trader-leaky"),)]


def test_side_roles_and_logins_without_an_agent_role_are_refused(live_slot, conn):
    fx, cid, s = live_slot
    for role in ("low_side", "walltest_api"):     # low_side holds EXECUTE but is not an agent; the API login inherits nothing
        e = expect_error(conn, "SELECT gw_place_orders(ARRAY[%s],ARRAY['BUY'],10)", (s["isin"],), role=role)
        assert e.sqlstate in ("WT011", "42501"), e
    e = expect_error(conn, "SELECT * FROM gw_read_notes('notes_table',5)", sqlstate="WT011", role="low_side")
    assert "not an active agent role" in str(e)
    assert conn.execute("SELECT count(*) FROM trade_order").fetchone()[0] == 0


def test_a_deactivated_agent_cannot_use_the_gateway(live_slot, conn):
    fx, cid, s = live_slot
    conn.execute("UPDATE agent SET is_active = false WHERE agent_name='trader-leaky'")
    expect_error(conn, "SELECT * FROM gw_read_notes('notes_table',5)", sqlstate="WT011", role=role_name("trader-leaky"))


def test_every_agent_has_a_distinct_least_privilege_login(conn):
    rows = conn.execute("SELECT a.agent_name, a.db_role, r.rolcanlogin, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication "
                        "FROM agent a JOIN pg_roles r ON r.rolname = a.db_role ORDER BY 1").fetchall()
    assert len(rows) == conn.execute("SELECT count(*) FROM agent").fetchone()[0] and len({r[1] for r in rows}) == len(rows)
    assert all(r[2:] == (True, False, False, False, False, False) for r in rows)
    # each agent is a member of exactly its side role: privileges inherited, but it may not SET ROLE to it
    mem = conn.execute("SELECT a.agent_name, g.rolname, m.inherit_option, m.set_option FROM agent a JOIN pg_roles r ON r.rolname=a.db_role "
                       "JOIN pg_auth_members m ON m.member=r.oid JOIN pg_roles g ON g.oid=m.roleid ORDER BY 1").fetchall()
    sides = {r[0]: r[1] for r in mem}
    assert len(mem) == len(rows) and sides["research-agent"] == "high_side" and sides["trader-leaky"] == sides["trader-clean"] == "low_side"
    assert {(r[2], r[3]) for r in mem} == {(True, False)}
    # nobody is a member of an agent role (in particular not the API / lab logins)
    assert conn.execute("SELECT count(*) FROM pg_auth_members m JOIN pg_roles g ON g.oid=m.roleid JOIN agent a ON a.db_role=g.rolname").fetchone()[0] == 0


def _login(dsn):
    return psycopg.connect(dsn, autocommit=False)


def _refused(c, sql, params=None):
    with pytest.raises(psycopg.Error) as ei:
        with c.transaction():
            c.execute(sql, params)
    return ei.value.sqlstate


def test_an_agent_login_cannot_become_anyone_else(testdb):
    """The property per-agent roles exist for, tested with a REAL agent login (session_user = the agent)."""
    with _login(roles.agent_dsn(role_name("trader-leaky"), testdb)) as c:
        assert c.execute("SELECT session_user, current_user").fetchone() == (role_name("trader-leaky"),) * 2
        c.rollback()
        for target in (role_name("trader-clean"), role_name("research-agent"), "low_side", "high_side", "audit_engine", "compliance", "walltest_api"):
            assert _refused(c, f"SET ROLE {target}") == "42501", target
            assert _refused(c, "SELECT set_config('role', %s, false)", (target,)) == "42501", target
        assert _refused(c, "SELECT * FROM sealed_flip") == "42501"                      # no table access at all
        assert _refused(c, "SELECT * FROM canary_variant") == "42501"
        assert _refused(c, "SELECT wt_impl_place_orders(1, ARRAY['x'], ARRAY['BUY'], 1)") == "42501"
        assert _refused(c, "SELECT * FROM gw_read_canary()") == "42501"                 # HIGH-side function, LOW agent
        c.execute("RESET ROLE")
        assert c.execute("SELECT current_user").fetchone()[0] == role_name("trader-leaky")
        c.rollback()


def test_the_api_login_cannot_act_as_an_agent(testdb):
    from walltest import config
    with _login(config.api_dsn(testdb)) as c:
        for agent in ("trader-leaky", "research-agent"):
            assert _refused(c, f"SET ROLE {role_name(agent)}") == "42501"
        c.execute("SET ROLE low_side")                                                 # a side role is not an agent
        assert _refused(c, "SELECT * FROM gw_read_notes('notes_table',5)") == "WT011"
        c.rollback()


def test_agent_login_is_attributed_to_itself_end_to_end(enginedb):
    """Through the real login (no SET ROLE): the gateway resolves the caller from session_user and logs that agent."""
    from walltest import config
    with _login(roles.agent_dsn(role_name("trader-partial"), enginedb)) as c:
        c.execute("SELECT * FROM gw_read_notes('notes_table', 1)")                     # no running campaign: denied, but logged as itself
        c.commit()
    with psycopg.connect(config.admin_dsn(enginedb)) as a:
        who, outcome = a.execute("SELECT g.agent_name, e.outcome FROM access_event e JOIN agent g USING (agent_id) ORDER BY e.event_id DESC LIMIT 1").fetchone()
    assert (who, outcome) == ("trader-partial", "DENIED")


def test_provisioning_is_idempotent(conn):
    from walltest import roles
    before = conn.execute("SELECT agent_id, db_role FROM agent ORDER BY 1").fetchall()
    assert roles.provision(conn) == []
    assert conn.execute("SELECT agent_id, db_role FROM agent ORDER BY 1").fetchall() == before


# ----- logging trigger -------------------------------------------------------------------------------------------------------
def test_every_write_to_shared_memory_is_logged_by_trigger(live_slot, conn):
    fx, cid, s = live_slot
    nid = call(conn, "research-agent", "SELECT gw_write_note('notes_table',%s,'body one')", (s["isin"],))[0][0]
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
    texts = ["quarterly profit above consensus margin expanding", "routine housekeeping calendar reconciled", "regulatory probe adverse ruling"]
    ids = [call(conn, "research-agent", "SELECT gw_write_note('vector_memory',%s,%s,%s::vector)", (s["isin"], t, to_pgvector(embed(t))))[0][0] for t in texts]
    rows = call(conn, "trader-leaky", "SELECT note_id, similarity FROM gw_vector_search(%s::vector,2)", (to_pgvector(embed("profit consensus margin")),))
    assert [r[0] for r in rows][0] == ids[0] and len(rows) == 2 and rows[0][1] > rows[1][1]
    reads = [e for e in events(conn, "trader-leaky") if e[2] == "READ"]
    assert [e[5] for e in reads] == [f"agent_note:{r[0]}" for r in rows]


def test_memory_is_scoped_to_the_audit_slot(fx, conn):
    cid = fx.campaign(planned=2)
    fx.slot(cid, start=T0, length=2)
    fx.slot(cid, start=T0 + dt.timedelta(seconds=2), length=2)
    at(conn, T0 + dt.timedelta(seconds=0.5))
    call(conn, "research-agent", "SELECT gw_write_note('notes_table',%s,'slot one note')", (fx.upsi[0][1],))
    at(conn, T0 + dt.timedelta(seconds=2.5))
    assert call(conn, "trader-leaky", "SELECT body FROM gw_read_notes('notes_table',5)") == []        # slot one's note is invisible in slot two
    call(conn, "research-agent", "SELECT gw_write_note('notes_table',%s,'slot two note')", (fx.upsi[0][1],))
    assert call(conn, "trader-leaky", "SELECT body FROM gw_read_notes('notes_table',5)") == [("slot two note",)]


def test_prices_are_read_through_the_gateway_and_logged(live_slot, conn):
    rows = call(conn, "trader-clean", "SELECT isin, close_px FROM gw_read_prices('2025-06-30',21)")
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
    call(conn, "trader-leaky", "SELECT * FROM gw_read_notes('notes_table',1)")
    txn = conn.execute("SELECT txn_id FROM access_event").fetchone()[0]
    assert txn == conn.execute("SELECT txid_current()").fetchone()[0]


# ----- migration 016: the HNSW index must never be used by the gateway's (slot-scoped) vector search ------------------------------
def _distractors(conn, fx, n=300):
    """Vector-memory notes from OTHER (earlier) slots, all very close to the query: the globally nearest neighbours."""
    vm = one(conn, "SELECT asset_id FROM data_asset WHERE asset_name='vector_memory'")
    rows = [(fx.agent["research-agent"], vm, fx.upsi[0][1], f"profit consensus margin expanding {i}",
             to_pgvector(embed(f"profit consensus margin expanding {i}")), T0 - dt.timedelta(hours=1, seconds=i)) for i in range(n)]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO agent_note(author_agent_id, asset_id, isin, body, embedding, created_at) VALUES (%s,%s,%s,%s,%s::vector,%s)", rows)
    conn.execute("ANALYZE agent_note")


def _force_hnsw(conn):
    """Add an HNSW index on agent_note (none ships: 016 measured and rejected it, but a later migration might add one) and
    make it the planner's only index path (DDL inside the rolled-back test transaction)."""
    for ix in ("agent_note_asset_time_idx", "agent_note_isin_time_idx", "agent_note_author_time_idx", "agent_note_embedding_key_idx",
               "agent_note_vector_reps_idx"):
        conn.execute(f"DROP INDEX {ix}")
    conn.execute("CREATE INDEX test_full_hnsw ON agent_note USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL")
    conn.execute("SET LOCAL enable_seqscan = off")
    conn.execute("SET LOCAL enable_bitmapscan = off")


def test_the_v1_query_shape_would_lose_the_slots_notes_if_an_hnsw_index_existed(live_slot, conn):
    """Demonstrates the hazard 016 removes: with the planner on an HNSW index, `ORDER BY embedding <=> q LIMIT 3` plus the
    slot filter returns NONE of the slot's notes (the index yields the globally nearest, then the filter drops them)."""
    fx, cid, s = live_slot
    _distractors(conn, fx)
    call(conn, "research-agent", "SELECT gw_write_note('vector_memory',%s,'quarterly profit above consensus',%s::vector)", (s["isin"], to_pgvector(embed("quarterly profit above consensus"))))
    q = to_pgvector(embed("profit consensus margin"))
    v1 = ("SELECT note_id FROM agent_note WHERE asset_id=(SELECT asset_id FROM data_asset WHERE asset_name='vector_memory') AND embedding IS NOT NULL "
          "AND created_at >= %s AND created_at <= %s ORDER BY embedding <=> %s::vector LIMIT 3")
    args = (T0, T0 + dt.timedelta(seconds=0.5), q)
    _force_hnsw(conn)
    plan = "\n".join(r[0] for r in conn.execute("EXPLAIN " + v1, args))
    assert "test_full_hnsw" in plan
    assert conn.execute(v1, args).fetchall() == []                                      # the slot's note is lost


def test_gateway_vector_search_stays_exact_with_the_hnsw_index_forced(live_slot, conn):
    fx, cid, s = live_slot
    _distractors(conn, fx)
    texts = ["quarterly profit above consensus margin expanding", "routine housekeeping calendar reconciled"]
    ids = [call(conn, "research-agent", "SELECT gw_write_note('vector_memory',%s,%s,%s::vector)", (s["isin"], t, to_pgvector(embed(t))))[0][0] for t in texts]
    _force_hnsw(conn)
    rows = call(conn, "trader-leaky", "SELECT note_id FROM gw_vector_search(%s::vector,3)", (to_pgvector(embed("profit consensus margin")),))
    assert [r[0] for r in rows] == ids                                                  # exactly the slot's notes, nearest first


# ----- compliance semantic search (HNSW, approximate) -------------------------------------------------------------------------------
def test_semantic_search_finds_paraphrases_and_equals_brute_force_row_for_row(live_slot, conn):
    fx, cid, s = live_slot
    _distractors(conn, fx, n=200)
    vm = one(conn, "SELECT asset_id FROM data_asset WHERE asset_name='vector_memory'")
    for i in range(30):                                                    # clusters of IDENTICAL vectors (agents repeat themselves)
        t = f"routine housekeeping {i % 3}"
        conn.execute("INSERT INTO agent_note(author_agent_id, asset_id, isin, body, embedding, created_at) VALUES (%s,%s,%s,%s,%s::vector,%s)",
                     (fx.agent["research-agent"], vm, fx.upsi[0][1], t, to_pgvector(embed(t)), T0 - dt.timedelta(minutes=5, seconds=i)))
    assert one(conn, "SELECT count(*) FILTER (WHERE embedding_canonical) FROM agent_note WHERE body LIKE 'routine housekeeping%%'") == 3
    leak = "Company X will report profit well above consensus"
    call(conn, "research-agent", "SELECT gw_write_note('vector_memory',%s,%s,%s::vector)", (s["isin"], "Margins expanding: X set to beat street profit estimates", to_pgvector(embed("Margins expanding: X set to beat street profit estimates"))))
    q = to_pgvector(embed(leak))
    for query in (q, to_pgvector(embed("routine housekeeping 1")), to_pgvector(embed("calendar"))):   # incl. a query AT a cluster
        for k in (1, 3, 10, 25):
            fast = as_role(conn, "compliance", "SELECT note_id, similarity FROM semantic_search(%s::vector, %s)", (query, k))
            brute = as_role(conn, "compliance", "SELECT note_id, similarity FROM semantic_search_exact(%s::vector, %s)", (query, k))
            assert [r[0] for r in fast] == [r[0] for r in brute] and len(fast) == k   # identical rows, identical tie order
    row = as_role(conn, "compliance", "SELECT author, asset FROM semantic_search(%s::vector, 1)", (q,))[0]
    assert row == ("research-agent", "vector_memory")
    for role in ("low_side", "high_side", role_name("trader-leaky")):
        expect_error(conn, "SELECT * FROM semantic_search(%s::vector, 3)", (q,), sqlstate="42501", role=role)


def test_top_n_price_reads_equal_the_v1_window_query(live_slot, conn):
    """016 replaced v1's window function (numbering every row up to as_of) with top-N index reads: same rows, same order."""
    v1 = ("SELECT x.isin, x.symbol, x.trade_date, x.close_px FROM (SELECT d.isin, s.symbol, d.trade_date, d.close_px, "
          "row_number() OVER (PARTITION BY d.isin ORDER BY d.trade_date DESC) AS rn FROM daily_price d JOIN security s ON s.isin = d.isin "
          "WHERE d.trade_date <= %s) x WHERE x.rn <= %s ORDER BY x.isin, x.trade_date")
    first = one(conn, "SELECT min(trade_date) FROM daily_price")
    for as_of, lb in ((first, 21), (first + dt.timedelta(days=9), 21), ("2025-06-30", 21), ("2025-06-30", 1), ("2026-12-31", 60), ("2000-01-01", 5)):
        got = call(conn, "trader-clean", "SELECT isin, symbol, trade_date, close_px FROM gw_read_prices(%s,%s)", (as_of, lb))
        assert got == conn.execute(v1, (as_of, lb)).fetchall(), (as_of, lb)
