"""Doc 8.4 append-only triggers: UPDATE/DELETE/TRUNCATE raise (trigger layer), and are also not granted (privilege layer)."""
import pytest
from conftest import expect_error, one

TABLES = ["access_event", "trade_order", "canary_slot", "sealed_flip", "audit_result", "canary_variant"]

UPDATE = {"access_event": "UPDATE access_event SET detail='tampered'",
          "trade_order": "UPDATE trade_order SET quantity=1",
          "canary_slot": "UPDATE canary_slot SET commitment=repeat('0',64)",
          "sealed_flip": "UPDATE sealed_flip SET flip_bit=1-flip_bit",
          "audit_result": "UPDATE audit_result SET verdict='LEAK'",
          "canary_variant": "UPDATE canary_variant SET content='edited'"}


@pytest.fixture
def populated(fx, conn):
    cid = fx.campaign()
    s = fx.slot(cid)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")      # run the deferred slot-completeness check now (TRUNCATE refuses pending events)
    conn.execute("INSERT INTO access_event(agent_id,asset_id,op,row_ref,event_time,txn_id) VALUES (2,3,'READ','x',now(),1)")
    conn.execute("INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) SELECT 2,isin,'BUY',1,now() FROM security LIMIT 1")
    conn.execute("INSERT INTO audit_result(campaign_id,treatment_id,low_agent_id,n_slots,n_correct,p_value,leakage_bits,verdict,computed_by,p_adjusted,"
                 "acc_lower,leakage_bits_lower,family_size,alpha,clock_mode,result_hash) "
                 "SELECT %s,%s,2,10,5,0.5,0,'NO_EVIDENCE',%s,1,0.2,0,1,0.05,'SIMULATED',%s",
                 (cid, fx.treatments(cid)[0], fx.user, "a" * 64))
    return s


@pytest.mark.parametrize("table", TABLES)
def test_trigger_blocks_update_delete_truncate_even_for_the_owner(conn, populated, table):
    for sql in (UPDATE[table], f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
        e = expect_error(conn, sql, sqlstate="WT001", role="walltest_owner")
        assert "append-only" in str(e) and table in str(e)


@pytest.mark.parametrize("table", TABLES)
def test_privileges_are_also_revoked(conn, populated, table):
    for role in ("audit_engine", "compliance", "low_side", "high_side"):
        expect_error(conn, UPDATE[table], sqlstate="42501", role=role)
        expect_error(conn, f"DELETE FROM {table}", sqlstate="42501", role=role)
        expect_error(conn, f"TRUNCATE {table}", sqlstate="42501", role=role)


def test_the_error_text_the_rules_lab_shows(conn, populated):
    e = expect_error(conn, "UPDATE trade_order SET quantity=999", sqlstate="WT001", role="walltest_owner")
    assert "trade_order is append-only: UPDATE is not permitted" in str(e)


def test_inserts_still_work(conn, populated):
    conn.execute("INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) SELECT 3,isin,'SELL',5,now() FROM security LIMIT 1")
    assert one(conn, "SELECT count(*) FROM trade_order") == 2
