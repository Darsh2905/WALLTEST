"""Per-agent database logins (migration 015). CREATE ROLE needs a privileged login, so this runs as the admin connection
(bootstrap / seed / migrate), never on the request path.

Every agent gets its OWN login role wt_agent_<name>: a member of the side role(s) it sits on (inheriting their EXECUTE grants
on the gateway, but not allowed to SET ROLE to them), with no other memberships. The gateway authenticates the agent from
current_setting('role'), which for an agent login is the agent itself.

Why a LOGIN and not a role the API login switches into: PostgreSQL checks SET ROLE against the SESSION user. If agents ran
under the shared API login, any agent able to send SQL could `RESET ROLE` / `SET ROLE wt_agent_<someone else>` and the per-agent
role would authenticate nothing (found by tests/test_gateway.py; see DEVIATIONS D-24). With its own login an agent session
cannot become anyone else, and the API/lab logins are not members of any agent role.

Passwords are HMAC-SHA256(master key, role name); the master key lives outside the database (WALLTEST_AGENT_SECRET, or a
0600 file next to the signing key), so the engine can derive any agent's password and nothing in the DB reveals one."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from functools import lru_cache

import psycopg
from psycopg import sql

from . import config

API_LOGINS = ("walltest_api", "walltest_lab")
CONNECTION_LIMIT = 32


def role_name(agent_name: str) -> str:
    return "wt_agent_" + re.sub(r"[^a-z0-9]+", "_", agent_name.lower()).strip("_")


@lru_cache(maxsize=1)
def _master_key() -> bytes:
    env = os.environ.get("WALLTEST_AGENT_SECRET")
    if env:
        return env.encode()
    return config.publish_secret_once(config.AGENT_SECRET_PATH, secrets.token_bytes(32))


def agent_password(role: str) -> str:
    return hmac.new(_master_key(), role.encode(), hashlib.sha256).hexdigest()


def agent_dsn(role: str, dbname: str | None = None) -> str:
    return config.dsn(role, agent_password(role), dbname)


def provision(conn: psycopg.Connection) -> list[str]:
    """Idempotent. `conn` must be an admin connection to the database whose agents are provisioned."""
    if conn.execute("SELECT to_regclass('public.agent') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='agent' AND column_name='db_role')").fetchone()[0] is not True:
        return []
    made = []
    dbname = conn.execute("SELECT current_database()").fetchone()[0]
    for aid, name, db_role in conn.execute("SELECT agent_id, agent_name, db_role FROM agent ORDER BY agent_id").fetchall():
        r = db_role or role_name(name)
        opts = sql.SQL("LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION INHERIT CONNECTION LIMIT {} PASSWORD {}").format(
            sql.Literal(CONNECTION_LIMIT), sql.Literal(agent_password(r)))
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,)).fetchone():
            conn.execute(sql.SQL("ALTER ROLE {} ").format(sql.Identifier(r)) + opts)
        else:
            conn.execute(sql.SQL("CREATE ROLE {} ").format(sql.Identifier(r)) + opts)
            made.append(r)
        for (side,) in conn.execute("SELECT DISTINCT side FROM wall_membership WHERE agent_id = %s", (aid,)).fetchall():
            grp = "high_side" if side == "HIGH" else "low_side"
            conn.execute(sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET FALSE").format(sql.Identifier(grp), sql.Identifier(r)))
        for login in API_LOGINS:          # v2 early drafts let the API login SET agent roles: remove any such membership
            if conn.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", (login, r)).fetchone()[0]:
                conn.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(r), sql.Identifier(login)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(r)))
        if db_role is None:
            conn.execute("UPDATE agent SET db_role = %s WHERE agent_id = %s", (r, aid))
    return made
