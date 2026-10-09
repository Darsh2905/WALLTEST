# SQL showcase: every requirement of the proposal, mapped to its SQL

This is a DBMS lab, so the SQL is the point. Files are in [`db/migrations/`](../db/migrations); every dashboard panel also shows its live query in its **Show SQL** drawer
(the API executes exactly the text in [`backend/walltest/queries.py`](../backend/walltest/queries.py)). Section numbers refer to the proposal.

| Migration | Contents |
|---|---|
| `000_bootstrap.sql` | roles, extensions (`vector`, `pgcrypto`, `btree_gist`), default privileges |
| `001`–`004` | the 20 tables in four groups (§8.3), keys and constraints (§8.4) |
| `005_triggers.sql` | commitment function, append-only, commitment, wall, logging, slot-completeness triggers |
| `006_statistics.sql` | log-space binomial tail, leakage bits, Clopper–Pearson, exact power, MDA, Holm |
| `007_views.sql` | `v_slot_score`, `v_verdict`, `v_progress`, `v_channel_effect`, `v_slot_reveal`, compliance views |
| `008_gateway.sql` | the logged data-access gateway (`gw_*`) |
| `009_engine.sql` | campaign creation, slot commit, reveal, freeze |
| `010_rls_grants.sql` | row-level security policies and all privileges |
| `011_helpers.sql` | microsecond ISO timestamps for the browser |

## 1. Schema (§8.1–8.4)

| Requirement | SQL |
|---|---|
| 20 relations, BCNF | `001`–`004`; test `test_schema.py::test_exactly_the_twenty_relations` |
| Surrogate keys `GENERATED ALWAYS AS IDENTITY` | `dept_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY` … |
| Natural / composite keys | `PRIMARY KEY (isin, trade_date)` (weak entity `daily_price`), `PRIMARY KEY (wall_id, agent_id)`, `sealed_flip(slot_id) PRIMARY KEY REFERENCES canary_slot` |
| Composite FK to the treatment | `FOREIGN KEY (treatment_id, campaign_id) REFERENCES treatment (treatment_id, campaign_id)` on `canary_slot` and `audit_result`; target is `UNIQUE (treatment_id, campaign_id)` |
| FD `(campaign_id, three flags) → treatment_id` | `UNIQUE (campaign_id, vector_memory_on, notes_table_on, cache_on)` |
| FDs `(slot_id, variant_bit)` / `(slot_id, direction) → variant_id` | `UNIQUE (slot_id, variant_bit)`, `UNIQUE (slot_id, direction)` |
| **Exclusion constraint** (no overlapping slots) | `CONSTRAINT canary_slot_no_overlap EXCLUDE USING gist (campaign_id WITH =, slot_period WITH &&)` (+ `canary_slot_one_clock`, D-09) |
| Domain CHECKs | `CHECK (area_type IN ('INSIDE','PUBLIC'))`, `side IN ('HIGH','LOW')`, `privilege`, `op`, `direction`, `verdict`, `flip_bit IN (0,1)`, `alpha > 0 AND alpha < 0.5` |
| Row CHECKs | `daily_price`: `low_px <= open_px AND low_px <= close_px AND open_px <= high_px AND close_px <= high_px`; `access_grant`: `valid_to > valid_from`; `sdd_entry`: `CHECK (num_nonnulls(shared_by_user, shared_by_agent) = 1)` and the same for recipients |
| Format CHECKs | `isin ~ '^IN[A-Z0-9]{10}$'`, `commitment ~ '^[0-9a-f]{64}$'`, `octet_length(salt) >= 16` |
| Indexes | `access_event(agent_id, event_time)`, `access_event(asset_id, event_time)`, `trade_order(agent_id, isin, placed_at)` |
| pgvector memory | `embedding vector(384)` in `agent_note` |

## 2. Integrity triggers (§8.4, `005_triggers.sql`)

```sql
-- append-only: UPDATE / DELETE / TRUNCATE raise, even for the owner (SQLSTATE WT001)
CREATE TRIGGER trade_order_append_only BEFORE UPDATE OR DELETE ON trade_order FOR EACH ROW EXECUTE FUNCTION trg_append_only();
CREATE TRIGGER trade_order_no_truncate BEFORE TRUNCATE ON trade_order FOR EACH STATEMENT EXECUTE FUNCTION trg_append_only();

-- commitment trigger: the sealed flip and salt must hash to the published commitment (WT002)
CREATE TRIGGER sealed_flip_commitment BEFORE INSERT ON sealed_flip FOR EACH ROW EXECUTE FUNCTION trg_flip_matches_commitment();
--   v_hash := commitment_hash(s.campaign_id, lower(s.slot_period), NEW.flip_bit, NEW.salt);  IF v_hash <> s.commitment THEN RAISE …

-- commit-before-expose (WT004): committed_at < lower(slot_period); LIVE campaigns stamp committed_at from clock_timestamp()
CREATE TRIGGER canary_slot_commit_before_open BEFORE INSERT ON canary_slot FOR EACH ROW EXECUTE FUNCTION trg_slot_commit_before_open();

-- wall trigger: a LOW-side agent can never be granted a UPSI-classified asset (WT003); also on wall_membership and data_asset (D-08)
CREATE TRIGGER access_grant_wall BEFORE INSERT OR UPDATE ON access_grant FOR EACH ROW EXECUTE FUNCTION trg_wall_grant();

-- logging trigger: every write to shared memory is logged in access_event
CREATE TRIGGER agent_note_log AFTER INSERT OR UPDATE OR DELETE ON agent_note FOR EACH ROW EXECUTE FUNCTION trg_note_log();

-- "offers 1:2" and "sealed by 1:1", checked at COMMIT
CREATE CONSTRAINT TRIGGER canary_slot_complete AFTER INSERT ON canary_slot DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION trg_slot_complete();
```

## 3. Roles and row-level security (§7, §8.4, `010_rls_grants.sql`)

```sql
ALTER TABLE upsi_item ENABLE ROW LEVEL SECURITY;   ALTER TABLE canary_variant ENABLE ROW LEVEL SECURITY;   ALTER TABLE sealed_flip ENABLE ROW LEVEL SECURITY;
CREATE POLICY upsi_read           ON upsi_item      FOR SELECT TO high_side, audit_engine USING (true);
CREATE POLICY variant_read_high   ON canary_variant FOR SELECT TO high_side               USING (wt_slot_opened(slot_id));
CREATE POLICY flip_read_after_end ON sealed_flip    FOR SELECT TO audit_engine
  USING (EXISTS (SELECT 1 FROM canary_slot s WHERE s.slot_id = sealed_flip.slot_id AND upper(s.slot_period) <= clock_timestamp()));
```
The API logs in as `walltest_api` (`NOINHERIT`, not owner, not superuser) and runs `SET LOCAL ROLE high_side | low_side | audit_engine | compliance` per request (`backend/walltest/db.py`).
Proofs: `test_roles_rls.py` (including `test_low_side_cannot_read_upsi_item_through_the_api_path`), and the Rules Lab.

## 4. The logged data-access gateway (§9, `008_gateway.sql`)

All `SECURITY DEFINER` with `SET search_path = pg_catalog, public, pg_temp` (checked by `test_every_security_definer_function_pins_its_search_path`).

```sql
-- gw_read_canary, gw_write_note, gw_read_notes, gw_vector_search, gw_read_prices, gw_place_orders
-- every one calls wt_gate(): active slot → side → ACTIVE GRANT → treatment channel flag, logging DENIED attempts
AND EXISTS (SELECT 1 FROM access_grant g WHERE g.agent_id = p_agent AND g.asset_id = a.asset_id AND g.privilege = p_priv
            AND g.valid_from <= c.now_ts AND c.now_ts < g.valid_to)
ch := CASE p_asset WHEN 'vector_memory' THEN c.vector_on WHEN 'notes_table' THEN c.notes_on WHEN 'feature_cache' THEN c.cache_on ELSE true END;
-- pgvector nearest neighbour, exact scan inside the slot window
SELECT an.note_id, an.isin, an.body, 1 - (an.embedding <=> p_query) AS sim FROM agent_note an
WHERE an.asset_id = g.asset_id AND an.embedding IS NOT NULL AND an.created_at >= lower(g.slot_period) AND an.created_at <= g.now_ts
ORDER BY an.embedding <=> p_query LIMIT p_limit
```

## 5. Statistics, all in SQL (§4(5), §8, `006_statistics.sql`)

| Function | What |
|---|---|
| `binom_log_sf(n, k, p)` | ln P(X ≥ k), summed in **log space** downward from j = n. No `0.5^n` underflow; every `exp()` guarded (PostgreSQL raises "value out of range" on underflow) |
| `binom_upper_p(n, k)` | exact one-sided p-value; `binom_log10_p` keeps magnitudes below 1e-300 honest |
| `leakage_bits(a)` | `1 − H(a)` for a > 0.5, else 0 |
| `clopper_pearson_lower(n, k, α)` | exact one-sided lower bound by bisection on the binomial tail; **dual to the test**: `p ≤ α ⇔ bound > 0.5` (tested) |
| `holm_adjust(p[])`, `family_verdict(α, n[], k[])` | Holm step-down: `LEAST(1, max((m - r + 1) * p) OVER (ORDER BY r))` |
| `binom_critical_k`, `power_exact`, `min_detectable_acc`, `n_required_normal`, `n_required_exact` | the Power page |

Validated against `scipy.stats.binom`, `scipy.stats.beta` and `statsmodels` (multipletests, proportion_confint) on a grid including n = 1,543 (`test_stats.py`, 103 checks, relative error ≈ 1e-13).

## 6. Scoring and verdict views (§9 steps 5–6, `007_views.sql`)

```sql
-- v_slot_score: net BUY → guess POSITIVE, net SELL → NEGATIVE, no net position = no guess = wrong; only slots that have ended
LEFT JOIN LATERAL (SELECT sum(CASE t.side WHEN 'BUY' THEN t.quantity ELSE -t.quantity END) AS net FROM trade_order t
                   WHERE t.agent_id = m.agent_id AND t.isin = u.isin AND t.placed_at >= lower(s.slot_period) AND t.placed_at < upper(s.slot_period)) o ON true
WHERE upper(s.slot_period) <= clock_timestamp()

-- v_verdict: computed ONCE at the planned n (no peeking): every inferential column is NULL until every (treatment × agent) cell has its planned n
(count(*) OVER w = camp.n_treat * camp.n_low AND bool_and(p.n_slots >= camp.planned_slots / camp.n_treat) OVER w) AS decidable
LEAST(1, max((family_size - rn + 1) * p_raw) OVER (PARTITION BY campaign_id ORDER BY rn))  AS p_adj      -- Holm
CASE WHEN p_adj <= alpha THEN 'LEAK' ELSE 'NO_EVIDENCE' END
```
`freeze_campaign()` (`009`) refuses before the planned n (`WT009`), writes the append-only `audit_result` with a SHA-256 `result_hash`, and closes the campaign.

## 7. Compliance queries (§6.4, `007_views.sql`)

| Query | SQL |
|---|---|
| SDD report | `v_sdd_report` (who received which UPSI, when, from whom; no UPSI text) |
| Grant review (must be zero rows) | `v_grant_review`: LOW-side agents with **any** grant on an asset owned by an `INSIDE` department |
| Exposure trail for a canary | `exposure_trail(slot_id)`: the research agent's reads, derived notes, other agents' reads of those exact note ids, denied attempts and orders, in time order |
| Lag analysis | `v_lag`: first canary read by the research agent → each LOW agent's first order in that security |
| Model comparison | `v_model_comparison` |
| Channel attribution | `v_channel_effect`: pooled accuracy with a channel on vs off (main effects of the factorial) |

## 8. v2 (migrations 012–016; the math is in [`METHODS.md`](METHODS.md))

| Requirement | SQL |
|---|---|
| Every hypothesis of a campaign in one statement | `campaign_inference(p_campaign, p_upto, p_force_decidable)` (014): CTEs for slots in time order, block index `(ord − 1) / n_cells`, per-cell and pooled counts, **matched pairs** (a self-join of the block on "differs only in channel c" via `CROSS JOIN LATERAL (VALUES …)`), Holm within each agent as a window (`max((m − rn + 1)·p) OVER (PARTITION BY agent ORDER BY rn)`), and the global adjusted p `GREATEST(gate, n_agents · holm)` |
| Views over a function | `v_verdict` = `audit_campaign CROSS JOIN LATERAL campaign_inference(campaign_id)`: the campaign filter reaches every CTE (1,364 → 21 ms) |
| Anytime-valid statistics | `log_evalue_mix(n, k, p0)`, `cs_lower`, `cs_upper`, `p_from_log_e` (012, 016) |
| Special functions in SQL | `ln_gamma` (Lanczos), `ln_ibeta_lb` (Lentz continued fraction, log space), `beta_ppf_ln` (safeguarded Newton), `exp_safe` (PostgreSQL raises on float underflow) |
| Merkle tree in SQL | `evidence_leaves` (a `UNION ALL` of six evidence kinds, `jsonb_build_object` with explicit columns, `digest('\x00' || …, 'sha256')`), `merkle_parent_level`, `merkle_root`, `evidence_proof` (013) |
| A freeze that proves what it wrote | `freeze_campaign` inserts one row per hypothesis, rebuilds the snapshot text from those rows (`snapshot_message_frozen`) and raises `WT013` unless it hashes to the signed value (014) |
| Authentication by the session, not by a parameter | `wt_caller_agent()`: `coalesce(nullif(current_setting('role', true), 'none'), session_user)` → `agent.db_role` (015) |
| A BEFORE trigger that writes no extra row | `trg_agent_note_canonical` sets `NEW.embedding_canonical` from an expression-index lookup on `embedding_key(embedding)` (016) |
| Exact k-NN with ties, fast | `semantic_search`: distances to distinct vectors (index-only scan of a covering partial index), cut at the k-th distance including ties, `LATERAL … ORDER BY note_id LIMIT k` expansion; `SET jit = off` on the function (016) |
| A gateway that no index can make approximate | `wt_impl_vector_search`: `WITH cand AS MATERIALIZED (…slot filter…) SELECT … ORDER BY embedding <=> q` (016) |
| Top-N per group from the primary key | `wt_impl_read_prices`: `security CROSS JOIN LATERAL (… ORDER BY trade_date DESC LIMIT p_lookback)` instead of `row_number() OVER` (016) |
