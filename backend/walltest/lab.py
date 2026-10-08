"""DB Rules Lab: fixed buttons (NO free-form SQL) that attempt forbidden operations under the REAL roles.

Each case is a fixed script of steps. Every step runs in a savepoint inside ONE transaction that is ALWAYS rolled back, so the
lab leaves no trace. Errors are PostgreSQL's own text (message, SQLSTATE, hint), never paraphrased.

The lab connects as `walltest_lab`, a login that may SET ROLE to any of the four roles and to the table owner (the owner is used
only for steps marked as such, to show the TRIGGER layer after the PRIVILEGE layer has already refused)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row

from . import config


@dataclass
class Step:
    role: str
    sql: str
    caption: str
    setup: bool = False
    expect: str = "error"           # 'error' | 'rows' | 'zero_rows' | 'ok'


@dataclass
class Case:
    id: str
    title: str
    doc: str
    blurb: str
    steps: list[Step] = field(default_factory=list)


# helper SQL used by several cases ---------------------------------------------------------------------------------------------
NEW_CAMPAIGN = ("SELECT create_campaign((SELECT wall_id FROM info_wall WHERE wall_name LIKE 'WALL-1%'), "
                "(SELECT user_id FROM app_user WHERE user_role='COMPLIANCE' LIMIT 1), 0.05, 4, 'ALL_ON', 'LIVE', '{}'::jsonb) AS campaign_id")
START = "SELECT start_campaign((SELECT max(campaign_id) FROM audit_campaign))"


def commit_slot_sql(offset_start: str, length: str, *, flip_in_commitment: int, flip_sealed: int) -> str:
    """Commit one LIVE slot starting `offset_start` from now. The published commitment hashes flip_in_commitment; the sealed flip is flip_sealed."""
    return f"""WITH p AS (
  SELECT (SELECT max(campaign_id) FROM audit_campaign) AS cid, clock_timestamp() + interval '{offset_start}' AS t0, gen_random_bytes(32) AS salt
)
SELECT engine_commit_slot(p.cid, (SELECT min(treatment_id) FROM treatment WHERE campaign_id = p.cid),
       (SELECT min(upsi_id) FROM upsi_item), tstzrange(p.t0, p.t0 + interval '{length}', '[)'),
       commitment_hash(p.cid, p.t0, {flip_in_commitment}::smallint, p.salt), clock_timestamp(),
       {flip_sealed}::smallint, p.salt, 1::smallint,
       'Company X will report profit well above consensus.', 'Company X will report profit well below consensus.') AS slot_id
FROM p"""


CASES: list[Case] = [
    Case("update_trade_order", "UPDATE trade_order", "8.4 append-only triggers + revoked privileges",
         "Orders are evidence. Two independent layers refuse a rewrite: the privilege is not granted, and a trigger raises even for the table owner.",
         [Step("walltest_owner", "INSERT INTO trade_order (agent_id, isin, side, quantity, placed_at) "
                                 "SELECT (SELECT agent_id FROM agent WHERE agent_name='trader-clean'), isin, 'BUY', 100, now() FROM security LIMIT 1",
               "Setup: insert one order (inserts are allowed).", setup=True, expect="ok"),
          Step("audit_engine", "UPDATE trade_order SET quantity = 1", "Layer 1, privilege: the audit engine has no UPDATE grant."),
          Step("walltest_owner", "UPDATE trade_order SET quantity = 1", "Layer 2, trigger: even the table owner is refused."),
          Step("walltest_owner", "TRUNCATE trade_order CASCADE", "TRUNCATE is blocked by a statement-level trigger too.")]),
    Case("delete_access_event", "DELETE access_event", "8.4 append-only triggers + revoked privileges",
         "The audit trail cannot be edited after the fact, by anyone.",
         [Step("walltest_owner", "INSERT INTO access_event (agent_id, asset_id, op, row_ref, event_time, txn_id) "
                                 "SELECT (SELECT agent_id FROM agent WHERE agent_name='trader-leaky'), (SELECT asset_id FROM data_asset WHERE asset_name='notes_table'), "
                                 "'READ', 'agent_note:1', now(), txid_current()", "Setup: log one access event.", setup=True, expect="ok"),
          Step("compliance", "DELETE FROM access_event", "Layer 1, privilege: compliance can read the trail, not delete from it."),
          Step("walltest_owner", "DELETE FROM access_event", "Layer 2, trigger: refused for the owner as well.")]),
    Case("grant_upsi_to_low", "Grant a UPSI asset to a LOW-side agent", "8.4 wall trigger",
         "The wall trigger refuses any grant of a UPSI-classified asset to an agent that sits on the LOW side of a wall.",
         [Step("compliance", "INSERT INTO access_grant (agent_id, asset_id, privilege, granted_by, valid_from) "
                             "SELECT (SELECT agent_id FROM agent WHERE agent_name='trader-clean'), (SELECT asset_id FROM data_asset WHERE asset_name='upsi_item'), "
                             "'READ', (SELECT user_id FROM app_user WHERE user_role='COMPLIANCE' LIMIT 1), now()",
               "A compliance officer tries to grant trader-clean (LOW) read access to upsi_item (UPSI).")]),
    Case("overlapping_slot", "Overlapping slot", "8.4 exclusion constraint (EXCLUDE USING gist)",
         "No two slots of a campaign may overlap in time (and, because orders carry no campaign id, no two slots of any campaign).",
         [Step("compliance", NEW_CAMPAIGN, "Setup: create a campaign.", setup=True, expect="rows"),
          Step("compliance", START, "Setup: start it.", setup=True, expect="ok"),
          Step("audit_engine", commit_slot_sql("1 hour", "5 seconds", flip_in_commitment=0, flip_sealed=0), "Commit a slot [T, T+5s): accepted.", expect="rows"),
          Step("audit_engine", commit_slot_sql("1 hour 3 seconds", "5 seconds", flip_in_commitment=0, flip_sealed=0),
               "Commit a second slot starting at T+3s: overlaps the first.")]),
    Case("flip_mismatch", "Flip not matching its commitment", "8.4 commitment trigger",
         "The sealed flip and salt must hash to the commitment published with the slot, otherwise the insert is rejected.",
         [Step("compliance", NEW_CAMPAIGN, "Setup: create a campaign.", setup=True, expect="rows"),
          Step("compliance", START, "Setup: start it.", setup=True, expect="ok"),
          Step("audit_engine", commit_slot_sql("1 hour", "2 seconds", flip_in_commitment=0, flip_sealed=1),
               "Publish a commitment to flip = 0, then try to seal flip = 1.")]),
    Case("sdd_two_sharers", "SDD row with two sharers", "8.4 row CHECK num_nonnulls(...) = 1",
         "An SDD entry records exactly one sharer and exactly one recipient.",
         [Step("compliance", "INSERT INTO sdd_entry (upsi_id, shared_by_user, shared_by_agent, recipient_agent, purpose) "
                             "SELECT (SELECT min(upsi_id) FROM sdd_entry), (SELECT min(user_id) FROM app_user), (SELECT min(agent_id) FROM agent), "
                             "(SELECT min(agent_id) FROM agent), 'two sharers'", "Insert a row naming both a user and an agent as the sharer.")]),
    Case("low_side_select_upsi", "low_side SELECT on upsi_item", "8.4 role privileges + row-level security",
         "The wall is enforced inside the database. First the privilege refuses; then, even if a privilege leaked, RLS still returns nothing.",
         [Step("low_side", "SELECT * FROM upsi_item", "A trading agent reads the UPSI table."),
          Step("walltest_owner", "GRANT SELECT ON upsi_item TO low_side", "Setup: simulate a privilege leak (rolled back).", setup=True, expect="ok"),
          Step("low_side", "SELECT count(*) AS visible_rows FROM upsi_item", "Same read with the privilege present: the RLS policy still hides every row.", expect="zero_rows"),
          Step("high_side", "SELECT count(*) AS visible_rows FROM upsi_item", "Control: the research side sees the rows.", expect="rows")]),
    Case("engine_reads_unended_flip", "audit_engine reads a not-yet-ended flip", "8.4 RLS: flips stay secret until the slot ends",
         "Even the audit engine that drew the flip cannot read it back before the slot ends.",
         [Step("compliance", NEW_CAMPAIGN, "Setup: create a campaign.", setup=True, expect="rows"),
          Step("compliance", START, "Setup: start it.", setup=True, expect="ok"),
          Step("audit_engine", commit_slot_sql("1 hour", "2 seconds", flip_in_commitment=1, flip_sealed=1), "Commit a slot that opens in one hour.", expect="rows"),
          Step("audit_engine", "SELECT count(*) AS visible_flips FROM sealed_flip", "SELECT the sealed flip: RLS filters it out silently (no error is raised by design).", expect="zero_rows"),
          Step("audit_engine", "SELECT * FROM engine_reveal((SELECT max(slot_id) FROM canary_slot))", "engine_reveal() says why, explicitly."),
          Step("walltest_owner", "SELECT count(*) AS flips_that_exist FROM sealed_flip", "Control: the row exists (the owner bypasses RLS).", expect="rows")]),
    Case("commit_after_open", "Commit after the slot has opened", "5 commit-before-expose",
         "A commitment published after its slot opened proves nothing, so the trigger refuses it (the server clock stamps committed_at).",
         [Step("compliance", NEW_CAMPAIGN, "Setup: create a campaign.", setup=True, expect="rows"),
          Step("compliance", START, "Setup: start it.", setup=True, expect="ok"),
          Step("audit_engine", commit_slot_sql("-1 second", "2 seconds", flip_in_commitment=0, flip_sealed=0), "Commit a slot whose start is already in the past.")]),
    Case("freeze_early", "Freeze a verdict before the planned n", "no peeking",
         "The verdict is computed once, at the planned n. Asking for it earlier is refused.",
         [Step("compliance", NEW_CAMPAIGN, "Setup: create a campaign.", setup=True, expect="rows"),
          Step("compliance", START, "Setup: start it.", setup=True, expect="ok"),
          Step("audit_engine", "SELECT freeze_campaign((SELECT max(campaign_id) FROM audit_campaign), "
                               "(SELECT user_id FROM app_user WHERE email='audit-engine@walltest.example'))",
               "Freeze a campaign with no scored slots.")]),
]
BY_ID = {c.id: c for c in CASES}


def catalogue() -> list[dict]:
    return [{"id": c.id, "title": c.title, "doc": c.doc, "blurb": c.blurb,
             "steps": [{"role": s.role, "sql": s.sql, "caption": s.caption, "setup": s.setup} for s in c.steps]} for c in CASES]


def _jsonable(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def run_case(case_id: str, dbname: str | None = None) -> dict:
    case = BY_ID[case_id]
    out_steps = []
    t0 = time.perf_counter()
    with psycopg.connect(config.lab_dsn(dbname), row_factory=dict_row, autocommit=False) as conn:
        # Open the OUTER transaction explicitly. Without this, the first `conn.transaction()` on an idle connection is a real
        # transaction (BEGIN/COMMIT) rather than a savepoint, and the "rolled back" lab would silently commit its writes.
        conn.execute("SELECT 1")
        try:
            for st in case.steps:
                rec = {"role": st.role, "sql": st.sql, "caption": st.caption, "setup": st.setup}
                try:
                    with conn.transaction():                       # savepoint per step
                        conn.execute(f"SET LOCAL ROLE {st.role}")
                        cur = conn.execute(st.sql)
                        rows = cur.fetchall() if cur.description else []
                        conn.execute("RESET ROLE")
                    rec.update(ok=True, rows=[{k: _jsonable(v) for k, v in r.items()} for r in rows[:20]], rowcount=len(rows))
                except psycopg.Error as e:
                    d = e.diag
                    rec.update(ok=False, error={"message": d.message_primary or str(e), "sqlstate": e.sqlstate, "detail": d.message_detail,
                                                "hint": d.message_hint, "context": d.context, "constraint": d.constraint_name,
                                                "table": d.table_name, "full": str(e).strip()})
                out_steps.append(rec)
        finally:
            conn.rollback()                                        # ALWAYS rolled back
    return {"id": case.id, "title": case.title, "doc": case.doc, "blurb": case.blurb, "rolled_back": True,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "steps": out_steps}
