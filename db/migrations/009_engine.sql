-- 009: Audit-engine and compliance functions (doc 9 workflow steps 1, 2, 5, 6)
-- All SECURITY INVOKER: they run with the caller's (compliance / audit_engine) privileges, so RLS still applies.

-- Step 1: the compliance officer creates a campaign for a wall, choosing alpha, the slot target and the treatment cells.
-- create_campaign_cells takes the cells explicitly: a boolean[][] of (vector_memory_on, notes_table_on, cache_on) rows. The preset
-- designs below are shorthands for it:
--   FULL_FACTORIAL  all 2^3 on/off combinations of (vector memory, notes table, cache)  -> 8 cells
--   ONE_AT_A_TIME   ALL_ON, each channel switched off alone, ALL_OFF                    -> 5 cells
--   ALL_ON          a single cell with every channel on                                 -> 1 cell
-- planned_slots must be a multiple of the number of cells so every cell has the same planned n.
CREATE FUNCTION create_campaign_cells(p_wall integer, p_user integer, p_alpha numeric, p_planned_slots integer,
                                      p_cells boolean[], p_clock text DEFAULT 'LIVE', p_config jsonb DEFAULT '{}'::jsonb)
RETURNS integer LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE cid integer; n_cells integer;
BEGIN
  n_cells := COALESCE(array_length(p_cells, 1), 0);
  IF n_cells = 0 OR array_length(p_cells, 2) <> 3 THEN RAISE EXCEPTION 'cells must be an N x 3 boolean array'; END IF;
  IF p_planned_slots % n_cells <> 0 THEN
    RAISE EXCEPTION 'planned_slots (%) must be a multiple of the number of treatment cells (%)', p_planned_slots, n_cells;
  END IF;
  INSERT INTO audit_campaign (wall_id, created_by, alpha, planned_slots, clock_mode, config)
  VALUES (p_wall, p_user, p_alpha, p_planned_slots, p_clock, p_config) RETURNING campaign_id INTO cid;
  FOR i IN 1..n_cells LOOP
    INSERT INTO treatment (campaign_id, vector_memory_on, notes_table_on, cache_on)
    VALUES (cid, p_cells[i][1], p_cells[i][2], p_cells[i][3]);
  END LOOP;
  RETURN cid;
END
$$;

CREATE FUNCTION create_campaign(p_wall integer, p_user integer, p_alpha numeric, p_planned_slots integer,
                                p_design text DEFAULT 'FULL_FACTORIAL', p_clock text DEFAULT 'LIVE',
                                p_config jsonb DEFAULT '{}'::jsonb)
RETURNS integer LANGUAGE sql
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT create_campaign_cells(p_wall, p_user, p_alpha, p_planned_slots,
    CASE p_design
      WHEN 'FULL_FACTORIAL' THEN ARRAY[[true,true,true],[true,true,false],[true,false,true],[true,false,false],
                                       [false,true,true],[false,true,false],[false,false,true],[false,false,false]]
      WHEN 'ONE_AT_A_TIME'  THEN ARRAY[[true,true,true],[false,true,true],[true,false,true],[true,true,false],[false,false,false]]
      WHEN 'ALL_ON'         THEN ARRAY[[true,true,true]]
      ELSE NULL END,
    p_clock, p_config || jsonb_build_object('design', p_design))
$$;

CREATE FUNCTION start_campaign(p_campaign integer, p_at timestamptz DEFAULT clock_timestamp())
RETURNS void LANGUAGE sql
SET search_path = pg_catalog, public, pg_temp
AS $$ UPDATE audit_campaign SET status = 'RUNNING', started_at = p_at WHERE campaign_id = p_campaign AND status = 'PLANNED' $$;

CREATE FUNCTION abort_campaign(p_campaign integer, p_at timestamptz DEFAULT clock_timestamp())
RETURNS void LANGUAGE sql
SET search_path = pg_catalog, public, pg_temp
AS $$ UPDATE audit_campaign SET status = 'ABORTED', closed_at = p_at WHERE campaign_id = p_campaign AND status IN ('PLANNED','RUNNING') $$;

-- Step 2: commit one slot. Inserts the slot (with its published commitment), its two variants and the sealed flip in
-- one transaction; the triggers check commit-before-open, the commitment match and the 1:2 / 1:1 cardinalities.
-- The engine draws flip and salt from a CSPRNG and chooses (independently of the flip) which variant_bit carries POSITIVE.
CREATE FUNCTION engine_commit_slot(p_campaign integer, p_treatment integer, p_upsi integer, p_period tstzrange,
                                   p_commitment char(64), p_committed_at timestamptz,
                                   p_flip smallint, p_salt bytea, p_positive_bit smallint,
                                   p_positive_text text, p_negative_text text)
RETURNS integer LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE sid integer;
BEGIN
  INSERT INTO canary_slot (campaign_id, treatment_id, upsi_id, slot_period, commitment, committed_at)
  VALUES (p_campaign, p_treatment, p_upsi, p_period, p_commitment, p_committed_at) RETURNING slot_id INTO sid;
  INSERT INTO canary_variant (slot_id, variant_bit, direction, content) VALUES
    (sid, p_positive_bit,       'POSITIVE', p_positive_text),
    (sid, 1 - p_positive_bit,   'NEGATIVE', p_negative_text);
  INSERT INTO sealed_flip (slot_id, flip_bit, salt) VALUES (sid, p_flip, p_salt);
  RETURN sid;
END
$$;

-- Step 5: reveal. RLS only lets audit_engine see a flip after its slot has ended; this function says so explicitly.
CREATE FUNCTION engine_reveal(p_slot integer)
RETURNS TABLE (slot_id integer, flip_bit smallint, salt_hex text, commitment char(64), recomputed char(64), commitment_ok boolean)
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
BEGIN
  IF NOT EXISTS (SELECT 1 FROM canary_slot s WHERE s.slot_id = p_slot) THEN
    RAISE EXCEPTION 'slot % does not exist', p_slot;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM v_slot_reveal r WHERE r.slot_id = p_slot) THEN
    RAISE EXCEPTION 'flip for slot % is sealed until the slot has ended', p_slot USING ERRCODE = 'WT006';
  END IF;
  RETURN QUERY SELECT r.slot_id, r.flip_bit, r.salt_hex, r.commitment, r.recomputed, r.commitment_ok
               FROM v_slot_reveal r WHERE r.slot_id = p_slot;
END
$$;

-- Step 6: freeze. Verdicts are computed ONCE, at the planned n, and written to the append-only audit_result.
-- result_hash is a SHA-256 over the canonical field list: a tamper-evident snapshot (D-06).
CREATE FUNCTION audit_result_hash(p_campaign integer, p_treatment integer, p_agent integer, p_n integer, p_k integer,
                                  p_p double precision, p_padj double precision, p_lower double precision,
                                  p_alpha numeric, p_clock text)
RETURNS char(64) LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT encode(digest(convert_to(format('WALLTEST-RESULT-v1|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s',
         p_campaign, p_treatment, p_agent, p_n, p_k, p_p, p_padj, p_lower, p_alpha, p_clock), 'UTF8'), 'sha256'), 'hex')
$$;

CREATE FUNCTION freeze_campaign(p_campaign integer, p_user integer, p_closed_at timestamptz DEFAULT clock_timestamp())
RETURNS integer LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE n integer;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM v_verdict WHERE campaign_id = p_campaign AND decidable) THEN
    RAISE EXCEPTION 'campaign % has not reached its planned n in every cell; the verdict is computed once, at the planned n (no peeking)',
      p_campaign USING ERRCODE = 'WT009';
  END IF;
  INSERT INTO audit_result (campaign_id, treatment_id, low_agent_id, n_slots, n_correct, p_value, leakage_bits, verdict,
                            computed_by, computed_at, p_adjusted, acc_lower, leakage_bits_lower, min_detectable_acc,
                            family_size, alpha, clock_mode, result_hash)
  SELECT v.campaign_id, v.treatment_id, v.low_agent_id, v.n_slots, v.n_correct, v.p_raw, v.leakage_bits, v.verdict,
         p_user, p_closed_at, v.p_adj, v.acc_lower, v.leakage_bits_lower, v.min_detectable_acc,
         v.family_size, v.alpha::numeric, v.clock_mode,
         audit_result_hash(v.campaign_id, v.treatment_id, v.low_agent_id, v.n_slots, v.n_correct, v.p_raw, v.p_adj,
                           v.acc_lower, v.alpha::numeric, v.clock_mode)
  FROM v_verdict v WHERE v.campaign_id = p_campaign AND v.decidable;
  GET DIAGNOSTICS n = ROW_COUNT;
  UPDATE audit_campaign SET status = 'CLOSED', closed_at = p_closed_at WHERE campaign_id = p_campaign;
  RETURN n;
END
$$;

-- Integrity check on frozen results: recompute each snapshot hash.
CREATE VIEW v_result_integrity AS
SELECT r.result_id, r.campaign_id, r.treatment_id, r.low_agent_id, r.result_hash,
       audit_result_hash(r.campaign_id, r.treatment_id, r.low_agent_id, r.n_slots, r.n_correct, r.p_value,
                         r.p_adjusted, r.acc_lower, r.alpha, r.clock_mode) AS recomputed,
       (r.result_hash = audit_result_hash(r.campaign_id, r.treatment_id, r.low_agent_id, r.n_slots, r.n_correct,
                         r.p_value, r.p_adjusted, r.acc_lower, r.alpha, r.clock_mode)) AS hash_ok
FROM audit_result r;
