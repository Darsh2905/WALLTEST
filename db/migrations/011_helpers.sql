-- 011: helpers for the API / browser inspector

-- ISO-8601 UTC text with MICROSECOND precision (JavaScript Date only has milliseconds, so the browser parses this string
-- itself to recompute the commitment preimage; see frontend/src/lib/commitment.ts).
CREATE FUNCTION iso_us(t timestamptz) RETURNS text LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT to_char(t AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') $$;

GRANT EXECUTE ON FUNCTION iso_us(timestamptz) TO audit_engine, compliance;
GRANT SELECT ON security TO high_side;   -- research agent's gateway output joins security; harmless public reference data
