-- 013: Merkle evidence over a campaign's audit trail (docs/METHODS.md §4)
--
-- v1 limitation: append-only triggers stop everyone except a superuser, who can disable them and rewrite the log.
-- v2: when a campaign is frozen, the engine computes a Merkle root over EVERY row that constitutes the evidence and signs it
-- (Ed25519, key outside the database). Any later edit, insertion or deletion of an evidence row changes the recomputed root,
-- so it is detectable by anyone holding the published signature. Inclusion proofs show a single row was in the audited set.
--
-- Canonical format "WALLTEST-EVIDENCE-1" (identical in SQL, Python and the browser):
--   leaf payload = kind || ':' || jsonb_build_object(<the columns listed below>)::text
--                                                                  kinds in this order: 1 canary_slot, 2 canary_variant,
--                                                                  3 sealed_flip, 4 agent_note, 5 access_event, 6 trade_order
--   The column lists are EXPLICIT and frozen: to_jsonb(whole row) would change every leaf -- and silently invalidate every
--   previously signed root -- the first time a later migration added a column to one of these tables. A new format gets a new
--   name; this one never changes.
--   leaf hash    = SHA-256( 0x00 || UTF-8(payload) )               (RFC 6962 domain separation)
--   node hash    = SHA-256( 0x01 || left || right )
--   a level with an odd number of nodes promotes its last node unchanged; root of zero leaves = SHA-256(empty)
-- Scope: the campaign's slots, variants and flips; notes and access events by the wall's agents, and orders by its LOW
-- agents, inside [started_at, closed_at).

CREATE FUNCTION evidence_leaves(p_campaign integer)
RETURNS TABLE (ord bigint, kind text, ref text, payload text, leaf_hash bytea)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
SET extra_float_digits = 1
SET TimeZone = 'UTC'                -- to_jsonb renders timestamptz in the session time zone: pin it, or hashes would vary
AS $$
DECLARE c record;
BEGIN
  SELECT ac.campaign_id, ac.wall_id, ac.started_at, ac.closed_at INTO c FROM audit_campaign ac WHERE ac.campaign_id = p_campaign;
  IF NOT FOUND THEN RAISE EXCEPTION 'no campaign %', p_campaign; END IF;
  IF c.closed_at IS NULL THEN
    RAISE EXCEPTION 'campaign %: evidence is defined once its window is closed (closed_at is NULL)', p_campaign USING ERRCODE = 'WT012';
  END IF;
  IF EXISTS (SELECT 1 FROM canary_slot s WHERE s.campaign_id = p_campaign AND lower(s.slot_period) < c.closed_at
             AND upper(s.slot_period) > clock_timestamp()) THEN
    RAISE EXCEPTION 'campaign %: a slot inside the window has not ended yet (its flip is still sealed)', p_campaign USING ERRCODE = 'WT006';
  END IF;
  RETURN QUERY
  WITH members AS (SELECT m.agent_id, m.side FROM wall_membership m WHERE m.wall_id = c.wall_id),
  rows_ AS (
    SELECT 1 AS k, s.slot_id::bigint AS id, 'canary_slot' AS kind,
           jsonb_build_object('slot_id', s.slot_id, 'campaign_id', s.campaign_id, 'treatment_id', s.treatment_id, 'upsi_id', s.upsi_id,
                              'slot_period', s.slot_period, 'commitment', s.commitment, 'committed_at', s.committed_at)::text AS j
      FROM canary_slot s WHERE s.campaign_id = p_campaign AND lower(s.slot_period) < c.closed_at
    UNION ALL
    SELECT 2, v.variant_id, 'canary_variant',
           jsonb_build_object('variant_id', v.variant_id, 'slot_id', v.slot_id, 'variant_bit', v.variant_bit, 'direction', v.direction,
                              'content', v.content)::text
      FROM canary_variant v JOIN canary_slot s ON s.slot_id = v.slot_id WHERE s.campaign_id = p_campaign AND lower(s.slot_period) < c.closed_at
    UNION ALL
    SELECT 3, f.slot_id, 'sealed_flip', jsonb_build_object('slot_id', f.slot_id, 'flip_bit', f.flip_bit, 'salt', f.salt)::text
      FROM sealed_flip f JOIN canary_slot s ON s.slot_id = f.slot_id WHERE s.campaign_id = p_campaign AND lower(s.slot_period) < c.closed_at
    UNION ALL
    SELECT 4, n.note_id, 'agent_note',
           jsonb_build_object('note_id', n.note_id, 'author_agent_id', n.author_agent_id, 'asset_id', n.asset_id, 'isin', n.isin,
                              'body', n.body, 'embedding', n.embedding::text, 'created_at', n.created_at)::text
      FROM agent_note n WHERE n.author_agent_id IN (SELECT agent_id FROM members)
       AND n.created_at >= c.started_at AND n.created_at < c.closed_at
    UNION ALL
    SELECT 5, e.event_id, 'access_event',
           jsonb_build_object('event_id', e.event_id, 'agent_id', e.agent_id, 'asset_id', e.asset_id, 'op', e.op, 'row_ref', e.row_ref,
                              'event_time', e.event_time, 'txn_id', e.txn_id, 'outcome', e.outcome, 'detail', e.detail)::text
      FROM access_event e WHERE e.agent_id IN (SELECT agent_id FROM members)
       AND e.event_time >= c.started_at AND e.event_time < c.closed_at
    UNION ALL
    SELECT 6, o.order_id, 'trade_order',
           jsonb_build_object('order_id', o.order_id, 'agent_id', o.agent_id, 'isin', o.isin, 'side', o.side, 'quantity', o.quantity,
                              'limit_price', o.limit_price, 'placed_at', o.placed_at)::text
      FROM trade_order o WHERE o.agent_id IN (SELECT agent_id FROM members WHERE side = 'LOW')
       AND o.placed_at >= c.started_at AND o.placed_at < c.closed_at
  )
  SELECT row_number() OVER (ORDER BY r.k, r.id) - 1, r.kind, r.kind || ':' || r.id, r.kind || ':' || r.j,
         digest('\x00'::bytea || convert_to(r.kind || ':' || r.j, 'UTF8'), 'sha256')
  FROM rows_ r ORDER BY r.k, r.id;
END
$$;

-- Merkle levels from a list of leaf hashes (shared by the root and the proof functions).
CREATE FUNCTION merkle_parent_level(lvl bytea[]) RETURNS bytea[]
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE nxt bytea[] := '{}'; n integer := coalesce(array_length(lvl, 1), 0); i integer := 1;
BEGIN
  WHILE i <= n LOOP
    IF i + 1 <= n THEN nxt := nxt || digest('\x01'::bytea || lvl[i] || lvl[i + 1], 'sha256');
    ELSE nxt := nxt || lvl[i]; END IF;
    i := i + 2;
  END LOOP;
  RETURN nxt;
END
$$;

CREATE FUNCTION merkle_root(leaves bytea[]) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lvl bytea[] := coalesce(leaves, '{}');
BEGIN
  IF coalesce(array_length(lvl, 1), 0) = 0 THEN RETURN digest(''::bytea, 'sha256'); END IF;
  WHILE array_length(lvl, 1) > 1 LOOP lvl := merkle_parent_level(lvl); END LOOP;
  RETURN lvl[1];
END
$$;

CREATE FUNCTION evidence_root(p_campaign integer)
RETURNS TABLE (root char(64), leaves integer)
LANGUAGE sql STABLE
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT encode(merkle_root(array_agg(l.leaf_hash ORDER BY l.ord)), 'hex')::char(64), count(*)::integer
  FROM evidence_leaves(p_campaign) l
$$;

-- Inclusion proof for leaf `p_index`: the sibling at each level ('L' = sibling is on the left, 'R' = on the right,
-- 'P' = no sibling, promoted). Verification: h = leaf; for each step h = SHA256(0x01||sib||h) / SHA256(0x01||h||sib) / h.
CREATE FUNCTION evidence_proof(p_campaign integer, p_index integer)
RETURNS TABLE (level integer, side text, sibling char(64))
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lvl bytea[]; idx integer := p_index; n integer; lv integer := 0;
BEGIN
  SELECT array_agg(l.leaf_hash ORDER BY l.ord) INTO lvl FROM evidence_leaves(p_campaign) l;
  n := coalesce(array_length(lvl, 1), 0);
  IF p_index < 0 OR p_index >= n THEN RAISE EXCEPTION 'leaf index % out of range (0..%)', p_index, n - 1; END IF;
  WHILE array_length(lvl, 1) > 1 LOOP
    n := array_length(lvl, 1);
    IF idx % 2 = 1 THEN level := lv; side := 'L'; sibling := encode(lvl[idx], 'hex'); RETURN NEXT;          -- 1-based: lvl[idx] is idx-1
    ELSIF idx + 1 < n THEN level := lv; side := 'R'; sibling := encode(lvl[idx + 2], 'hex'); RETURN NEXT;
    ELSE level := lv; side := 'P'; sibling := NULL; RETURN NEXT;
    END IF;
    lvl := merkle_parent_level(lvl);
    idx := idx / 2; lv := lv + 1;
  END LOOP;
END
$$;

GRANT EXECUTE ON FUNCTION evidence_leaves(integer), merkle_parent_level(bytea[]), merkle_root(bytea[]), evidence_root(integer),
                          evidence_proof(integer, integer) TO audit_engine;
