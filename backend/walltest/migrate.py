"""Apply db/migrations/*.sql in order. 000 runs as the bootstrap superuser; every later file runs as
walltest_owner so that no table is owned by a superuser. Applied files are recorded in ops.migration_log
(outside `public`, so the application schema is exactly the proposal's 20 tables)."""
from __future__ import annotations

import sys

import psycopg
from psycopg import sql

from . import config, roles


def create_database_if_missing(name: str) -> None:
    with psycopg.connect(config.admin_dsn("postgres"), autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def drop_database(name: str) -> None:
    with psycopg.connect(config.admin_dsn("postgres"), autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def set_passwords(conn: psycopg.Connection) -> None:
    for role, pw in (("walltest_api", config.API_PASSWORD), ("walltest_lab", config.LAB_PASSWORD)):
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(pw)))


def migrate(dbname: str | None = None, verbose: bool = True) -> list[str]:
    applied: list[str] = []
    files = sorted(config.MIGRATIONS_DIR.glob("*.sql"))
    with psycopg.connect(config.admin_dsn(dbname), autocommit=True) as conn:
        have_log = conn.execute("SELECT to_regclass('ops.migration_log')").fetchone()[0] is not None
        done = set()
        if have_log:
            done = {r[0] for r in conn.execute("SELECT filename FROM ops.migration_log")}
        for f in files:
            if f.name in done:
                continue
            body = f.read_text()
            with conn.transaction():
                if not f.name.startswith("000"):
                    conn.execute("SET LOCAL ROLE walltest_owner")
                try:
                    conn.execute(body)
                except Exception as e:
                    raise RuntimeError(f"migration {f.name} failed: {e}") from e
                if f.name.startswith("000"):
                    conn.execute("CREATE TABLE IF NOT EXISTS ops.migration_log ("
                                 "filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
                    conn.execute("ALTER TABLE ops.migration_log OWNER TO walltest_owner")
                    conn.execute("SET LOCAL ROLE walltest_owner")
                conn.execute("INSERT INTO ops.migration_log(filename) VALUES (%s)", (f.name,))
            applied.append(f.name)
            if verbose:
                print(f"applied {f.name}")
        set_passwords(conn)
        with conn.transaction():          # per-agent roles (015) for agents seeded before the migration; no-op on an empty DB
            roles.provision(conn)
    return applied


if __name__ == "__main__":
    migrate(sys.argv[1] if len(sys.argv) > 1 else None)
