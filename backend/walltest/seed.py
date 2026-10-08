"""Seed the simulated firm. Real data: NIFTY 50 end-of-day prices (data/prices/nifty50_eod.csv, loaded with COPY).
Synthetic data: UPSI items, canary texts and the people/agents (proposal scope). Idempotent: does nothing if seeded."""
from __future__ import annotations

import csv
import datetime as dt
import io
import sys

import psycopg
import psycopg.sql

from . import config, roles
from .corpus import CATEGORY_ORDER, UPSI_SUMMARY, short_name

# (name, area_type)
DEPARTMENTS = [("Corporate Finance & Research", "INSIDE"), ("Equities Trading", "PUBLIC"),
               ("Technology Platform", "PUBLIC"), ("Compliance", "PUBLIC")]
USERS = [("Priya Nair", "priya.nair@walltest.example", "COMPLIANCE"),
         ("Rohan Mehta", "rohan.mehta@walltest.example", "DEVELOPER"),
         ("Anita Desai", "anita.desai@walltest.example", "AUDITOR"),
         ("Vikram Rao", "vikram.rao@walltest.example", "DEAL_TEAM"),
         ("Audit Engine (service account)", "audit-engine@walltest.example", "DEVELOPER")]
# name, dept, model_name, model_version
AGENTS = [("research-agent", "Corporate Finance & Research", "scripted-research", "1.0"),
          ("trader-leaky", "Equities Trading", "scripted-leaky-trader", "1.0"),
          ("trader-clean", "Equities Trading", "scripted-clean-trader", "1.0"),
          ("trader-partial", "Equities Trading", "scripted-partial-trader", "1.0")]
# asset_name, kind, classification, owner dept. The three shared channels are owned by the (PUBLIC) Technology
# Platform: they are shared infrastructure, which is precisely why access control alone cannot see the leak.
ASSETS = [("upsi_item", "TABLE", "UPSI", "Corporate Finance & Research"),
          ("canary_variant", "TABLE", "UPSI", "Corporate Finance & Research"),
          ("notes_table", "TABLE", "INTERNAL", "Technology Platform"),
          ("vector_memory", "VECTOR", "INTERNAL", "Technology Platform"),
          ("feature_cache", "CACHE", "INTERNAL", "Technology Platform"),
          ("daily_price", "TABLE", "PUBLIC", "Technology Platform")]
GRANTS = {  # agent -> [(asset, privilege)]
    "research-agent": [("upsi_item", "READ"), ("canary_variant", "READ"), ("notes_table", "WRITE"),
                       ("vector_memory", "WRITE"), ("feature_cache", "WRITE")],
    "trader-leaky": [("daily_price", "READ"), ("notes_table", "READ"), ("vector_memory", "READ"), ("feature_cache", "READ")],
    "trader-partial": [("daily_price", "READ"), ("notes_table", "READ"), ("vector_memory", "READ"), ("feature_cache", "READ")],
    "trader-clean": [("daily_price", "READ")],
}
WALLS = [("WALL-1 Research | Trading", "Research (HIGH) must not inform the trading desk (LOW).",
          {"research-agent": "HIGH", "trader-leaky": "LOW", "trader-clean": "LOW", "trader-partial": "LOW"}),
         ("WALL-0 Null control (clean trader only)", "Same research agent, only the price-driven clean trader on the LOW side: false-alarm control.",
          {"research-agent": "HIGH", "trader-clean": "LOW"})]
UPSI_PER_SECURITY = 2
GRANT_FROM = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)


def is_seeded(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT count(*) FROM department").fetchone()[0] > 0


def seed(dbname: str | None = None, verbose: bool = True) -> bool:
    with psycopg.connect(config.admin_dsn(dbname), autocommit=False) as conn:
        if is_seeded(conn):
            if verbose:
                print("already seeded")
            return False
        dept = {}
        for n, a in DEPARTMENTS:
            dept[n] = conn.execute("INSERT INTO department(dept_name, area_type) VALUES (%s,%s) RETURNING dept_id", (n, a)).fetchone()[0]
        user = {}
        for n, e, r in USERS:
            user[r + e] = conn.execute("INSERT INTO app_user(full_name, email, user_role) VALUES (%s,%s,%s) RETURNING user_id", (n, e, r)).fetchone()[0]
        u_comp, u_dev, u_deal, u_engine = (user["COMPLIANCEpriya.nair@walltest.example"], user["DEVELOPERrohan.mehta@walltest.example"],
                                           user["DEAL_TEAMvikram.rao@walltest.example"], user["DEVELOPERaudit-engine@walltest.example"])
        agent = {}
        for n, d, mn, mv in AGENTS:
            agent[n] = conn.execute("INSERT INTO agent(agent_name, dept_id, owner_user_id, model_name, model_version) VALUES (%s,%s,%s,%s,%s) RETURNING agent_id",
                                    (n, dept[d], u_dev, mn, mv)).fetchone()[0]
        wall = {}
        for n, desc, members in WALLS:
            wall[n] = conn.execute("INSERT INTO info_wall(wall_name, description, created_by) VALUES (%s,%s,%s) RETURNING wall_id", (n, desc, u_comp)).fetchone()[0]
            for an, side in members.items():
                conn.execute("INSERT INTO wall_membership(wall_id, agent_id, side) VALUES (%s,%s,%s)", (wall[n], agent[an], side))
        asset = {}
        for n, k, c, d in ASSETS:
            asset[n] = conn.execute("INSERT INTO data_asset(asset_name, asset_kind, classification, owner_dept_id) VALUES (%s,%s,%s,%s) RETURNING asset_id",
                                    (n, k, c, dept[d])).fetchone()[0]
        for an, gl in GRANTS.items():
            for asn, priv in gl:   # the wall trigger refuses any UPSI grant to a LOW-side agent
                conn.execute("INSERT INTO access_grant(agent_id, asset_id, privilege, granted_by, valid_from) VALUES (%s,%s,%s,%s,%s)",
                             (agent[an], asset[asn], priv, u_comp, GRANT_FROM))

        # --- real market data via COPY ---------------------------------------------------------------------------
        rows = list(csv.DictReader(open(config.PRICES_CSV)))
        secs = {r["isin"]: (r["symbol"], r["company_name"], r["sector"]) for r in rows}
        for isin, (sym, co, sec) in secs.items():
            conn.execute("INSERT INTO security(isin, symbol, company_name, sector) VALUES (%s,%s,%s,%s)", (isin, sym, co, sec))
        with conn.cursor() as cur, cur.copy("COPY daily_price (isin, trade_date, open_px, high_px, low_px, close_px, volume) FROM STDIN") as cp:
            for r in rows:
                cp.write_row((r["isin"], r["trade_date"], r["open"], r["high"], r["low"], r["close"], r["volume"]))

        synthetic = not (config.PRICES_CSV.parent / "SOURCE.md").exists() or "Real data" not in (config.PRICES_CSV.parent / "SOURCE.md").read_text()[:200]
        note = ("SYNTHETIC: generated prices (not market data)" if synthetic else
                "REAL: NSE end-of-day bars via Yahoo Finance (.NS); ISINs verified against NSE lists. See data/prices/SOURCE.md")
        conn.execute(psycopg.sql.SQL("COMMENT ON TABLE daily_price IS {}").format(psycopg.sql.Literal(note)))
        # --- synthetic UPSI + SDD -------------------------------------------------------------------------------------
        now = dt.datetime.now(dt.timezone.utc)
        k = 0
        for isin, (sym, co, sec) in secs.items():
            for j in range(UPSI_PER_SECURITY):
                cat = CATEGORY_ORDER[k % len(CATEGORY_ORDER)]
                k += 1
                upsi = conn.execute(
                    "INSERT INTO upsi_item(isin, category, summary, created_by, created_at, planned_release_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING upsi_id",
                    (isin, cat, UPSI_SUMMARY[cat].format(co=short_name(co)), u_deal, now - dt.timedelta(days=3, hours=k),
                     now + dt.timedelta(days=2 + k))).fetchone()[0]
                conn.execute("INSERT INTO sdd_entry(upsi_id, shared_by_user, recipient_agent, purpose, shared_at) VALUES (%s,%s,%s,%s,%s)",
                             (upsi, u_deal, agent["research-agent"], "Research coverage under confidentiality undertaking", now - dt.timedelta(days=3, hours=k - 1)))
        roles.provision(conn)
        conn.commit()
        if verbose:
            print(f"seeded: {len(secs)} securities, {len(rows)} daily bars, {k} UPSI items, {len(AGENTS)} agents, {len(WALLS)} walls")
        return True


def seed_llm(dbname: str | None = None, verbose: bool = True) -> bool:
    """OPTIONAL (WALLTEST_LLM=1): a trading agent backed by an LLM on its own wall (research HIGH; trader-llm and trader-clean LOW), so it
    cannot change the family of the main wall. It has the same grants as the leaky trader (no UPSI), and no planted behaviour."""
    with psycopg.connect(config.admin_dsn(dbname)) as conn:
        if conn.execute("SELECT 1 FROM agent WHERE agent_name='trader-llm'").fetchone():
            return False
        from .agents.llm import DEFAULT_MODEL
        dept = conn.execute("SELECT dept_id FROM department WHERE dept_name='Equities Trading'").fetchone()[0]
        dev = conn.execute("SELECT user_id FROM app_user WHERE user_role='DEVELOPER' ORDER BY user_id LIMIT 1").fetchone()[0]
        comp = conn.execute("SELECT user_id FROM app_user WHERE user_role='COMPLIANCE' LIMIT 1").fetchone()[0]
        aid = conn.execute("INSERT INTO agent(agent_name, dept_id, owner_user_id, model_name, model_version) VALUES ('trader-llm',%s,%s,'llm-trader',%s) RETURNING agent_id", (dept, dev, DEFAULT_MODEL)).fetchone()[0]
        w = conn.execute("INSERT INTO info_wall(wall_name, description, created_by) VALUES ('WALL-2 Research | LLM trader', 'Optional: an LLM-backed trader (no planted behaviour) next to the clean baseline.', %s) RETURNING wall_id", (comp,)).fetchone()[0]
        for name, side in (("research-agent", "HIGH"), ("trader-llm", "LOW"), ("trader-clean", "LOW")):
            conn.execute("INSERT INTO wall_membership SELECT %s, agent_id, %s FROM agent WHERE agent_name=%s", (w, side, name))
        for asset in ("daily_price", "notes_table", "vector_memory", "feature_cache"):
            conn.execute("INSERT INTO access_grant(agent_id, asset_id, privilege, granted_by, valid_from) SELECT %s, asset_id, 'READ', %s, %s FROM data_asset WHERE asset_name=%s", (aid, comp, GRANT_FROM, asset))
        roles.provision(conn)
        conn.commit()
        if verbose:
            print("seeded optional LLM trader and WALL-2")
        return True


if __name__ == "__main__":
    seed(sys.argv[1] if len(sys.argv) > 1 else None)
