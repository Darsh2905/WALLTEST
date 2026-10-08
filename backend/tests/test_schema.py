"""Doc section 8: exactly 20 relations, the keys and constraints of 8.4, identity PKs, indexes."""
from conftest import one

TABLES = """department app_user agent info_wall wall_membership data_asset access_grant security daily_price upsi_item sdd_entry
audit_campaign treatment canary_slot canary_variant sealed_flip agent_note access_event trade_order audit_result""".split()


def test_exactly_the_twenty_relations(conn):
    got = sorted(r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    assert got == sorted(TABLES) and len(got) == 20


def test_extensions(conn):
    ext = {r[0] for r in conn.execute("SELECT extname FROM pg_extension")}
    assert {"vector", "pgcrypto", "btree_gist"} <= ext
    assert one(conn, "SELECT current_setting('server_version_num')::int") // 10000 == 16


def test_surrogate_pks_are_generated_always_identity(conn):
    pks = {"department": "dept_id", "app_user": "user_id", "agent": "agent_id", "info_wall": "wall_id", "data_asset": "asset_id",
           "access_grant": "grant_id", "upsi_item": "upsi_id", "sdd_entry": "sdd_id", "audit_campaign": "campaign_id",
           "treatment": "treatment_id", "canary_slot": "slot_id", "canary_variant": "variant_id", "agent_note": "note_id",
           "access_event": "event_id", "trade_order": "order_id", "audit_result": "result_id"}
    for t, col in pks.items():
        assert one(conn, "SELECT is_identity||'/'||identity_generation FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                   (t, col)) == "YES/ALWAYS", (t, col)


def test_natural_and_composite_keys(conn):
    def pk(t):
        return [r[0] for r in conn.execute(
            "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
            "WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY array_position(i.indkey::int[], a.attnum::int)", (t,))]
    assert pk("security") == ["isin"]
    assert pk("daily_price") == ["isin", "trade_date"]
    assert pk("wall_membership") == ["wall_id", "agent_id"]
    assert pk("sealed_flip") == ["slot_id"]


def test_declared_constraint_types_exist(conn):
    cons = {r[0]: r[1] for r in conn.execute("SELECT conname, contype FROM pg_constraint WHERE connamespace='public'::regnamespace")}
    assert cons["canary_slot_no_overlap"] == "x"      # EXCLUDE USING gist (campaign_id WITH =, slot_period WITH &&)
    assert cons["canary_slot_one_clock"] == "x"
    assert cons["sdd_exactly_one_sharer"] == "c" and cons["sdd_exactly_one_recipient"] == "c"
    # composite FKs to treatment(treatment_id, campaign_id) from canary_slot and audit_result
    for t in ("canary_slot", "audit_result"):
        n = one(conn, "SELECT count(*) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='f' AND confrelid='treatment'::regclass AND cardinality(conkey)=2", (t,))
        assert n == 1, t


def test_exclusion_constraint_definition(conn):
    d = one(conn, "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='canary_slot_no_overlap'")
    assert "EXCLUDE USING gist" in d and "campaign_id WITH =" in d and "slot_period WITH &&" in d


def test_required_indexes(conn):
    idx = " ".join(r[0] for r in conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='public'"))
    assert "ON public.access_event USING btree (agent_id, event_time)" in idx
    assert "ON public.access_event USING btree (asset_id, event_time)" in idx
    assert "ON public.trade_order USING btree (agent_id, isin, placed_at)" in idx


def test_data_types_match_the_er_figures(conn):
    def typ(t, c):
        return one(conn, "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid=%s::regclass AND attname=%s", (t, c))
    assert typ("security", "isin") == "character(12)"
    assert typ("canary_slot", "slot_period") == "tstzrange"
    assert typ("canary_slot", "commitment") == "character(64)"
    assert typ("sealed_flip", "salt") == "bytea"
    assert typ("agent_note", "embedding") == "vector(384)"
    assert typ("access_event", "event_id") == "bigint" and typ("trade_order", "order_id") == "bigint" and typ("agent_note", "note_id") == "bigint"
    assert typ("audit_result", "p_value") == "double precision"
    assert typ("daily_price", "volume") == "bigint"
