"""The wall trigger: a LOW-side agent can never be granted a UPSI-classified asset (on grant, membership AND reclassification),
and the grant review (LOW agents holding any grant on an inside-owned asset) returns zero rows on the seed."""
from conftest import expect_error, one


def aid(conn, name):
    return one(conn, "SELECT asset_id FROM data_asset WHERE asset_name=%s", (name,))


def test_granting_upsi_to_a_low_side_agent_is_refused(fx, conn):
    for asset in ("upsi_item", "canary_variant"):
        e = expect_error(conn, "INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from) VALUES (%s,%s,'READ',%s,now())",
                         (fx.agent["trader-clean"], aid(conn, asset), fx.user), sqlstate="WT003")
        assert "wall violation" in str(e) and "LOW side" in str(e)


def test_granting_upsi_to_a_high_side_agent_is_fine(fx, conn):
    conn.execute("INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from) VALUES (%s,%s,'WRITE',%s,now())",
                 (fx.agent["research-agent"], aid(conn, "upsi_item"), fx.user))


def test_updating_an_existing_grant_to_point_at_upsi_is_refused(fx, conn):
    g = one(conn, "SELECT grant_id FROM access_grant WHERE agent_id=%s AND asset_id=%s", (fx.agent["trader-clean"], aid(conn, "daily_price")))
    expect_error(conn, "UPDATE access_grant SET asset_id=%s WHERE grant_id=%s", (aid(conn, "upsi_item"), g), sqlstate="WT003")


def test_bypass_1_adding_an_agent_with_a_upsi_grant_to_the_low_side_is_refused(fx, conn):
    """Hole in the proposal's trigger (access_grant only): grant first, then move the agent behind the wall."""
    a = one(conn, "INSERT INTO agent(agent_name,dept_id,owner_user_id,model_name,model_version) VALUES ('mover',1,1,'m','1') RETURNING agent_id")
    conn.execute("INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from) VALUES (%s,%s,'READ',%s,now())", (a, aid(conn, "upsi_item"), fx.user))
    expect_error(conn, "INSERT INTO wall_membership(wall_id,agent_id,side) VALUES (%s,%s,'LOW')", (fx.wall1, a), sqlstate="WT003")
    conn.execute("INSERT INTO wall_membership(wall_id,agent_id,side) VALUES (%s,%s,'HIGH')", (fx.wall1, a))      # HIGH is fine
    expect_error(conn, "UPDATE wall_membership SET side='LOW' WHERE agent_id=%s", (a,), sqlstate="WT003")


def test_bypass_2_reclassifying_a_held_asset_as_upsi_is_refused(fx, conn):
    e = expect_error(conn, "UPDATE data_asset SET classification='UPSI' WHERE asset_name='notes_table'", sqlstate="WT003")
    assert "cannot be classified UPSI" in str(e)


def test_grant_review_is_empty_on_the_seed(conn):
    assert conn.execute("SELECT * FROM v_grant_review").fetchall() == []


def test_grant_review_catches_what_the_trigger_cannot(fx, conn):
    """An INTERNAL asset owned by an INSIDE department is not UPSI-classified, so the trigger allows the grant;
    the review query is what finds it."""
    inside = one(conn, "SELECT dept_id FROM department WHERE area_type='INSIDE' LIMIT 1")
    a = one(conn, "INSERT INTO data_asset(asset_name,asset_kind,classification,owner_dept_id) VALUES ('research_scratch','TABLE','INTERNAL',%s) RETURNING asset_id", (inside,))
    conn.execute("INSERT INTO access_grant(agent_id,asset_id,privilege,granted_by,valid_from) VALUES (%s,%s,'READ',%s,now())", (fx.agent["trader-leaky"], a, fx.user))
    rows = conn.execute("SELECT agent_name, asset_name, owner_dept FROM v_grant_review").fetchall()
    assert rows == [("trader-leaky", "research_scratch", "Corporate Finance & Research")]


def test_the_shared_channels_are_not_inside_owned(conn):
    """The leak channels are shared infrastructure owned by a PUBLIC department: access control looks clean, which is the point."""
    owners = dict(conn.execute("SELECT d.asset_name, dep.area_type FROM data_asset d JOIN department dep ON dep.dept_id=d.owner_dept_id").fetchall())
    assert owners["notes_table"] == owners["vector_memory"] == owners["feature_cache"] == "PUBLIC"
    assert owners["upsi_item"] == owners["canary_variant"] == "INSIDE"
