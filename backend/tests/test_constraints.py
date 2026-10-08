"""Every negative constraint test: bad data is refused by the database itself, with the real Postgres error."""
import datetime as dt

import psycopg
import pytest
from conftest import expect_error, one

UTC = dt.timezone.utc


# ----- format / domain CHECKs ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("isin", ["XX0000000000", "INE00A0101", "ine002a01018", "INE002A010189", "IN-002A01018"])
def test_isin_format_check(conn, isin):
    expect_error(conn, "INSERT INTO security VALUES (%s,'ZZZ','Z Ltd','Z')", (isin,), sqlstate="22001" if len(isin) > 12 else "23514")


def test_domain_checks(conn):
    expect_error(conn, "INSERT INTO department(dept_name, area_type) VALUES ('x','SECRET')", sqlstate="23514")
    expect_error(conn, "INSERT INTO app_user(full_name,email,user_role) VALUES ('a','not-an-email','COMPLIANCE')", sqlstate="23514")
    expect_error(conn, "INSERT INTO wall_membership(wall_id,agent_id,side) SELECT wall_id, 1, 'MID' FROM info_wall LIMIT 1", sqlstate="23514")
    expect_error(conn, "INSERT INTO data_asset(asset_name,asset_kind,classification,owner_dept_id) VALUES ('q','TABLE','TOP_SECRET',1)", sqlstate="23514")
    expect_error(conn, "INSERT INTO data_asset(asset_name,asset_kind,classification,owner_dept_id) VALUES ('q','FILE','PUBLIC',1)", sqlstate="23514")
    expect_error(conn, "INSERT INTO access_event(agent_id,asset_id,op,event_time,txn_id) VALUES (1,1,'MERGE',now(),1)", sqlstate="23514")
    expect_error(conn, "INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) SELECT 2, isin, 'HOLD', 1, now() FROM security LIMIT 1", sqlstate="23514")
    expect_error(conn, "INSERT INTO trade_order(agent_id,isin,side,quantity,placed_at) SELECT 2, isin, 'BUY', 0, now() FROM security LIMIT 1", sqlstate="23514")


def test_alpha_must_be_in_open_interval(fx, conn):
    for a in (0, 0.5, 0.75, -0.1):
        expect_error(conn, "INSERT INTO audit_campaign(wall_id,created_by,alpha,planned_slots) VALUES (%s,%s,%s,10)",
                     (fx.wall1, fx.user, a), sqlstate="23514")
    conn.execute("INSERT INTO audit_campaign(wall_id,created_by,alpha,planned_slots) VALUES (%s,%s,0.001,10)", (fx.wall1, fx.user))


def test_flip_bit_variant_bit_and_salt_length(fx, conn):
    cid = fx.campaign(planned=2)
    s = fx.slot(cid)
    # the commitment trigger would fire first (BEFORE triggers run before CHECKs); disable it inside this rolled-back txn
    # so the CHECK constraints themselves are what is exercised
    conn.execute("ALTER TABLE sealed_flip DISABLE TRIGGER sealed_flip_commitment")
    conn.execute("DELETE FROM sealed_flip WHERE false")
    expect_error(conn, "INSERT INTO sealed_flip(slot_id,flip_bit,salt) VALUES (%s,2,%s)", (s["slot_id"] + 1000, b"x" * 32), sqlstate="23514")
    expect_error(conn, "INSERT INTO sealed_flip(slot_id,flip_bit,salt) VALUES (%s,0,%s)", (s["slot_id"] + 1000, b"x" * 15), sqlstate="23514")
    conn.execute("ALTER TABLE sealed_flip ENABLE TRIGGER sealed_flip_commitment")
    expect_error(conn, "INSERT INTO canary_variant(slot_id,variant_bit,direction,content) VALUES (%s,2,'POSITIVE','x')", (s["slot_id"],), sqlstate="23514")
    expect_error(conn, "INSERT INTO canary_variant(slot_id,variant_bit,direction,content) VALUES (%s,0,'NEUTRAL','x')", (s["slot_id"],), sqlstate="23514")


def test_commitment_must_be_64_lowercase_hex(fx, conn):
    cid = fx.campaign()
    for bad in ("g" * 64, "A" * 64, "a" * 63, "a" * 65):
        expect_error(conn, "SELECT engine_commit_slot(%s,%s,1,tstzrange(now()+interval '1 day',now()+interval '1 day 2 seconds','[)'),%s,now(),0::smallint,%s,1::smallint,'p','n')",
                     (cid, fx.treatments(cid)[0], bad, b"s" * 32))


def test_row_checks_prices_grants(conn):
    isin = one(conn, "SELECT isin FROM security LIMIT 1")
    expect_error(conn, "INSERT INTO daily_price VALUES (%s,'2001-01-01',10,9,8,9,1)", (isin,), sqlstate="23514")    # high < open
    expect_error(conn, "INSERT INTO daily_price VALUES (%s,'2001-01-01',10,12,11,11,1)", (isin,), sqlstate="23514")  # low > open
    expect_error(conn, "INSERT INTO daily_price VALUES (%s,'2001-01-01',10,12,9,13,1)", (isin,), sqlstate="23514")   # close > high
    expect_error(conn, "INSERT INTO daily_price VALUES (%s,(SELECT max(trade_date) FROM daily_price),10,12,9,11,1)", (isin,), sqlstate="23505")  # one bar per stock per day
    expect_error(conn, "INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from,valid_to) VALUES (2,3,'READ',1,'2025-01-02','2025-01-01')", sqlstate="23514")
    expect_error(conn, "INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from,valid_to) VALUES (2,3,'READ',1,'2025-01-01','2025-01-01')", sqlstate="23514")


def test_one_side_per_agent_per_wall(fx, conn):
    expect_error(conn, "INSERT INTO wall_membership(wall_id,agent_id,side) VALUES (%s,%s,'HIGH')", (fx.wall1, fx.agent["trader-clean"]), sqlstate="23505")


# ----- SDD: exactly one sharer and one recipient --------------------------------------------------------------------
def test_sdd_two_sharers_rejected(conn):
    u, a, up = one(conn, "SELECT min(user_id) FROM app_user"), one(conn, "SELECT min(agent_id) FROM agent"), one(conn, "SELECT min(upsi_id) FROM upsi_item")
    e = expect_error(conn, "INSERT INTO sdd_entry(upsi_id,shared_by_user,shared_by_agent,recipient_agent,purpose) VALUES (%s,%s,%s,%s,'x')",
                     (up, u, a, a), sqlstate="23514")
    assert "sdd_exactly_one_sharer" in str(e)


def test_sdd_zero_sharers_and_two_recipients_rejected(conn):
    u, a, up = one(conn, "SELECT min(user_id) FROM app_user"), one(conn, "SELECT min(agent_id) FROM agent"), one(conn, "SELECT min(upsi_id) FROM upsi_item")
    expect_error(conn, "INSERT INTO sdd_entry(upsi_id,recipient_agent,purpose) VALUES (%s,%s,'x')", (up, a), contains="sdd_exactly_one_sharer")
    e = expect_error(conn, "INSERT INTO sdd_entry(upsi_id,shared_by_user,recipient_user,recipient_agent,purpose) VALUES (%s,%s,%s,%s,'x')", (up, u, u, a))
    assert "sdd_exactly_one_recipient" in str(e)
    conn.execute("INSERT INTO sdd_entry(upsi_id,shared_by_user,recipient_agent,purpose) VALUES (%s,%s,%s,'ok')", (up, u, a))  # one + one is fine


# ----- composite FK -------------------------------------------------------------------------------------------------
def test_slot_cannot_use_another_campaigns_treatment(fx, conn):
    c1, c2 = fx.campaign(), fx.campaign(wall=fx.wall0)
    t_other = fx.treatments(c2)[0]
    e = expect_error(conn, "SELECT engine_commit_slot(%s,%s,1,tstzrange('2020-07-01','2020-07-01 00:00:02','[)'),%s,'2020-06-30',0::smallint,%s,1::smallint,'p','n')",
                     (c1, t_other, "0" * 64, b"s" * 32), sqlstate="23503")
    assert "canary_slot_treatment_id_campaign_id_fkey" in str(e) or "foreign key" in str(e)


def test_audit_result_cannot_use_another_campaigns_treatment(fx, conn):
    c1, c2 = fx.campaign(), fx.campaign(wall=fx.wall0)
    t_other = fx.treatments(c2)[0]
    expect_error(conn, "INSERT INTO audit_result(campaign_id,treatment_id,low_agent_id,n_slots,n_correct,p_value,leakage_bits,verdict,computed_by,"
                       "p_adjusted,acc_lower,leakage_bits_lower,family_size,alpha,clock_mode,result_hash) VALUES (%s,%s,2,10,5,0.5,0,'NO_EVIDENCE',%s,1,0.2,0,1,0.05,'LIVE',%s)",
                 (c1, t_other, fx.user, "a" * 64), sqlstate="23503")


def test_treatment_cells_unique_per_campaign(fx, conn):
    cid = fx.campaign(design="ALL_ON")
    expect_error(conn, "INSERT INTO treatment(campaign_id,vector_memory_on,notes_table_on,cache_on) VALUES (%s,true,true,true)", (cid,), sqlstate="23505")


# ----- exclusion constraint: no overlapping slots --------------------------------------------------------------------
def test_overlapping_slots_in_a_campaign_rejected(fx, conn):
    cid = fx.campaign(planned=2)
    t0 = dt.datetime(2020, 7, 1, tzinfo=UTC)
    fx.slot(cid, start=t0, length=5)
    e = expect_error(conn, "SELECT 1", sqlstate=None) if False else None
    with pytest.raises(psycopg.errors.ExclusionViolation) as ei:
        with conn.transaction():
            fx.slot(cid, start=t0 + dt.timedelta(seconds=3), length=5)
    assert "canary_slot_no_overlap" in str(ei.value) or "canary_slot_one_clock" in str(ei.value)


def test_adjacent_slots_are_allowed(fx, conn):
    cid = fx.campaign(planned=2)
    t0 = dt.datetime(2020, 7, 1, tzinfo=UTC)
    fx.slot(cid, start=t0, length=2)
    fx.slot(cid, start=t0 + dt.timedelta(seconds=2), length=2)       # [t0,t0+2) then [t0+2,t0+4): half-open, no overlap


def test_slots_of_different_campaigns_cannot_overlap_either(fx, conn):
    """D-09: orders carry no campaign id, so one audit clock per firm."""
    c1, c2 = fx.campaign(), fx.campaign(wall=fx.wall0)
    t0 = dt.datetime(2020, 7, 1, tzinfo=UTC)
    fx.slot(c1, start=t0, length=5)
    with pytest.raises(psycopg.errors.ExclusionViolation) as ei:
        with conn.transaction():
            fx.slot(c2, start=t0 + dt.timedelta(seconds=1), length=2)
    assert "canary_slot_one_clock" in str(ei.value)


# ----- cardinalities 1:2 and 1:1 -------------------------------------------------------------------------------------
def test_slot_must_offer_exactly_two_variants_and_one_flip(fx, conn):
    cid = fx.campaign()
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    t = fx.treatments(cid)[0]
    with pytest.raises(psycopg.Error) as ei:
        with conn.transaction():
            conn.execute("INSERT INTO canary_slot(campaign_id,treatment_id,upsi_id,slot_period,commitment,committed_at) VALUES (%s,%s,1,tstzrange('2020-08-01','2020-08-01 00:00:02','[)'),%s,'2020-07-31')",
                         (cid, t, "0" * 64))
    assert ei.value.sqlstate == "WT005"


def test_variants_unique_per_bit_and_direction(fx, conn):
    cid = fx.campaign()
    s = fx.slot(cid)
    expect_error(conn, "INSERT INTO canary_variant(slot_id,variant_bit,direction,content) VALUES (%s,0,'POSITIVE','dup')", (s["slot_id"],), sqlstate="23505")
    expect_error(conn, "INSERT INTO canary_variant(slot_id,variant_bit,direction,content) VALUES (%s,1,'NEGATIVE','dup')", (s["slot_id"],), sqlstate="23505")


def test_one_running_campaign_per_wall(fx, conn):
    fx.campaign(wall=fx.wall1)
    c2 = fx.campaign(wall=fx.wall1, start=False)
    expect_error(conn, "SELECT start_campaign(%s)", (c2,), sqlstate="23505")
    expect_error(conn, "SELECT create_campaign(%s,%s,0.05,7,'FULL_FACTORIAL','LIVE','{}'::jsonb)", (fx.wall1, fx.user), contains="multiple of")
