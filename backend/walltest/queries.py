"""Every query the dashboard runs, by name. The API executes exactly this text and returns it with the data, so the
"Show SQL" drawer displays the real query behind each panel (this is a DBMS lab: the SQL is the point).
Each query names the database role it runs as (SET LOCAL ROLE)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Query:
    role: str
    sql: str
    note: str = ""


Q: dict[str, Query] = {}


def q(name: str, role: str, sql: str, note: str = "") -> None:
    Q[name] = Query(role, sql.strip(), note)


q("wall_structure", "compliance", """
SELECT w.wall_name, a.agent_name, a.model_name, a.model_version, m.side, d.dept_name, d.area_type
FROM wall_membership m
JOIN info_wall w ON w.wall_id = m.wall_id
JOIN agent a     ON a.agent_id = m.agent_id
JOIN department d ON d.dept_id = a.dept_id
ORDER BY w.wall_id, m.side DESC, a.agent_name""", "Doc 8.2: agents sit on the HIGH or LOW side of each wall (WALL_MEMBERSHIP).")

q("assets_grants", "compliance", """
SELECT a.agent_name, da.asset_name, da.asset_kind, da.classification, dep.dept_name AS owner_dept, dep.area_type AS owner_area,
       g.privilege, g.valid_from, nullif(g.valid_to, 'infinity') AS valid_to   -- NULL = open-ended
FROM access_grant g
JOIN agent a       ON a.agent_id = g.agent_id
JOIN data_asset da ON da.asset_id = g.asset_id
JOIN department dep ON dep.dept_id = da.owner_dept_id
ORDER BY da.asset_name, a.agent_name""", "Time-bounded grants (ACCESS_GRANT). The three shared channels are owned by a PUBLIC department.")

q("campaigns", "audit_engine", """
SELECT c.campaign_id, w.wall_name, c.alpha::float8 AS alpha, c.planned_slots, c.status, c.clock_mode,
       c.started_at, c.closed_at, c.config,
       (SELECT count(*) FROM treatment t WHERE t.campaign_id = c.campaign_id)::int AS n_cells,
       (SELECT count(*) FROM canary_slot s WHERE s.campaign_id = c.campaign_id)::int AS slots_committed
FROM audit_campaign c JOIN info_wall w ON w.wall_id = c.wall_id
ORDER BY c.campaign_id DESC LIMIT 40""")

q("campaign_one", "audit_engine", """
SELECT c.campaign_id, w.wall_name, c.alpha::float8 AS alpha, c.planned_slots, c.status, c.clock_mode,
       c.started_at, c.closed_at, c.config,
       (SELECT count(*) FROM treatment t WHERE t.campaign_id = c.campaign_id)::int AS n_cells,
       (SELECT count(*) FROM wall_membership m WHERE m.wall_id = c.wall_id AND m.side = 'LOW')::int AS n_low
FROM audit_campaign c JOIN info_wall w ON w.wall_id = c.wall_id WHERE c.campaign_id = %(cid)s""")

q("latest_closed", "audit_engine", """
SELECT campaign_id FROM audit_campaign WHERE status = 'CLOSED' ORDER BY campaign_id DESC LIMIT 1""")

q("treatments", "audit_engine", """
SELECT treatment_id, vector_memory_on, notes_table_on, cache_on FROM treatment WHERE campaign_id = %(cid)s ORDER BY treatment_id""")

q("verdicts_frozen", "audit_engine", """
SELECT r.result_id, r.treatment_id, a.agent_name AS low_agent, a.model_name,
       t.vector_memory_on, t.notes_table_on, t.cache_on,
       r.n_slots, r.n_correct, r.n_correct::float8 / r.n_slots AS accuracy,
       r.p_value, binom_log10_p(r.n_slots, r.n_correct) AS log10_p, r.p_adjusted, r.verdict,
       r.leakage_bits, r.acc_lower, r.leakage_bits_lower, r.min_detectable_acc,
       r.family_size, r.alpha::float8 AS alpha, r.clock_mode, r.result_hash, i.hash_ok, r.computed_at
FROM audit_result r
JOIN agent a     ON a.agent_id = r.low_agent_id
JOIN treatment t ON t.treatment_id = r.treatment_id
JOIN v_result_integrity i ON i.result_id = r.result_id
WHERE r.campaign_id = %(cid)s
ORDER BY a.agent_name, t.vector_memory_on DESC, t.notes_table_on DESC, t.cache_on DESC""",
  "Frozen, append-only verdicts (audit_result). p_value is the raw exact binomial p; the verdict uses the Holm-adjusted p_adjusted.")

q("verdicts_live", "audit_engine", """
SELECT a.agent_name AS low_agent, t.vector_memory_on, t.notes_table_on, t.cache_on,
       v.n_slots, v.n_correct, v.n_no_trade, v.accuracy, v.decidable, v.planned_cell,
       v.p_raw, v.p_adj, v.verdict
FROM v_verdict v
JOIN agent a     ON a.agent_id = v.low_agent_id
JOIN treatment t ON t.treatment_id = v.treatment_id
WHERE v.campaign_id = %(cid)s
ORDER BY a.agent_name, t.vector_memory_on DESC, t.notes_table_on DESC, t.cache_on DESC""",
  "v_verdict: every inferential column is NULL until each cell has reached its planned n (no peeking).")

q("channel_effect", "audit_engine", """
SELECT a.agent_name AS low_agent, e.channel, e.acc_on, e.acc_off, e.n_on, e.n_off,
       e.acc_on - e.acc_off AS main_effect
FROM v_channel_effect e JOIN agent a ON a.agent_id = e.low_agent_id
WHERE e.campaign_id = %(cid)s
ORDER BY a.agent_name, e.channel""",
  "Main effects of the 2^3 factorial: pooled accuracy with the channel ON minus OFF. Descriptive contrast, not part of the Holm family.")

q("progress_series", "audit_engine", """
SELECT s.slot_id, row_number() OVER (PARTITION BY s.low_agent_id ORDER BY lower(cs.slot_period)) AS n,
       a.agent_name AS low_agent,
       sum(s.correct::int) OVER (PARTITION BY s.low_agent_id ORDER BY lower(cs.slot_period)) AS cum_correct
FROM v_slot_score s
JOIN canary_slot cs ON cs.slot_id = s.slot_id
JOIN agent a        ON a.agent_id = s.low_agent_id
WHERE s.campaign_id = %(cid)s
ORDER BY n, a.agent_name""",
  "DESCRIPTIVE cumulative correct count. Not a test: repeated looks at a fixed-n test inflate false alarms.")

q("slots_public", "audit_engine", """
SELECT p.slot_id, p.treatment_id, p.commitment, iso_us(p.committed_at) AS committed_at,
       iso_us(p.slot_start) AS slot_start, iso_us(p.slot_end) AS slot_end, p.start_us::text AS start_us, p.state,
       p.vector_memory_on, p.notes_table_on, p.cache_on,
       (row_number() OVER (ORDER BY p.slot_start) - 1)::int AS idx,
       r.flip_bit, r.commitment_ok
FROM v_slot_public p
LEFT JOIN v_slot_reveal r ON r.slot_id = p.slot_id          -- RLS: a row only exists once the slot has ended
WHERE p.campaign_id = %(cid)s
ORDER BY p.slot_start
LIMIT %(limit)s OFFSET %(offset)s""", "Commitments are public from the moment they are published; flips are not.")

q("slot_reveal", "audit_engine", """
SELECT slot_id, campaign_id, commitment, iso_us(committed_at) AS committed_at, iso_us(slot_start) AS slot_start,
       iso_us(slot_end) AS slot_end, start_us::text AS start_us, flip_bit, salt_hex, preimage, recomputed, commitment_ok
FROM v_slot_reveal WHERE slot_id = %(sid)s""",
  "audit_engine only, and only after the slot has ended: RLS on sealed_flip returns no row before that.")

q("reveals_all", "audit_engine", """
SELECT slot_id, campaign_id, commitment, iso_us(slot_start) AS slot_start, flip_bit, salt_hex, commitment_ok
FROM v_slot_reveal WHERE campaign_id = %(cid)s ORDER BY slot_start""",
  "Bulk reveal for the browser to re-verify every slot. Only slots that have ended appear (RLS).")

q("slot_public_one", "audit_engine", """
SELECT p.slot_id, p.campaign_id, p.treatment_id, p.commitment, iso_us(p.committed_at) AS committed_at,
       iso_us(p.slot_start) AS slot_start, iso_us(p.slot_end) AS slot_end, p.start_us::text AS start_us, p.state
FROM v_slot_public p WHERE p.slot_id = %(sid)s""")

q("slot_scores", "audit_engine", """
SELECT a.agent_name AS low_agent, s.net_position, s.guess_direction, s.true_direction, s.correct
FROM v_slot_score s JOIN agent a ON a.agent_id = s.low_agent_id
WHERE s.slot_id = %(sid)s ORDER BY a.agent_name""")

q("sdd_report", "compliance", """
SELECT sdd_id, upsi_id, category, symbol, shared_by, shared_by_kind, recipient, recipient_kind, purpose, shared_at, planned_release_at
FROM v_sdd_report
WHERE (%(upsi_id)s::int IS NULL OR upsi_id = %(upsi_id)s)
ORDER BY upsi_id, shared_at""", "SDD: who received which UPSI item, when and from whom. (UPSI text itself is not exposed to compliance.)")

q("grant_review", "compliance", """
SELECT grant_id, agent_name, wall_name, asset_name, classification, owner_dept, privilege, valid_from, nullif(valid_to, 'infinity') AS valid_to
FROM v_grant_review ORDER BY agent_name, asset_name""", "LOW-side agents holding ANY grant on an asset owned by an inside area. Must return zero rows.")

q("exposure_trail", "compliance", """
SELECT iso_us(event_time) AS event_time, agent_name, side, action, object, outcome
FROM exposure_trail(%(sid)s)""", "Which agents touched rows derived from a canary, in time order.")

q("lag_summary", "compliance", """
SELECT low_agent,
       count(*)::int AS n_slots,
       round(avg(lag_ms)::numeric, 1)::float8 AS mean_ms,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY lag_ms))::numeric, 1)::float8 AS median_ms,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY lag_ms))::numeric, 1)::float8 AS p90_ms
FROM v_lag WHERE campaign_id = %(cid)s GROUP BY low_agent ORDER BY low_agent""",
  "Lag: research agent reads the canary -> the trading agent's first order in that security.")

q("lag_by_cell", "compliance", """
SELECT l.low_agent, t.vector_memory_on, t.notes_table_on, t.cache_on,
       count(*)::int AS n, round((percentile_cont(0.5) WITHIN GROUP (ORDER BY l.lag_ms))::numeric, 1)::float8 AS median_ms
FROM v_lag l
JOIN canary_slot s ON s.slot_id = l.slot_id
JOIN treatment t   ON t.treatment_id = s.treatment_id
WHERE l.campaign_id = %(cid)s
GROUP BY l.low_agent, t.vector_memory_on, t.notes_table_on, t.cache_on
ORDER BY l.low_agent, t.vector_memory_on DESC, t.notes_table_on DESC, t.cache_on DESC""")

q("model_comparison", "compliance", "SELECT * FROM v_model_comparison ORDER BY model_name, model_version",
  "Leak rate by agent model version across campaigns (frozen results).")

q("access_summary", "audit_engine", """
SELECT d.asset_name, e.op, e.outcome, count(*)::int AS n
FROM access_event e JOIN data_asset d ON d.asset_id = e.asset_id
WHERE e.event_time >= (SELECT started_at FROM audit_campaign WHERE campaign_id = %(cid)s)
  AND e.event_time <= coalesce((SELECT closed_at FROM audit_campaign WHERE campaign_id = %(cid)s), 'infinity')
GROUP BY d.asset_name, e.op, e.outcome ORDER BY d.asset_name, e.op, e.outcome""",
  "Every gateway access in the campaign's time window, including DENIED attempts (channel switched off, no grant, no active slot).")

q("power_table", "audit_engine", """
SELECT a AS accuracy,
       n_required_normal(a, %(alpha)s, %(power)s) AS n_normal,
       n_required_exact(a, %(alpha)s, %(power)s)  AS n_exact,
       power_exact(n_required_normal(a, %(alpha)s), a, %(alpha)s) AS power_at_normal_n
FROM unnest(%(accs)s::float8[]) AS a ORDER BY a DESC""",
  "n_normal is the normal-approximation formula (the proposal's table); n_exact is the first n whose EXACT binomial power reaches the target.")

q("power_point", "audit_engine", """
SELECT %(n)s::int AS n, binom_critical_k(%(n)s, %(alpha)s) AS critical_k,
       min_detectable_acc(%(n)s, %(alpha)s, %(power)s) AS min_detectable_acc,
       power_exact(%(n)s, %(acc)s, %(alpha)s) AS power_at_acc,
       leakage_bits(%(acc)s) AS bits_at_acc""")

q("power_curve", "audit_engine", """
SELECT a::float8 AS accuracy, power_exact(%(n)s, a::float8, %(alpha)s) AS power
FROM generate_series(0.50, 0.95, 0.005) AS a ORDER BY a""")

q("schema_columns", "compliance", """
SELECT c.relname AS table_name, a.attnum, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type,
       a.attnotnull AS not_null,
       coalesce((SELECT true FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary AND a.attnum = ANY (i.indkey)), false) AS is_pk,
       coalesce((SELECT true FROM pg_index i WHERE i.indrelid = c.oid AND i.indisunique AND NOT i.indisprimary
                 AND i.indnatts = 1 AND a.attnum = ANY (i.indkey)), false) AS is_unique,
       a.attidentity = 'a' AS is_identity
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind = 'r'
ORDER BY c.relname, a.attnum""")

q("schema_fks", "compliance", """
SELECT con.conname, src.relname AS from_table, tgt.relname AS to_table,
       (SELECT array_agg(a.attname ORDER BY k.ord) FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum) AS from_cols,
       (SELECT array_agg(a.attname ORDER BY k.ord) FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum) AS to_cols
FROM pg_constraint con
JOIN pg_class src ON src.oid = con.conrelid
JOIN pg_class tgt ON tgt.oid = con.confrelid
WHERE con.contype = 'f' AND src.relnamespace = 'public'::regnamespace
ORDER BY src.relname, con.conname""")

q("schema_rules", "compliance", """
SELECT c.relname AS table_name,
       c.relrowsecurity AS rls_enabled,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)::int AS n_policies,
       (SELECT coalesce(array_agg(t.tgname ORDER BY t.tgname), '{}') FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal) AS triggers,
       (SELECT count(*) FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'c')::int AS n_checks,
       (SELECT count(*) FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'x')::int AS n_exclusions
FROM pg_class c WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace ORDER BY c.relname""")

q("row_counts", "compliance", "SELECT table_name, row_count FROM schema_row_counts()",
  "Exact counts via a SECURITY DEFINER function so RLS cannot hide row counts (numbers only).")

q("price_meta", "compliance", """
SELECT min(trade_date) AS first_date, max(trade_date) AS last_date, count(*)::int AS n_bars, count(DISTINCT isin)::int AS n_securities,
       obj_description('daily_price'::regclass) AS provenance
FROM daily_price""")

q("securities", "compliance", "SELECT isin, symbol, company_name, sector FROM security ORDER BY symbol")

q("upsi_ids", "compliance", "SELECT DISTINCT upsi_id, category, symbol FROM v_sdd_report ORDER BY upsi_id")


def sql_meta(*names: str, **params) -> list[dict]:
    """The queries behind a panel, for the Show-SQL drawer."""
    return [{"name": n, "role": Q[n].role, "sql": Q[n].sql, "note": Q[n].note, "params": params or None} for n in names]
