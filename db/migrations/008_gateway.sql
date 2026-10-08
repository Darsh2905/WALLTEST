-- 008: Data-access gateway  (doc 9: "agents work only through the logged gateway")
--
-- Every agent read/write of a shared channel goes through a gw_* function. Each one:
--   1. resolves the agent's ACTIVE SLOT from the clock (LIVE: server clock; SIMULATED: the engine's session clock),
--   2. checks an active access_grant (valid_from <= t < valid_to) for the asset and privilege,
--   3. checks the slot's treatment flag for the channel (vector_memory / notes_table / feature_cache),
--   4. logs the access in access_event. Denied attempts are logged too (outcome = 'DENIED', detail = reason) and the
--      function returns normally with no rows: raising would roll back the very log row we want to keep (D-02).
-- All are SECURITY DEFINER with a pinned search_path (no schema-shadowing). Agents have no direct table privileges.

CREATE TYPE wt_ctx_t AS (
  campaign_id integer, wall_id integer, side text, clock_mode text,
  slot_id integer, upsi_id integer, slot_period tstzrange, now_ts timestamptz,
  vector_on boolean, notes_on boolean, cache_on boolean
);

CREATE FUNCTION wt_sim_now() RETURNS timestamptz LANGUAGE sql STABLE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT nullif(current_setting('walltest.sim_now', true), '')::timestamptz $$;

-- The agent's current context: which running campaign / slot / treatment applies "now" (LIVE) or at the simulated time.
CREATE FUNCTION wt_context(p_agent integer) RETURNS wt_ctx_t
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE r wt_ctx_t; cand record; sl record; t timestamptz; hits integer := 0; first_seen boolean := false;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM agent WHERE agent_id = p_agent AND is_active) THEN
    RAISE EXCEPTION 'unknown or inactive agent %', p_agent USING ERRCODE = 'WT008';
  END IF;
  r.now_ts := clock_timestamp();
  FOR cand IN
    SELECT c.campaign_id, c.wall_id, c.clock_mode, m.side
    FROM wall_membership m JOIN audit_campaign c ON c.wall_id = m.wall_id AND c.status = 'RUNNING'
    WHERE m.agent_id = p_agent ORDER BY c.campaign_id
  LOOP
    IF cand.clock_mode = 'LIVE' THEN
      t := clock_timestamp();
    ELSE
      t := wt_sim_now();
      IF t IS NULL THEN
        RAISE EXCEPTION 'walltest.sim_now is not set for SIMULATED campaign %', cand.campaign_id USING ERRCODE = 'WT010';
      END IF;
    END IF;
    SELECT s.slot_id, s.upsi_id, s.slot_period, tr.vector_memory_on, tr.notes_table_on, tr.cache_on INTO sl
    FROM canary_slot s JOIN treatment tr ON tr.treatment_id = s.treatment_id
    WHERE s.campaign_id = cand.campaign_id AND s.slot_period @> t;
    IF FOUND THEN
      hits := hits + 1;
      r.campaign_id := cand.campaign_id; r.wall_id := cand.wall_id; r.side := cand.side; r.clock_mode := cand.clock_mode;
      r.slot_id := sl.slot_id; r.upsi_id := sl.upsi_id; r.slot_period := sl.slot_period; r.now_ts := t;
      r.vector_on := sl.vector_memory_on; r.notes_on := sl.notes_table_on; r.cache_on := sl.cache_on;
    ELSIF NOT first_seen THEN
      first_seen := true;
      r.campaign_id := cand.campaign_id; r.wall_id := cand.wall_id; r.side := cand.side; r.clock_mode := cand.clock_mode;
      r.now_ts := t;
    END IF;
  END LOOP;
  IF hits > 1 THEN
    RAISE EXCEPTION 'agent % has an active slot in more than one running campaign', p_agent USING ERRCODE = 'WT010';
  END IF;
  RETURN r;
END
$$;

-- Authorisation + denied-attempt logging. Returns ok = false (and has already logged DENIED) when refused.
CREATE FUNCTION wt_gate(p_agent integer, p_asset text, p_priv text, p_need_side text, p_row_ref text)
RETURNS TABLE (ok boolean, asset_id integer, asset_kind text, reason text,
               slot_id integer, upsi_id integer, slot_period tstzrange, now_ts timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE a record; c wt_ctx_t; why text; ch boolean;
BEGIN
  SELECT d.asset_id, d.asset_kind INTO a FROM data_asset d WHERE d.asset_name = p_asset;
  IF NOT FOUND THEN RAISE EXCEPTION 'unknown asset %', p_asset USING ERRCODE = 'WT008'; END IF;
  c := wt_context(p_agent);
  IF c.slot_id IS NULL THEN
    why := 'NO_ACTIVE_SLOT';
  ELSIF p_need_side IS NOT NULL AND c.side IS DISTINCT FROM p_need_side THEN
    why := 'WRONG_SIDE:' || COALESCE(c.side, 'none');
  ELSIF NOT EXISTS (SELECT 1 FROM access_grant g
                    WHERE g.agent_id = p_agent AND g.asset_id = a.asset_id AND g.privilege = p_priv
                      AND g.valid_from <= c.now_ts AND c.now_ts < g.valid_to) THEN
    why := 'NO_GRANT';
  ELSE
    ch := CASE p_asset WHEN 'vector_memory' THEN c.vector_on
                       WHEN 'notes_table'   THEN c.notes_on
                       WHEN 'feature_cache' THEN c.cache_on
                       ELSE true END;
    IF NOT ch THEN why := 'CHANNEL_OFF'; END IF;
  END IF;
  IF why IS NOT NULL THEN
    INSERT INTO access_event (agent_id, asset_id, op, row_ref, event_time, txn_id, outcome, detail)
    VALUES (p_agent, a.asset_id, CASE p_priv WHEN 'WRITE' THEN 'INSERT' ELSE 'READ' END, p_row_ref,
            c.now_ts, txid_current(), 'DENIED', why);
  END IF;
  ok := (why IS NULL); asset_id := a.asset_id; asset_kind := a.asset_kind; reason := why;
  slot_id := c.slot_id; upsi_id := c.upsi_id; slot_period := c.slot_period; now_ts := c.now_ts;
  RETURN NEXT;
END
$$;

CREATE FUNCTION wt_log_read(p_agent integer, p_asset_id integer, p_row_ref text, p_at timestamptz, p_detail text DEFAULT NULL)
RETURNS void LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
  INSERT INTO access_event (agent_id, asset_id, op, row_ref, event_time, txn_id, outcome, detail)
  VALUES (p_agent, p_asset_id, 'READ', p_row_ref, p_at, txid_current(), 'ALLOWED', p_detail)
$$;

------------------------------------------------------------------------------------------------
-- HIGH side: the research agent reads the canary variant chosen by the sealed flip.
-- It never sees the flip itself, nor the other variant, nor which bit it is.
------------------------------------------------------------------------------------------------
CREATE FUNCTION gw_read_canary(p_agent integer)
RETURNS TABLE (slot_id integer, variant_id integer, isin char(12), symbol varchar, company_name varchar,
               category varchar, content text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE g record; u record; v record;
BEGIN
  SELECT * INTO g FROM wt_gate(p_agent, 'canary_variant', 'READ', 'HIGH', NULL);
  IF NOT g.ok THEN RETURN; END IF;
  SELECT x.* INTO g FROM wt_gate(p_agent, 'upsi_item', 'READ', 'HIGH', NULL) x;
  IF NOT g.ok THEN RETURN; END IF;
  SELECT cv.variant_id, cv.content INTO v
  FROM canary_variant cv JOIN sealed_flip f ON f.slot_id = cv.slot_id AND f.flip_bit = cv.variant_bit
  WHERE cv.slot_id = g.slot_id;
  SELECT i.isin, s.symbol, s.company_name, i.category INTO u
  FROM upsi_item i JOIN security s ON s.isin = i.isin WHERE i.upsi_id = g.upsi_id;
  PERFORM wt_log_read(p_agent, (SELECT d.asset_id FROM data_asset d WHERE d.asset_name = 'canary_variant'),
                      'canary_variant:' || v.variant_id, g.now_ts);
  PERFORM wt_log_read(p_agent, g.asset_id, 'upsi_item:' || g.upsi_id, g.now_ts);
  slot_id := g.slot_id; variant_id := v.variant_id; isin := u.isin; symbol := u.symbol;
  company_name := u.company_name; category := u.category; content := v.content;
  RETURN NEXT;
END
$$;

-- Write to a shared channel (notes_table / vector_memory / feature_cache). The AFTER INSERT trigger on agent_note
-- writes the ALLOWED access_event; refusals are logged here.
CREATE FUNCTION gw_write_note(p_agent integer, p_asset text, p_isin char(12), p_body text, p_embedding vector(384) DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE g record; v_id bigint;
BEGIN
  SELECT * INTO g FROM wt_gate(p_agent, p_asset, 'WRITE', NULL, NULL);
  IF NOT g.ok THEN RETURN NULL; END IF;
  IF g.asset_kind = 'VECTOR' AND p_embedding IS NULL THEN
    RAISE EXCEPTION 'asset % is a vector store: an embedding is required', p_asset;
  END IF;
  INSERT INTO agent_note (author_agent_id, asset_id, isin, body, embedding, created_at)
  VALUES (p_agent, g.asset_id, p_isin, p_body, p_embedding, g.now_ts) RETURNING note_id INTO v_id;
  RETURN v_id;
END
$$;

-- Read a TABLE/CACHE channel. Memory is scoped to the audit slot: only rows written since the slot opened are visible,
-- so slots are statistically independent (a stale note from slot i cannot influence slot i+1).
CREATE FUNCTION gw_read_notes(p_agent integer, p_asset text, p_limit integer DEFAULT 5)
RETURNS TABLE (note_id bigint, isin char(12), body text, created_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE g record; r record; n integer := 0;
BEGIN
  SELECT * INTO g FROM wt_gate(p_agent, p_asset, 'READ', NULL, NULL);
  IF NOT g.ok THEN RETURN; END IF;
  FOR r IN SELECT an.note_id, an.isin, an.body, an.created_at FROM agent_note an
           WHERE an.asset_id = g.asset_id AND an.created_at >= lower(g.slot_period) AND an.created_at <= g.now_ts
           ORDER BY an.created_at DESC, an.note_id DESC LIMIT p_limit
  LOOP
    n := n + 1;
    PERFORM wt_log_read(p_agent, g.asset_id, 'agent_note:' || r.note_id, g.now_ts);
    note_id := r.note_id; isin := r.isin; body := r.body; created_at := r.created_at;
    RETURN NEXT;
  END LOOP;
  IF n = 0 THEN PERFORM wt_log_read(p_agent, g.asset_id, NULL, g.now_ts, 'EMPTY'); END IF;
END
$$;

-- pgvector nearest-neighbour search over the vector memory (cosine distance, exact scan).
CREATE FUNCTION gw_vector_search(p_agent integer, p_query vector(384), p_limit integer DEFAULT 3)
RETURNS TABLE (note_id bigint, isin char(12), body text, similarity double precision, created_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE g record; r record; n integer := 0;
BEGIN
  SELECT * INTO g FROM wt_gate(p_agent, 'vector_memory', 'READ', NULL, NULL);
  IF NOT g.ok THEN RETURN; END IF;
  FOR r IN SELECT an.note_id, an.isin, an.body, 1 - (an.embedding <=> p_query) AS sim, an.created_at
           FROM agent_note an
           WHERE an.asset_id = g.asset_id AND an.embedding IS NOT NULL
             AND an.created_at >= lower(g.slot_period) AND an.created_at <= g.now_ts
           ORDER BY an.embedding <=> p_query LIMIT p_limit
  LOOP
    n := n + 1;
    PERFORM wt_log_read(p_agent, g.asset_id, 'agent_note:' || r.note_id, g.now_ts);
    note_id := r.note_id; isin := r.isin; body := r.body; similarity := r.sim; created_at := r.created_at;
    RETURN NEXT;
  END LOOP;
  IF n = 0 THEN PERFORM wt_log_read(p_agent, g.asset_id, NULL, g.now_ts, 'EMPTY'); END IF;
END
$$;

-- Market data: last p_lookback closes per security up to p_as_of (one logged READ).
CREATE FUNCTION gw_read_prices(p_agent integer, p_as_of date, p_lookback integer DEFAULT 21)
RETURNS TABLE (isin char(12), symbol varchar, trade_date date, close_px numeric)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE g record;
BEGIN
  SELECT * INTO g FROM wt_gate(p_agent, 'daily_price', 'READ', NULL, 'daily_price:' || p_as_of);
  IF NOT g.ok THEN RETURN; END IF;
  PERFORM wt_log_read(p_agent, g.asset_id, 'daily_price:' || p_as_of, g.now_ts);
  RETURN QUERY
    SELECT x.isin, x.symbol, x.trade_date, x.close_px FROM (
      SELECT d.isin, s.symbol, d.trade_date, d.close_px,
             row_number() OVER (PARTITION BY d.isin ORDER BY d.trade_date DESC) AS rn
      FROM daily_price d JOIN security s ON s.isin = d.isin WHERE d.trade_date <= p_as_of) x
    WHERE x.rn <= p_lookback ORDER BY x.isin, x.trade_date;
END
$$;

-- Orders: LOW-side agents only. placed_at comes from the slot clock, not from the caller.
CREATE FUNCTION gw_place_orders(p_agent integer, p_isins text[], p_sides text[], p_qty integer, p_limits numeric[] DEFAULT NULL)
RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE c wt_ctx_t; i integer; n integer := 0;
BEGIN
  c := wt_context(p_agent);
  IF NOT EXISTS (SELECT 1 FROM wall_membership m WHERE m.agent_id = p_agent AND m.side = 'LOW') THEN
    RAISE EXCEPTION 'agent % is not a LOW-side agent: only trading agents place orders', p_agent USING ERRCODE = 'WT008';
  END IF;
  IF c.campaign_id IS NOT NULL AND c.slot_id IS NULL THEN
    RETURN 0;                         -- the slot is over: a late order is simply not placed (it counts as "no trade")
  END IF;
  FOR i IN 1..COALESCE(array_length(p_isins, 1), 0) LOOP
    INSERT INTO trade_order (agent_id, isin, side, quantity, limit_price, placed_at)
    VALUES (p_agent, p_isins[i], p_sides[i], p_qty, CASE WHEN p_limits IS NULL THEN NULL ELSE p_limits[i] END, c.now_ts);
    n := n + 1;
  END LOOP;
  RETURN n;
END
$$;

CREATE FUNCTION gw_place_order(p_agent integer, p_isin text, p_side text, p_qty integer, p_limit numeric DEFAULT NULL)
RETURNS integer LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT gw_place_orders(p_agent, ARRAY[p_isin], ARRAY[p_side], p_qty, CASE WHEN p_limit IS NULL THEN NULL ELSE ARRAY[p_limit] END) $$;
