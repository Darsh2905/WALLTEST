"""Fixtures: a fresh migrated+seeded PostgreSQL database per test session (real Postgres, real roles; nothing mocked)."""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from walltest import commitment as cm  # noqa: E402
from walltest import config, migrate, seed  # noqa: E402

UTC = dt.timezone.utc
TEST_DB = os.environ.get("WALLTEST_TEST_DB", "walltest_test")


@pytest.fixture(scope="session")
def testdb() -> str:
    migrate.drop_database(TEST_DB)
    migrate.create_database_if_missing(TEST_DB)
    migrate.migrate(TEST_DB, verbose=False)
    seed.seed(TEST_DB, verbose=False)
    os.environ["WALLTEST_DB_NAME"] = TEST_DB
    config.DB_NAME = TEST_DB
    yield TEST_DB


@pytest.fixture(scope="session")
def enginedb() -> str:
    """A second database for tests that COMMIT data through the real engine, so they cannot disturb rolled-back tests."""
    name = TEST_DB + "_engine"
    migrate.drop_database(name)
    migrate.create_database_if_missing(name)
    migrate.migrate(name, verbose=False)
    seed.seed(name, verbose=False)
    yield name


@pytest.fixture
def conn(testdb):
    """Superuser/admin connection inside a transaction that is ALWAYS rolled back (tests leave no residue)."""
    c = psycopg.connect(config.admin_dsn(testdb), autocommit=False)
    yield c
    c.rollback()
    c.close()


def one(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()[0]


def expect_error(conn, sql, params=None, *, sqlstate=None, contains=None, role=None):
    """Run `sql` (optionally as `role`) in a savepoint; assert it raises the expected PostgreSQL error."""
    with pytest.raises(psycopg.Error) as ei:
        with conn.transaction():
            if role:
                conn.execute(f"SET LOCAL ROLE {role}")
            conn.execute(sql, params)
    if sqlstate:
        assert ei.value.sqlstate == sqlstate, f"sqlstate {ei.value.sqlstate} != {sqlstate}: {ei.value}"
    if contains:
        assert contains in str(ei.value), f"{contains!r} not in {ei.value}"
    return ei.value


def as_role(conn, role, sql, params=None):
    with conn.transaction():
        conn.execute(f"SET LOCAL ROLE {role}")
        cur = conn.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.execute("RESET ROLE")        # SET LOCAL survives a released savepoint; restore the admin role explicitly
    return rows


def dicts_as(conn, role, sql, params=None) -> list[dict]:
    """as_role, but rows come back as dicts keyed by the cursor's column names (never by position)."""
    with conn.transaction():
        conn.execute(f"SET LOCAL ROLE {role}")
        cur = conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.execute("RESET ROLE")
    return rows


class Fx:
    """Builds small campaigns directly in SQL (inside the test transaction)."""
    BASE = dt.datetime(2020, 6, 1, tzinfo=UTC)          # tests use mid-2020; the engine uses 2021+, so windows never collide

    def __init__(self, conn):
        self.c = conn
        self.wall1 = one(conn, "SELECT wall_id FROM info_wall WHERE wall_name LIKE 'WALL-1%'")
        self.wall0 = one(conn, "SELECT wall_id FROM info_wall WHERE wall_name LIKE 'WALL-0%'")
        self.user = one(conn, "SELECT user_id FROM app_user WHERE user_role='COMPLIANCE' LIMIT 1")
        self.engine_user = one(conn, "SELECT user_id FROM app_user WHERE email='audit-engine@walltest.example'")
        self.agent = {r[0]: r[1] for r in conn.execute("SELECT agent_name, agent_id FROM agent")}
        self.upsi = conn.execute("SELECT upsi_id, isin FROM upsi_item ORDER BY upsi_id").fetchall()
        self._offset = 0

    def campaign(self, planned=2, design="ALL_ON", clock="SIMULATED", wall=None, alpha=0.05, start=True, config=None, started_at=None):
        """config: audit_campaign.config (e.g. {"inference": "SEQUENTIAL"}). started_at: the evidence window's start (fixture slots
        are in 2020, so evidence tests pass Fx.BASE; the default is the server clock, as in v1)."""
        cid = one(self.c, "SELECT create_campaign(%s,%s,%s::numeric,%s,%s,%s,%s::jsonb)",
                  (wall or self.wall1, self.user, alpha, planned, design, clock, json.dumps(config or {})))
        if start:
            self.c.execute("SELECT start_campaign(%s, coalesce(%s, clock_timestamp()))", (cid, started_at))
        return cid

    def treatments(self, cid):
        return [r[0] for r in self.c.execute("SELECT treatment_id FROM treatment WHERE campaign_id=%s ORDER BY 1", (cid,))]

    def slot(self, cid, tid=None, start=None, length=2.0, flip=None, salt=None, pos_bit=1, upsi_idx=0,
             committed_at=None, commitment=None, role=None):
        """Commit one slot. Returns dict with slot id, start, flip, salt."""
        start = start or (self.BASE + dt.timedelta(seconds=self._offset))
        self._offset += int(length) + 1000
        tid = tid or self.treatments(cid)[0]
        f, s = cm.draw_flip() if flip is None else (flip, salt or secrets.token_bytes(32))
        end = start + dt.timedelta(seconds=length)
        com = commitment or cm.commitment(cid, start, f, s)
        up = self.upsi[upsi_idx][0]
        sql = ("SELECT engine_commit_slot(%s,%s,%s,tstzrange(%s,%s,'[)'),%s,%s,%s::smallint,%s,%s::smallint,%s,%s)")
        params = (cid, tid, up, start, end, com, committed_at or (start - dt.timedelta(seconds=0.5)), f, s, pos_bit,
                  "Company X will report profit well above consensus.", "Company X will report profit well below consensus.")
        if role:
            self.c.execute(f"SET LOCAL ROLE {role}")
        sid = one(self.c, sql, params)
        if role:
            self.c.execute("RESET ROLE")
        return {"slot_id": sid, "start": start, "end": end, "flip": f, "salt": s, "isin": self.upsi[upsi_idx][1],
                "direction": ("POSITIVE" if f == pos_bit else "NEGATIVE")}


@pytest.fixture
def fx(conn):
    return Fx(conn)
