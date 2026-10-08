"""Wait for PostgreSQL, apply migrations, seed. Used by `docker compose up` (the api container) and `make demo`."""
from __future__ import annotations

import sys
import time

import psycopg

from . import config, migrate, seed


def wait_for_db(timeout: float = 90) -> None:
    t0 = time.time()
    while True:
        try:
            psycopg.connect(config.admin_dsn(), connect_timeout=3).close()
            return
        except Exception as e:  # noqa: BLE001
            if time.time() - t0 > timeout:
                raise SystemExit(f"database not reachable at {config.DB_HOST}:{config.DB_PORT}: {e}")
            time.sleep(1)


def main() -> None:
    wait_for_db()
    applied = migrate.migrate(verbose=False)
    print(f"migrations applied now: {applied or 'none (up to date)'}")
    seeded = seed.seed(verbose=False)
    print("seeded" if seeded else "already seeded")
    if config.LLM_ENABLED:
        seed.seed_llm()


if __name__ == "__main__":
    sys.exit(main())
