#!/usr/bin/env python3
"""Drop and re-create the walltest database, then migrate and seed. Usage: python scripts/reset_db.py [dbname]"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from walltest import config, migrate, seed  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else config.DB_NAME
migrate.drop_database(name)
migrate.create_database_if_missing(name)
migrate.migrate(name)
seed.seed(name)
