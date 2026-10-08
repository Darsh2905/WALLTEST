"""Async connection pool + per-request role switching.

The pool logs in as `walltest_api`: not a superuser, not a table owner, NOINHERIT (no privileges of its own). Every unit
of work runs inside a transaction that begins with SET LOCAL ROLE <one of four roles>, so RLS and GRANTs apply exactly
as they would for that role, and the role reverts automatically at COMMIT/ROLLBACK (safe with pooled connections)."""
from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

ROLES = frozenset({"high_side", "low_side", "audit_engine", "compliance"})


class Database:
    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 16):
        self._pool = AsyncConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False,
                                         kwargs={"row_factory": dict_row}, name="walltest-api")

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def session(self, role: str, sim_now: dt.datetime | None = None):
        """A transaction running as `role`. sim_now sets the session clock used by SIMULATED campaigns' gateway calls."""
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        async with self._pool.connection() as conn:
            await conn.execute(f"SET LOCAL ROLE {role}")
            if sim_now is not None:
                await conn.execute("SELECT set_config('walltest.sim_now', %s, true)", (sim_now.isoformat(),))
            yield conn

    async def fetch(self, role: str, sql: str, params=None) -> list[dict]:
        async with self.session(role) as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall() if cur.description else []
