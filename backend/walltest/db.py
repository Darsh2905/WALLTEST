"""Async connection pools + per-request role switching.

The main pool logs in as `walltest_api`: not a superuser, not a table owner, NOINHERIT (no privileges of its own). Every unit
of work for the four service roles runs inside a transaction that begins with set_config('role', <role>, true), so RLS and
GRANTs apply exactly as they would for that role, and the role reverts automatically at COMMIT/ROLLBACK (safe when pooled).

Agents (v2, migration 015) do NOT go through that login: each agent has its own login role and its own small pool, so the
session user IS the agent and it cannot SET ROLE to anything else (walltest/roles.py explains why this matters)."""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from contextlib import asynccontextmanager

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

ROLES = frozenset({"high_side", "low_side", "audit_engine", "compliance"})
AGENT_ROLE = re.compile(r"^wt_agent_[a-z0-9_]+$")   # per-agent logins (migration 015)


class Database:
    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 16, agent_pool_size: int = 8):
        self._dsn = dsn
        self._pool = AsyncConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False,
                                         kwargs={"row_factory": dict_row}, name="walltest-api")
        self._agent_pools: dict[str, AsyncConnectionPool] = {}
        self._agent_pool_size = agent_pool_size
        self._agent_lock = asyncio.Lock()

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        for p in self._agent_pools.values():
            await p.close()
        self._agent_pools.clear()
        await self._pool.close()

    async def _agent_pool(self, role: str) -> AsyncConnectionPool:
        p = self._agent_pools.get(role)
        if p is not None:
            return p
        async with self._agent_lock:
            p = self._agent_pools.get(role)
            if p is None:
                from .roles import agent_password
                info = conninfo_to_dict(self._dsn)
                dsn = make_conninfo(**{**info, "user": role, "password": agent_password(role)})
                p = AsyncConnectionPool(dsn, min_size=1, max_size=self._agent_pool_size, open=False,
                                        kwargs={"row_factory": dict_row}, name=role)
                await p.open(wait=True, timeout=30)
                self._agent_pools[role] = p
        return p

    @asynccontextmanager
    async def session(self, role: str, sim_now: dt.datetime | None = None, durable: bool = True):
        """A transaction running as `role`. sim_now sets the session clock used by SIMULATED campaigns' gateway calls.

        durable=False commits without waiting for the WAL flush (synchronous_commit = off; agent sessions always). This is
        safe for every VERDICT: a campaign's results are written by one synchronously committed freeze, and flushing that
        commit record flushes all WAL before it -- including every slot-phase commit the verdict depends on. A crash can
        lose only the last few hundred ms of an UNFROZEN campaign, which the next start aborts (runs.recover_orphaned_campaigns),
        so it is never scored. (Profiled: WAL flush
        waits were ~15% of backend time at concurrency 8.)"""
        clock = sim_now.isoformat() if sim_now is not None else ""
        if AGENT_ROLE.match(role):
            async with (await self._agent_pool(role)).connection() as conn:
                await conn.execute("SELECT set_config('walltest.sim_now', %s, true), set_config('synchronous_commit', 'off', true)", (clock,))
                yield conn
            return
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        async with self._pool.connection() as conn:
            # one round trip: role, (simulated) clock and commit mode, all transaction-local. set_config('role') runs the same
            # membership check as SET ROLE.
            await conn.execute("SELECT set_config('role', %s, true), set_config('walltest.sim_now', %s, true), "
                               "set_config('synchronous_commit', %s, true)", (role, clock, "on" if durable else "off"))
            yield conn

    async def fetch(self, role: str, sql: str, params=None) -> list[dict]:
        async with self.session(role) as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall() if cur.description else []
