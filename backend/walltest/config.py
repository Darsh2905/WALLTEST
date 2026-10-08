"""Environment-driven configuration. Three distinct logins, none of which is a superuser or table owner
except the admin login used ONLY by migrations/seeding (never by the API request path)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "db" / "migrations"
PRICES_CSV = ROOT / "data" / "prices" / "nifty50_eod.csv"
FRONTEND_DIST = ROOT / "frontend" / "dist"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


DB_HOST = _env("WALLTEST_DB_HOST", "localhost")
DB_PORT = _env("WALLTEST_DB_PORT", "5433")
DB_NAME = _env("WALLTEST_DB_NAME", "walltest")
ADMIN_USER = _env("WALLTEST_ADMIN_USER", "walltest_admin")
ADMIN_PASSWORD = _env("WALLTEST_ADMIN_PASSWORD", "walltest_admin_pw")
API_PASSWORD = _env("WALLTEST_API_PASSWORD", "walltest_api_pw")
LAB_PASSWORD = _env("WALLTEST_LAB_PASSWORD", "walltest_lab_pw")
LLM_ENABLED = _env("WALLTEST_LLM", "0") == "1"


def dsn(user: str, password: str, dbname: str | None = None) -> str:
    return f"host={DB_HOST} port={DB_PORT} dbname={dbname or DB_NAME} user={user} password={password}"


def admin_dsn(dbname: str | None = None) -> str:
    return dsn(ADMIN_USER, ADMIN_PASSWORD, dbname)


def api_dsn(dbname: str | None = None) -> str:
    return dsn("walltest_api", API_PASSWORD, dbname)


def lab_dsn(dbname: str | None = None) -> str:
    return dsn("walltest_lab", LAB_PASSWORD, dbname)
