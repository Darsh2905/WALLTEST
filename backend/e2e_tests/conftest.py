"""Playwright end-to-end: a real uvicorn server (built UI + API) on a fresh database, a real Chromium, a real LIVE campaign."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from walltest import config, migrate, seed  # noqa: E402

E2E_DB = "walltest_e2e"
SHOTS = BACKEND.parent / "docs" / "screenshots"


@pytest.fixture(scope="session")
def base_url():
    if not (config.FRONTEND_DIST / "index.html").exists():
        pytest.skip("frontend is not built (cd frontend && npm run build)")
    migrate.drop_database(E2E_DB)
    migrate.create_database_if_missing(E2E_DB)
    migrate.migrate(E2E_DB, verbose=False)
    seed.seed(E2E_DB, verbose=False)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "WALLTEST_DB_NAME": E2E_DB}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "walltest.api:app", "--port", str(port), "--log-level", "warning"], cwd=BACKEND, env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    yield url
    p.terminate()
    p.wait(timeout=15)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.base = base_url
    yield pg
    ctx.close()
