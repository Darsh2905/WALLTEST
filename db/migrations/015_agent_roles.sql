-- 015: Per-agent database logins (docs/METHODS.md §5). Closes the v1 limitation "roles are per side, not per agent":
-- in v1 a LOW-side connection passed p_agent to the gateway and could present another LOW agent's id.
-- v2: every agent has its own LOGIN role (wt_agent_<name>), a member of its side role. Gateway functions take NO agent id:
-- they identify the caller from the session. Roles are created by walltest/roles.py (CREATE ROLE needs a privileged login).
--
-- Caller identity = the role in effect for the session: current_setting('role') if a SET ROLE is active (it reports the SET
-- ROLE value even inside a SECURITY DEFINER function), otherwise session_user (an agent's own login sets no role: 'none').
-- An agent login is a member of nothing it can SET ROLE to, so in production the caller is always the agent itself.

ALTER TABLE agent ADD COLUMN db_role varchar(63) UNIQUE CHECK (db_role ~ '^wt_agent_[a-z0-9_]+$');

CREATE FUNCTION wt_caller_agent() RETURNS integer
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE r text := coalesce(nullif(current_setting('role', true), 'none'), session_user); a integer;
BEGIN
  SELECT agent_id INTO a FROM agent WHERE db_role = r AND is_active;
  IF a IS NULL THEN
    RAISE EXCEPTION 'gateway: role "%" is not an active agent role (agents authenticate by their own database role)', r
      USING ERRCODE = 'WT011';
  END IF;
  RETURN a;
END
$$;

-- v1 entry points become internal implementations (not executable by any agent or side role).
ALTER FUNCTION gw_read_canary(integer) RENAME TO wt_impl_read_canary;
ALTER FUNCTION gw_write_note(integer, text, char, text, vector) RENAME TO wt_impl_write_note;
ALTER FUNCTION gw_read_notes(integer, text, integer) RENAME TO wt_impl_read_notes;
ALTER FUNCTION gw_vector_search(integer, vector, integer) RENAME TO wt_impl_vector_search;
ALTER FUNCTION gw_read_prices(integer, date, integer) RENAME TO wt_impl_read_prices;
ALTER FUNCTION gw_place_orders(integer, text[], text[], integer, numeric[]) RENAME TO wt_impl_place_orders;
DROP FUNCTION gw_place_order(integer, text, text, integer, numeric);
REVOKE EXECUTE ON FUNCTION wt_impl_read_canary(integer), wt_impl_write_note(integer, text, char, text, vector),
                           wt_impl_read_notes(integer, text, integer), wt_impl_vector_search(integer, vector, integer),
                           wt_impl_read_prices(integer, date, integer), wt_impl_place_orders(integer, text[], text[], integer, numeric[])
       FROM high_side, low_side;

-- v2 gateway: the caller is whoever the session's role says it is.
CREATE FUNCTION gw_read_canary()
RETURNS TABLE (slot_id integer, variant_id integer, isin char(12), symbol varchar, company_name varchar, category varchar, content text)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT * FROM wt_impl_read_canary(wt_caller_agent()) $$;

CREATE FUNCTION gw_write_note(p_asset text, p_isin char(12), p_body text, p_embedding vector(384) DEFAULT NULL)
RETURNS bigint LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT wt_impl_write_note(wt_caller_agent(), p_asset, p_isin, p_body, p_embedding) $$;

CREATE FUNCTION gw_read_notes(p_asset text, p_limit integer DEFAULT 5)
RETURNS TABLE (note_id bigint, isin char(12), body text, created_at timestamptz)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT * FROM wt_impl_read_notes(wt_caller_agent(), p_asset, p_limit) $$;

CREATE FUNCTION gw_vector_search(p_query vector(384), p_limit integer DEFAULT 3)
RETURNS TABLE (note_id bigint, isin char(12), body text, similarity double precision, created_at timestamptz)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT * FROM wt_impl_vector_search(wt_caller_agent(), p_query, p_limit) $$;

CREATE FUNCTION gw_read_prices(p_as_of date, p_lookback integer DEFAULT 21)
RETURNS TABLE (isin char(12), symbol varchar, trade_date date, close_px numeric)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT * FROM wt_impl_read_prices(wt_caller_agent(), p_as_of, p_lookback) $$;

CREATE FUNCTION gw_place_orders(p_isins text[], p_sides text[], p_qty integer, p_limits numeric[] DEFAULT NULL)
RETURNS integer LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT wt_impl_place_orders(wt_caller_agent(), p_isins, p_sides, p_qty, p_limits) $$;

CREATE FUNCTION gw_place_order(p_isin text, p_side text, p_qty integer, p_limit numeric DEFAULT NULL)
RETURNS integer LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT gw_place_orders(ARRAY[p_isin], ARRAY[p_side], p_qty, CASE WHEN p_limit IS NULL THEN NULL ELSE ARRAY[p_limit] END) $$;

REVOKE EXECUTE ON FUNCTION wt_caller_agent() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gw_read_canary(), gw_write_note(text, char, text, vector), gw_read_notes(text, integer) TO high_side;
GRANT EXECUTE ON FUNCTION gw_read_prices(date, integer), gw_read_notes(text, integer), gw_vector_search(vector, integer),
                          gw_place_orders(text[], text[], integer, numeric[]), gw_place_order(text, text, integer, numeric) TO low_side;
