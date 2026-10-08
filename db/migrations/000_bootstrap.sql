-- 000_bootstrap.sql  (runs as the bootstrap superuser; every later migration runs as walltest_owner)
--
-- Doc refs: section 7 (extensions: pgvector, pgcrypto, btree_gist), section 8.4 (role privileges).
--
-- Role model
--   walltest_owner  NOLOGIN  owns every table / function. Table owners bypass RLS, so NOTHING connects as it.
--   high_side       NOLOGIN  inside-area agents (research)
--   low_side        NOLOGIN  public-area agents (trading)
--   audit_engine    NOLOGIN  service account: slots, flips, scoring, verdicts
--   compliance      NOLOGIN  compliance officer: org/grants/campaigns/SDD
--   walltest_api    LOGIN    the ONLY login the API uses. Not superuser, not owner, NOINHERIT: it has no
--                            privileges of its own and must SET ROLE to one of the four roles per request.
--   walltest_lab    LOGIN    used only by the DB Rules Lab endpoint, which runs fixed scripts in rolled-back
--                            transactions. It may SET ROLE walltest_owner so the trigger layer can be shown
--                            after the privilege layer has already refused (see DEVIATIONS.md D-12).

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['walltest_owner','high_side','low_side','audit_engine','compliance'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', r);
    END IF;
  END LOOP;
  FOREACH r IN ARRAY ARRAY['walltest_api','walltest_lab'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT', r);
    END IF;
  END LOOP;
END $$;

-- PG16 membership options: no inheritance (no ambient privileges), SET ROLE allowed.
GRANT high_side, low_side, audit_engine, compliance TO walltest_api WITH INHERIT FALSE, SET TRUE;
GRANT high_side, low_side, audit_engine, compliance, walltest_owner TO walltest_lab WITH INHERIT FALSE, SET TRUE;

CREATE EXTENSION IF NOT EXISTS vector;       -- shared vector memory (agent_note.embedding)
CREATE EXTENSION IF NOT EXISTS pgcrypto;     -- digest() for SHA-256 commitments, gen_random_bytes()
CREATE EXTENSION IF NOT EXISTS btree_gist;   -- EXCLUDE USING gist (campaign_id WITH =, slot_period WITH &&)

-- Nobody but the owner may create objects in public (PG15+ default, made explicit).
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO walltest_owner;
GRANT USAGE ON SCHEMA public TO high_side, low_side, audit_engine, compliance, walltest_api, walltest_lab;

-- Functions are executable by PUBLIC by default; make every grant explicit instead.
ALTER DEFAULT PRIVILEGES FOR ROLE walltest_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Migration bookkeeping lives outside `public` so the application schema is exactly the 20 tables of doc section 8.
CREATE SCHEMA IF NOT EXISTS ops AUTHORIZATION walltest_owner;
