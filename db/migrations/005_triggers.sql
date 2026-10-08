-- 005: Integrity triggers  (doc 8.4: append-only, commitment, wall, logging triggers)
--
-- Custom SQLSTATEs (class WT) so callers and tests can tell the guards apart:
--   WT001 append-only violation          WT002 flip does not match its commitment
--   WT003 wall violation (UPSI -> LOW)   WT004 commitment not published before the slot opens
--   WT005 slot incomplete                WT006 flip not yet revealable   WT007 campaign not running

------------------------------------------------------------------------------------------------
-- Canonical commitment format (docs/COMMITMENT_FORMAT.md). Used identically by the engine (Python),
-- this trigger (SQL) and the inspector (browser, Web Crypto):
--
--   preimage   = "WALLTEST-v1|" campaign_id "|" start_us "|" flip_bit "|" salt_hex          (ASCII)
--   commitment = lower-case hex( SHA-256( UTF-8 bytes of preimage ) )                         (64 chars)
--
--   campaign_id  decimal, no padding
--   start_us     lower(slot_period) as integer MICROSECONDS since 1970-01-01T00:00:00Z (the reference
--                implementation truncated to whole seconds; live slots last ~1-2 s, so that is unusable)
--   flip_bit     "0" or "1"
--   salt_hex     lower-case hex of the raw salt bytes (>= 16 bytes; the engine draws 32 from a CSPRNG)
------------------------------------------------------------------------------------------------
CREATE FUNCTION commitment_preimage(p_campaign integer, p_slot_start timestamptz, p_flip smallint, p_salt bytea)
RETURNS text LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT 'WALLTEST-v1|' || p_campaign::text
         || '|' || ((extract(epoch FROM p_slot_start) * 1000000)::numeric)::bigint::text
         || '|' || p_flip::text
         || '|' || encode(p_salt, 'hex')
$$;

CREATE FUNCTION commitment_hash(p_campaign integer, p_slot_start timestamptz, p_flip smallint, p_salt bytea)
RETURNS char(64) LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT encode(digest(convert_to(commitment_preimage(p_campaign, p_slot_start, p_flip, p_salt), 'UTF8'), 'sha256'), 'hex')
$$;

------------------------------------------------------------------------------------------------
-- Append-only: UPDATE / DELETE / TRUNCATE raise (access_event, trade_order, canary_slot, sealed_flip,
-- audit_result per the proposal; canary_variant added, D-07)
------------------------------------------------------------------------------------------------
CREATE FUNCTION trg_append_only() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % is not permitted (tamper-evident audit trail)', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'WT001';
END
$$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['access_event','trade_order','canary_slot','sealed_flip','audit_result','canary_variant'] LOOP
    EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION trg_append_only()',
                   t || '_append_only', t);
    EXECUTE format('CREATE TRIGGER %I BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION trg_append_only()',
                   t || '_no_truncate', t);
  END LOOP;
END $$;

------------------------------------------------------------------------------------------------
-- canary_slot: commitment must be published before the slot opens (commit-before-expose) and the
-- campaign must be RUNNING. LIVE campaigns: committed_at is stamped by the server clock (cannot be
-- supplied or back-dated). SIMULATED campaigns: the engine supplies a back-dated value; the UI labels
-- such campaigns "simulated clock".
------------------------------------------------------------------------------------------------
CREATE FUNCTION trg_slot_commit_before_open() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE v_mode text; v_status text;
BEGIN
  SELECT clock_mode, status INTO v_mode, v_status FROM audit_campaign WHERE campaign_id = NEW.campaign_id;
  IF v_status IS DISTINCT FROM 'RUNNING' THEN
    RAISE EXCEPTION 'campaign % is % (slots can only be committed while RUNNING)', NEW.campaign_id, v_status
      USING ERRCODE = 'WT007';
  END IF;
  IF v_mode = 'LIVE' THEN
    NEW.committed_at := clock_timestamp();
  ELSIF NEW.committed_at IS NULL THEN
    RAISE EXCEPTION 'SIMULATED campaign: committed_at must be supplied' USING ERRCODE = 'WT004';
  END IF;
  IF NEW.committed_at >= lower(NEW.slot_period) THEN
    RAISE EXCEPTION 'commitment published at % is not before the slot opens at % (commit-before-expose)',
      NEW.committed_at, lower(NEW.slot_period) USING ERRCODE = 'WT004';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER canary_slot_commit_before_open BEFORE INSERT ON canary_slot
  FOR EACH ROW EXECUTE FUNCTION trg_slot_commit_before_open();

------------------------------------------------------------------------------------------------
-- Commitment trigger: BEFORE INSERT on sealed_flip. SHA-256(canonical preimage) must equal the
-- commitment published with the slot, otherwise the insert is rejected.
------------------------------------------------------------------------------------------------
CREATE FUNCTION trg_flip_matches_commitment() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE s record; v_hash char(64);
BEGIN
  SELECT campaign_id, slot_period, commitment INTO s FROM canary_slot WHERE slot_id = NEW.slot_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'slot % does not exist; commit the slot before sealing its flip', NEW.slot_id USING ERRCODE = 'WT002';
  END IF;
  v_hash := commitment_hash(s.campaign_id, lower(s.slot_period), NEW.flip_bit, NEW.salt);
  IF v_hash <> s.commitment THEN
    RAISE EXCEPTION 'flip/salt hash % does not match the commitment % published for slot %',
      v_hash, s.commitment, NEW.slot_id USING ERRCODE = 'WT002';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER sealed_flip_commitment BEFORE INSERT ON sealed_flip
  FOR EACH ROW EXECUTE FUNCTION trg_flip_matches_commitment();

------------------------------------------------------------------------------------------------
-- Slot completeness (cardinalities "offers 1:2" and "sealed by 1:1"), checked at COMMIT.
------------------------------------------------------------------------------------------------
CREATE FUNCTION trg_slot_complete() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  IF (SELECT count(*) FROM canary_variant WHERE slot_id = NEW.slot_id) <> 2 THEN
    RAISE EXCEPTION 'slot % must offer exactly two canary variants', NEW.slot_id USING ERRCODE = 'WT005';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM sealed_flip WHERE slot_id = NEW.slot_id) THEN
    RAISE EXCEPTION 'slot % must have a sealed flip', NEW.slot_id USING ERRCODE = 'WT005';
  END IF;
  RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER canary_slot_complete AFTER INSERT ON canary_slot
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION trg_slot_complete();

------------------------------------------------------------------------------------------------
-- Wall trigger: a LOW-side agent can never be granted a UPSI-classified asset.
-- The proposal puts it on access_grant only; that leaves two bypasses (add the agent to the LOW side
-- AFTER granting; re-classify an asset AFTER granting), so the same rule guards wall_membership and
-- data_asset too (D-08).
------------------------------------------------------------------------------------------------
CREATE FUNCTION trg_wall_grant() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM data_asset a WHERE a.asset_id = NEW.asset_id AND a.classification = 'UPSI')
     AND EXISTS (SELECT 1 FROM wall_membership m WHERE m.agent_id = NEW.agent_id AND m.side = 'LOW') THEN
    RAISE EXCEPTION 'wall violation: agent % sits on the LOW side of a wall and cannot be granted UPSI asset %',
      NEW.agent_id, NEW.asset_id USING ERRCODE = 'WT003';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER access_grant_wall BEFORE INSERT OR UPDATE ON access_grant
  FOR EACH ROW EXECUTE FUNCTION trg_wall_grant();

CREATE FUNCTION trg_wall_membership() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  IF NEW.side = 'LOW' AND EXISTS (
       SELECT 1 FROM access_grant g JOIN data_asset a USING (asset_id)
       WHERE g.agent_id = NEW.agent_id AND a.classification = 'UPSI') THEN
    RAISE EXCEPTION 'wall violation: agent % already holds a grant on a UPSI asset and cannot join the LOW side',
      NEW.agent_id USING ERRCODE = 'WT003';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER wall_membership_wall BEFORE INSERT OR UPDATE ON wall_membership
  FOR EACH ROW EXECUTE FUNCTION trg_wall_membership();

CREATE FUNCTION trg_asset_reclassify() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  IF NEW.classification = 'UPSI' AND EXISTS (
       SELECT 1 FROM access_grant g JOIN wall_membership m ON m.agent_id = g.agent_id AND m.side = 'LOW'
       WHERE g.asset_id = NEW.asset_id) THEN
    RAISE EXCEPTION 'wall violation: asset % is held by a LOW-side agent and cannot be classified UPSI', NEW.asset_id
      USING ERRCODE = 'WT003';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER data_asset_reclassify BEFORE UPDATE OF classification ON data_asset
  FOR EACH ROW EXECUTE FUNCTION trg_asset_reclassify();

------------------------------------------------------------------------------------------------
-- Logging trigger: every write to shared memory is logged in access_event automatically.
-- (INSERT per the proposal; UPDATE and DELETE too, because "every write" includes them.)
------------------------------------------------------------------------------------------------
CREATE FUNCTION trg_note_log() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    INSERT INTO access_event (agent_id, asset_id, op, row_ref, event_time, txn_id, outcome)
    VALUES (NEW.author_agent_id, NEW.asset_id, 'INSERT', 'agent_note:' || NEW.note_id, NEW.created_at, txid_current(), 'ALLOWED');
    RETURN NEW;
  ELSIF TG_OP = 'UPDATE' THEN
    INSERT INTO access_event (agent_id, asset_id, op, row_ref, event_time, txn_id, outcome)
    VALUES (NEW.author_agent_id, NEW.asset_id, 'UPDATE', 'agent_note:' || NEW.note_id, clock_timestamp(), txid_current(), 'ALLOWED');
    RETURN NEW;
  ELSE
    INSERT INTO access_event (agent_id, asset_id, op, row_ref, event_time, txn_id, outcome)
    VALUES (OLD.author_agent_id, OLD.asset_id, 'DELETE', 'agent_note:' || OLD.note_id, clock_timestamp(), txid_current(), 'ALLOWED');
    RETURN OLD;
  END IF;
END
$$;
CREATE TRIGGER agent_note_log AFTER INSERT OR UPDATE OR DELETE ON agent_note
  FOR EACH ROW EXECUTE FUNCTION trg_note_log();
