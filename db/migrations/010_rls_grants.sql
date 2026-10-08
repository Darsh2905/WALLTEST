-- 010: Row-level security and role privileges  (doc 8.4 "Role privileges + row-level security")
--
--   upsi_item, canary_variant : readable only by high_side and audit_engine
--   sealed_flip               : readable only by audit_engine, and only after its slot has ended
--
-- RLS is ENABLEd (not FORCEd): the table owner bypasses it, which the SECURITY DEFINER gateway needs; that is exactly why
-- nothing in the request path connects as the owner or a superuser (see 000_bootstrap.sql and tests/test_roles.py).

ALTER TABLE upsi_item      ENABLE ROW LEVEL SECURITY;
ALTER TABLE canary_variant ENABLE ROW LEVEL SECURITY;
ALTER TABLE sealed_flip    ENABLE ROW LEVEL SECURITY;

CREATE POLICY upsi_read ON upsi_item FOR SELECT TO high_side, audit_engine USING (true);

-- high_side sees a slot's variants only once the slot has opened (no pre-exposure); audit_engine may read all variants
-- (it cannot tell which one was shown: the flip is sealed).
-- The policy calls a SECURITY DEFINER helper because high_side has no privilege on canary_slot (a policy subquery runs
-- with the caller's rights and would fail with "permission denied").
CREATE FUNCTION wt_slot_opened(p_slot integer) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT EXISTS (SELECT 1 FROM canary_slot s WHERE s.slot_id = p_slot AND lower(s.slot_period) <= clock_timestamp()) $$;
GRANT EXECUTE ON FUNCTION wt_slot_opened(integer) TO high_side;
CREATE POLICY variant_read_high  ON canary_variant FOR SELECT TO high_side USING (wt_slot_opened(slot_id));
CREATE POLICY variant_read_audit ON canary_variant FOR SELECT TO audit_engine USING (true);
CREATE POLICY variant_insert     ON canary_variant FOR INSERT TO audit_engine WITH CHECK (true);

CREATE POLICY flip_insert ON sealed_flip FOR INSERT TO audit_engine WITH CHECK (true);
CREATE POLICY flip_read_after_end ON sealed_flip FOR SELECT TO audit_engine
  USING (EXISTS (SELECT 1 FROM canary_slot s WHERE s.slot_id = sealed_flip.slot_id AND upper(s.slot_period) <= clock_timestamp()));

------------------------------------------------------------------------------------------------
-- Table privileges. Agents (high_side, low_side) have NO direct table access except high_side's RLS-filtered SELECT on
-- upsi_item / canary_variant; everything else is through gw_* functions. UPDATE/DELETE on the append-only tables is
-- not granted to anyone (and the triggers refuse it even for the owner).
------------------------------------------------------------------------------------------------
GRANT SELECT ON upsi_item, canary_variant TO high_side;

GRANT SELECT, INSERT, UPDATE ON department, app_user, agent, info_wall, wall_membership, data_asset, access_grant TO compliance;
GRANT SELECT, INSERT ON security, daily_price, sdd_entry, audit_campaign, treatment TO compliance;
GRANT SELECT ON canary_slot, audit_result, access_event, trade_order, agent_note TO compliance;
GRANT SELECT ON v_slot_public, v_sdd_report, v_grant_review, v_lag, v_model_comparison, v_result_integrity TO compliance;
GRANT UPDATE (status, started_at, closed_at) ON audit_campaign TO compliance;

GRANT SELECT ON department, app_user, agent, info_wall, wall_membership, data_asset, access_grant, security, daily_price,
                upsi_item, audit_campaign, treatment, canary_slot, canary_variant, sealed_flip, audit_result,
                access_event, trade_order, agent_note TO audit_engine;
GRANT INSERT ON canary_slot, canary_variant, sealed_flip, audit_result TO audit_engine;
GRANT UPDATE (status, started_at, closed_at) ON audit_campaign TO audit_engine;
GRANT SELECT ON v_slot_score, v_progress, v_verdict, v_channel_effect, v_slot_reveal, v_slot_public, v_result_integrity,
                v_lag, v_model_comparison TO audit_engine;

------------------------------------------------------------------------------------------------
-- Function privileges
------------------------------------------------------------------------------------------------
GRANT EXECUTE ON FUNCTION gw_read_canary(integer), gw_write_note(integer, text, char, text, vector),
                          gw_read_notes(integer, text, integer) TO high_side;
GRANT EXECUTE ON FUNCTION gw_read_prices(integer, date, integer), gw_read_notes(integer, text, integer),
                          gw_vector_search(integer, vector, integer), gw_place_order(integer, text, text, integer, numeric),
                          gw_place_orders(integer, text[], text[], integer, numeric[]) TO low_side;

GRANT EXECUTE ON FUNCTION create_campaign(integer, integer, numeric, integer, text, text, jsonb),
                          start_campaign(integer, timestamptz), abort_campaign(integer, timestamptz) TO compliance, audit_engine;
GRANT EXECUTE ON FUNCTION engine_commit_slot(integer, integer, integer, tstzrange, char, timestamptz, smallint, bytea, smallint, text, text),
                          engine_reveal(integer), freeze_campaign(integer, integer, timestamptz),
                          audit_result_hash(integer, integer, integer, integer, integer, double precision, double precision, double precision, numeric, text)
                          TO audit_engine;
GRANT EXECUTE ON FUNCTION exposure_trail(integer), schema_row_counts() TO compliance, audit_engine;
GRANT EXECUTE ON FUNCTION commitment_preimage(integer, timestamptz, smallint, bytea),
                          commitment_hash(integer, timestamptz, smallint, bytea) TO audit_engine, compliance;

-- Statistics are pure functions of numbers: usable by the engine and by compliance (e.g. the Power page).
GRANT EXECUTE ON FUNCTION binom_log_sf(integer, integer, double precision), binom_upper_p(integer, integer, double precision),
                          binom_log10_p(integer, integer, double precision), leakage_bits(double precision),
                          clopper_pearson_lower(integer, integer, double precision), binom_critical_k(integer, double precision),
                          power_exact(integer, double precision, double precision),
                          min_detectable_acc(integer, double precision, double precision),
                          norm_ppf(double precision), n_required_normal(double precision, double precision, double precision),
                          n_required_exact(double precision, double precision, double precision),
                          holm_adjust(double precision[]), family_verdict(double precision, integer[], integer[])
      TO audit_engine, compliance;
