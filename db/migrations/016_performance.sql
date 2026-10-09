-- 016: Performance (docs/PLAN_V2.md P-1..P-5; measured in docs/benchmarks/). Three kinds of change:
--   1. Statistical kernels: every bound / power quantity is a Beta quantile. A log-space regularised incomplete beta
--      (modified Lentz continued fraction, O(sqrt n) per evaluation) + safeguarded Newton (~6 evaluations) replaces
--      ~60-step bisections over O(n)-term binomial sums. The old bodies are kept as *_bisect reference implementations;
--      backend/tests/test_stats_v2.py requires both to agree (and both to agree with scipy).
--   2. Indexes for the measured hot paths (exposure trail, evidence windows, scoring, campaign time windows), and an
--      exact compliance semantic search over DISTINCT vectors (HNSW was evaluated and rejected: see section 4).
--   3. Gateway: the vector search is made exact BY CONSTRUCTION (whatever vector indexes exist now or later); price reads
--      become top-N index reads (the largest single cost in a profiled campaign).

------------------------------------------------------------------------------------------------
-- 1. Kernels
------------------------------------------------------------------------------------------------
-- exp() that returns 0 / +Infinity instead of raising (PostgreSQL's exp() raises "value out of range: underflow" below ~-745)
CREATE FUNCTION exp_safe(v double precision) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT CASE WHEN v < -708 THEN 0::double precision WHEN v > 709 THEN 'Infinity'::double precision ELSE exp(v) END $$;

-- ln I_x(a, b), the regularised incomplete beta function, in log space (Numerical Recipes betacf, modified Lentz).
-- The continued fraction converges fast for x < (a+1)/(a+b+2); otherwise use I_x(a,b) = 1 - I_{1-x}(b,a).
-- lb = ln B(a, b) is passed in: root finders call this many times with the same (a, b) (profiled: recomputing it made
-- ln_gamma the single most expensive function in campaign_inference).
CREATE FUNCTION ln_ibeta_lb(x double precision, a double precision, b double precision, lb double precision) RETURNS double precision
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
  fpmin CONSTANT double precision := 1e-300; eps CONSTANT double precision := 1e-15;   -- ~4.5 ulp: reachable in double precision
  sw boolean; aa double precision; bb double precision; xx double precision;
  c double precision; d double precision; h double precision; del double precision; num double precision;
  m2 integer; lcf double precision; y double precision;
BEGIN
  IF a <= 0 OR b <= 0 THEN RAISE EXCEPTION 'ln_ibeta needs a, b > 0'; END IF;
  IF x <= 0 THEN RETURN '-Infinity'; END IF;
  IF x >= 1 THEN RETURN 0; END IF;
  sw := x > (a + 1) / (a + b + 2);
  IF sw THEN aa := b; bb := a; xx := 1 - x; ELSE aa := a; bb := b; xx := x; END IF;
  c := 1; d := 1 - (aa + bb) * xx / (aa + 1);
  IF abs(d) < fpmin THEN d := fpmin; END IF;
  d := 1 / d; h := d;
  FOR m IN 1..20000 LOOP
    m2 := 2 * m;
    num := m * (bb - m) * xx / ((aa - 1 + m2) * (aa + m2));
    d := 1 + num * d; IF abs(d) < fpmin THEN d := fpmin; END IF;
    c := 1 + num / c; IF abs(c) < fpmin THEN c := fpmin; END IF;
    d := 1 / d; h := h * d * c;
    num := -(aa + m) * (aa + bb + m) * xx / ((aa + m2) * (aa + 1 + m2));
    d := 1 + num * d; IF abs(d) < fpmin THEN d := fpmin; END IF;
    c := 1 + num / c; IF abs(c) < fpmin THEN c := fpmin; END IF;
    d := 1 / d; del := d * c; h := h * del;
    EXIT WHEN abs(del - 1) < eps;
  END LOOP;
  lcf := aa * ln(xx) + bb * ln(1 - xx) - lb - ln(aa) + ln(h);       -- ln I_xx(aa, bb)   (B is symmetric)
  IF NOT sw THEN RETURN LEAST(lcf, 0); END IF;
  y := exp_safe(LEAST(lcf, 0));                       -- I = 1 - y; keep precision when y is small (no log1p in PG 16)
  IF y < 1e-100 THEN RETURN -y; END IF;               -- (PostgreSQL raises on float underflow, so never cube a tiny y)
  IF y < 1e-5 THEN RETURN -(y + y * y / 2 + y * y * y / 3); END IF;
  RETURN ln(1 - y);
END
$$;

CREATE FUNCTION ln_ibeta(x double precision, a double precision, b double precision) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT ln_ibeta_lb(x, a, b, ln_beta(a, b)) $$;

-- ln of the Beta(a, b) density at x
CREATE FUNCTION ln_beta_pdf(x double precision, a double precision, b double precision) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT (a - 1) * ln(x) + (b - 1) * ln(1 - x) - ln_beta(a, b) $$;

-- Standard normal quantile from the LOG of a tail probability (Abramowitz & Stegun 26.2.23, |error| < 4.5e-4): a cheap,
-- underflow-free starting point for the Newton iterations below (exact roots come from Newton, not from this).
CREATE FUNCTION norm_ppf_from_ln(lp double precision) RETURNS double precision
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE t double precision; lq double precision; z double precision;
BEGIN
  IF lp >= 0 THEN RETURN 'Infinity'; END IF;
  IF lp <= ln(0.5::double precision) THEN
    t := sqrt(-2 * lp);
    RETURN -(t - (2.515517 + 0.802853 * t + 0.010328 * t * t) / (1 + 1.432788 * t + 0.189269 * t * t + 0.001308 * t * t * t));
  END IF;
  lq := ln(1 - exp_safe(lp));
  t := sqrt(-2 * lq);
  RETURN t - (2.515517 + 0.802853 * t + 0.010328 * t * t) / (1 + 1.432788 * t + 0.189269 * t * t + 0.001308 * t * t * t);
END
$$;

-- The x in (0, 1) with ln I_x(a, b) = la: safeguarded Newton (d ln I / dx = pdf / I) inside a bisection bracket, started
-- from the normal approximation to Beta(a, b) at the target tail (profiled: starting at the mean took ~18 iterations; this ~4).
CREATE FUNCTION beta_ppf_ln(la double precision, a double precision, b double precision) RETURNS double precision
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := 0; hi double precision := 1; x double precision; xn double precision; f double precision;
        li double precision; dd double precision; lb double precision := ln_beta(a, b); mu double precision; sd double precision;
BEGIN
  IF la >= 0 THEN RETURN 1; END IF;
  mu := a / (a + b); sd := sqrt(a * b / ((a + b) * (a + b) * (a + b + 1)));
  x := mu + norm_ppf_from_ln(la) * sd;
  IF NOT (x > 0 AND x < 1) THEN x := CASE WHEN x <= 0 THEN mu / 2 ELSE (1 + mu) / 2 END; END IF;
  FOR i IN 1..200 LOOP
    li := ln_ibeta_lb(x, a, b, lb);
    f := li - la;
    IF f > 0 THEN hi := x; ELSE lo := x; END IF;
    IF f = 0 THEN RETURN x; END IF;
    dd := exp_safe((a - 1) * ln(x) + (b - 1) * ln(1 - x) - lb - li);
    xn := CASE WHEN dd > 0 AND dd < 'Infinity' THEN x - f / dd ELSE (lo + hi) / 2 END;   -- (PG raises on x/0)
    -- convergence BEFORE the bracket safeguard: a converged step lands within an ulp of the bracket end just set to x, and
    -- the strict safeguard would then bisect away from the root (profiled: ~30 wasted evaluations on skewed Betas)
    IF abs(xn - x) <= 1e-14 * x OR hi - lo <= 1e-14 * hi THEN RETURN xn; END IF;
    IF NOT (xn > lo AND xn < hi) THEN xn := (lo + hi) / 2; END IF;      -- (also catches NaN)
    x := xn;
  END LOOP;
  RETURN x;
END
$$;

-- Keep the v1/v2 bisection implementations as references (identical bodies, new names).
CREATE FUNCTION clopper_pearson_lower_bisect(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := 0; hi double precision := 1; mid double precision; la double precision := ln(alpha);
BEGIN
  IF k = 0 THEN RETURN 0; END IF;
  FOR i IN 1..60 LOOP
    mid := (lo + hi) / 2;
    IF binom_log_sf(n, k, mid) < la THEN lo := mid; ELSE hi := mid; END IF;
  END LOOP;
  RETURN (lo + hi) / 2;
END
$$;

CREATE FUNCTION clopper_pearson_upper_bisect(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := 0; hi double precision := 1; mid double precision; la double precision := ln(alpha);
BEGIN
  IF k = n THEN RETURN 1; END IF;
  FOR i IN 1..60 LOOP
    mid := (lo + hi) / 2;
    IF binom_log_cdf(n, k, mid) > la THEN lo := mid; ELSE hi := mid; END IF;
  END LOOP;
  RETURN (lo + hi) / 2;
END
$$;

CREATE FUNCTION log_evalue_mix_sum(n integer, k integer, p0 double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT ln_beta(k + 1.0, n - k + 1.0) + binom_log_cdf(n + 1, k, p0) - ln(1 - p0) - k * ln(p0) - (n - k) * ln(1 - p0) $$;

CREATE FUNCTION log_evalue_mix_upper_sum(n integer, k integer, p0 double precision)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT ln_beta(k + 1.0, n - k + 1.0) + binom_log_sf(n + 1, k + 1, p0) - ln(p0) - k * ln(p0) - (n - k) * ln(1 - p0) $$;

-- Now the fast versions, under the original names (CREATE OR REPLACE keeps OIDs and grants: every caller speeds up).
CREATE OR REPLACE FUNCTION clopper_pearson_lower(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  IF k < 0 OR k > n OR n < 1 THEN RAISE EXCEPTION 'need 0 <= k <= n, n >= 1'; END IF;
  IF alpha <= 0 OR alpha >= 1 THEN RAISE EXCEPTION 'alpha must be in (0,1)'; END IF;
  IF k = 0 THEN RETURN 0; END IF;
  RETURN beta_ppf_ln(ln(alpha), k, n - k + 1);                      -- P(X >= k | n, p) = I_p(k, n-k+1) = alpha
END
$$;

CREATE OR REPLACE FUNCTION clopper_pearson_upper(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  IF k < 0 OR k > n OR n < 1 THEN RAISE EXCEPTION 'need 0 <= k <= n, n >= 1'; END IF;
  IF k = n THEN RETURN 1; END IF;
  RETURN 1 - beta_ppf_ln(ln(alpha), n - k, k + 1);                  -- P(X <= k | n, p) = I_{1-p}(n-k, k+1) = alpha
END
$$;

CREATE OR REPLACE FUNCTION min_detectable_acc(n integer, alpha double precision, target double precision DEFAULT 0.8)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE kc integer;
BEGIN
  kc := binom_critical_k(n, alpha);
  IF kc > n THEN RETURN NULL; END IF;
  IF kc <= 0 THEN RETURN 0.5; END IF;
  RETURN GREATEST(0.5, beta_ppf_ln(ln(target), kc, n - kc + 1));     -- power(a) = P(X >= kc | n, a) = I_a(kc, n-kc+1)
END
$$;

CREATE OR REPLACE FUNCTION log_evalue_mix(n integer, k integer, p0 double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT ln_beta(k + 1.0, n - k + 1.0) + ln_ibeta_lb(1 - p0, n + 1.0 - k, k + 1.0, ln_beta(n + 1.0 - k, k + 1.0)) - k * ln(p0) - (n - k + 1) * ln(1 - p0) $$;

CREATE OR REPLACE FUNCTION log_evalue_mix_upper(n integer, k integer, p0 double precision)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT lb + ln_ibeta_lb(p0, k + 1.0, n - k + 1.0, lb) - (k + 1) * ln(p0) - (n - k) * ln(1 - p0) FROM ln_beta(k + 1.0, n - k + 1.0) AS lb $$;

CREATE FUNCTION cs_lower_bisect(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := 1e-12; hi double precision; mid double precision; thr double precision := -ln(alpha);
BEGIN
  IF n = 0 OR k = 0 THEN RETURN 0; END IF;
  hi := k::double precision / n;
  IF hi >= 1 THEN hi := 1 - 1e-12; END IF;
  IF log_evalue_mix_sum(n, k, lo) < thr THEN RETURN 0; END IF;
  FOR i IN 1..60 LOOP
    mid := (lo + hi) / 2;
    IF log_evalue_mix_sum(n, k, mid) >= thr THEN lo := mid; ELSE hi := mid; END IF;
  END LOOP;
  RETURN lo;
END
$$;

CREATE FUNCTION cs_upper_bisect(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision; hi double precision := 1 - 1e-12; mid double precision; thr double precision := -ln(alpha);
BEGIN
  IF n = 0 OR k = n THEN RETURN 1; END IF;
  lo := k::double precision / n;
  IF lo <= 0 THEN lo := 1e-12; END IF;
  IF log_evalue_mix_upper_sum(n, k, hi) < thr THEN RETURN 1; END IF;
  FOR i IN 1..60 LOOP
    mid := (lo + hi) / 2;
    IF log_evalue_mix_upper_sum(n, k, mid) >= thr THEN hi := mid; ELSE lo := mid; END IF;
  END LOOP;
  RETURN hi;
END
$$;

-- Confidence-sequence ends by safeguarded Newton on ln E(p0) - ln(1/alpha), with the analytic derivative:
--   ln E(p0)   = C + ln I_{1-p0}(n+1-k, k+1) - k ln p0 - (n-k+1) ln(1-p0)
--   d/dp0      = -pdf_{n+1-k,k+1}(1-p0) / I_{1-p0}(n+1-k, k+1) - k/p0 + (n-k+1)/(1-p0)
--   ln E_u(p0) = C + ln I_{p0}(k+1, n-k+1) - (k+1) ln p0 - (n-k) ln(1-p0)
--   d/dp0      =  pdf_{k+1,n-k+1}(p0) / I_{p0}(k+1, n-k+1) - (k+1)/p0 + (n-k)/(1-p0)
-- The returned end is nudged to the conservative (wider) side by 1e-13 relative, as the bisection versions return the outer end.
CREATE OR REPLACE FUNCTION cs_lower(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := 1e-12; hi double precision; x double precision; xn double precision; f double precision;
        df double precision; li double precision; thr double precision := -ln(alpha); a double precision := n + 1.0 - k; b double precision := k + 1.0;
        lb double precision; c0 double precision;
BEGIN
  IF n = 0 OR k = 0 THEN RETURN 0; END IF;
  lb := ln_beta(a, b); c0 := ln_beta(k + 1.0, n - k + 1.0);
  hi := LEAST(k::double precision / n, 1 - 1e-12);
  IF log_evalue_mix(n, k, lo) < thr THEN RETURN 0; END IF;
  x := (lo + hi) / 2;
  FOR i IN 1..200 LOOP
    li := ln_ibeta_lb(1 - x, a, b, lb);
    f := c0 + li - k * ln(x) - (n - k + 1) * ln(1 - x) - thr;                                -- decreasing in x on (0, k/n]
    IF f >= 0 THEN lo := x; ELSE hi := x; END IF;
    IF hi - lo <= 1e-15 * hi THEN RETURN lo; END IF;
    df := -exp_safe((a - 1) * ln(1 - x) + (b - 1) * ln(x) - lb - li) - k / x + (n - k + 1) / (1 - x);
    xn := CASE WHEN df <> 0 AND abs(df) < 'Infinity' THEN x - f / df ELSE (lo + hi) / 2 END;
    IF abs(xn - x) <= 1e-15 * x THEN RETURN xn * (1 - 1e-13); END IF;     -- converged (tested before the safeguard); nudge wider
    IF NOT (xn > lo AND xn < hi) THEN xn := (lo + hi) / 2; END IF;
    x := xn;
  END LOOP;
  RETURN lo;
END
$$;

CREATE OR REPLACE FUNCTION cs_upper(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision; hi double precision := 1 - 1e-12; x double precision; xn double precision; f double precision;
        df double precision; li double precision; thr double precision := -ln(alpha); a double precision := k + 1.0; b double precision := n - k + 1.0;
        lb double precision;
BEGIN
  IF n = 0 OR k = n THEN RETURN 1; END IF;
  lb := ln_beta(a, b);                                 -- here a, b = k+1, n-k+1, so lb is also the constant C
  lo := GREATEST(k::double precision / n, 1e-12);
  IF log_evalue_mix_upper(n, k, hi) < thr THEN RETURN 1; END IF;
  x := (lo + hi) / 2;
  FOR i IN 1..200 LOOP
    li := ln_ibeta_lb(x, a, b, lb);
    f := lb + li - (k + 1) * ln(x) - (n - k) * ln(1 - x) - thr;                               -- increasing in x on [k/n, 1)
    IF f >= 0 THEN hi := x; ELSE lo := x; END IF;
    IF hi - lo <= 1e-15 * hi THEN RETURN hi; END IF;
    df := exp_safe((a - 1) * ln(x) + (b - 1) * ln(1 - x) - lb - li) - (k + 1) / x + (n - k) / (1 - x);
    xn := CASE WHEN df <> 0 AND abs(df) < 'Infinity' THEN x - f / df ELSE (lo + hi) / 2 END;
    IF abs(xn - x) <= 1e-15 * x THEN RETURN LEAST(1, xn * (1 + 1e-13)); END IF;
    IF NOT (xn > lo AND xn < hi) THEN xn := (lo + hi) / 2; END IF;
    x := xn;
  END LOOP;
  RETURN hi;
END
$$;

GRANT EXECUTE ON FUNCTION exp_safe(double precision), norm_ppf_from_ln(double precision),
                          ln_ibeta_lb(double precision, double precision, double precision, double precision), ln_ibeta(double precision, double precision, double precision), ln_beta_pdf(double precision, double precision, double precision),
                          beta_ppf_ln(double precision, double precision, double precision),
                          clopper_pearson_lower_bisect(integer, integer, double precision), clopper_pearson_upper_bisect(integer, integer, double precision),
                          log_evalue_mix_sum(integer, integer, double precision), log_evalue_mix_upper_sum(integer, integer, double precision),
                          cs_lower_bisect(integer, integer, double precision), cs_upper_bisect(integer, integer, double precision)
      TO audit_engine, compliance;

------------------------------------------------------------------------------------------------
-- 2. Indexes (each answers a plan measured in docs/benchmarks/v2-pre-016.json)
------------------------------------------------------------------------------------------------
-- exposure_trail: "row_ref IN (derived notes)" was a sequential scan of access_event
CREATE INDEX access_event_row_ref_idx ON access_event (row_ref) WHERE row_ref IS NOT NULL;
-- exposure_trail: notes about the slot's security inside the slot window (no asset filter)
CREATE INDEX agent_note_isin_time_idx ON agent_note (isin, created_at);
-- evidence_leaves: notes / orders of the wall's agents inside [started_at, closed_at)
CREATE INDEX agent_note_author_time_idx ON agent_note (author_agent_id, created_at);
CREATE INDEX trade_order_agent_time_idx ON trade_order (agent_id, placed_at);
-- scoring (v_slot_score's per-slot net position): covering, so the sum is an index-only scan on the append-only table
CREATE INDEX trade_order_score_idx ON trade_order (agent_id, isin, placed_at) INCLUDE (side, quantity);
DROP INDEX IF EXISTS trade_order_agent_isin_time_idx;
-- time windows over all agents (access_summary: a campaign's window; exposure_trail / v_lag: one slot's window).
-- BRIN was tried first (the column is written in time order, correlation 0.9999) and REJECTED on measurement: it made the
-- campaign-wide aggregate 17x faster, but the planner also chose it for the per-slot 2-second windows, where every probe
-- reads whole 32-page block ranges -- v_lag went from 2 ms to 68 ms. A B-tree (12 MB at 1M rows) is as fast or faster for
-- every query that exists (docs/benchmarks/: v_lag 2.1 ms, access_summary 0.75 ms, exposure_trail 0.63 ms).
CREATE INDEX access_event_time_idx ON access_event (event_time);
-- Compliance semantic search works on DISTINCT vectors. On the calibration corpus (199,200 embedded notes) only 11.9% of
-- the embeddings are distinct (agents repeat themselves; templated paraphrases), so every distinct vector gets ONE
-- representative row (the earliest note carrying it), found again through an exact hash of the vector.
CREATE FUNCTION embedding_key(v vector) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT digest(v::text, 'sha256') $$;         -- vector_out is the shortest exact decimal: equal vectors <=> equal text

ALTER TABLE agent_note ADD COLUMN embedding_canonical boolean NOT NULL DEFAULT false;
-- (key, note_id): the representative check needs the key; the search's expansion takes the lowest note_ids of a key.
CREATE INDEX agent_note_embedding_key_idx ON agent_note (embedding_key(embedding), note_id) WHERE embedding IS NOT NULL;

-- Set at INSERT (a BEFORE trigger edits NEW: no second write, so nothing extra reaches the access log). Two concurrent
-- inserts of the same new vector may both become representatives: harmless (search de-duplicates by key).
CREATE FUNCTION trg_agent_note_canonical() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  NEW.embedding_canonical := NEW.embedding IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_note x WHERE x.embedding IS NOT NULL AND embedding_key(x.embedding) = embedding_key(NEW.embedding)
      AND x.note_id <> NEW.note_id);
  RETURN NEW;
END
$$;
CREATE TRIGGER agent_note_canonical BEFORE INSERT OR UPDATE OF embedding ON agent_note
  FOR EACH ROW EXECUTE FUNCTION trg_agent_note_canonical();

-- Backfill existing notes. This is maintenance, not agent activity: the access-log trigger is suspended for it.
ALTER TABLE agent_note DISABLE TRIGGER agent_note_log;
UPDATE agent_note n SET embedding_canonical = true
FROM (SELECT min(note_id) AS id FROM agent_note WHERE embedding IS NOT NULL GROUP BY embedding_key(embedding)) f
WHERE n.note_id = f.id;
ALTER TABLE agent_note ENABLE TRIGGER agent_note_log;

-- The representatives' vectors, as a covering partial index: an exact scan of every distinct vector is an index-only scan.
CREATE INDEX agent_note_vector_reps_idx ON agent_note (note_id) INCLUDE (embedding) WHERE embedding_canonical;

------------------------------------------------------------------------------------------------
-- 3. Gateway: vector search exact over the slot's notes by construction; top-N price reads
------------------------------------------------------------------------------------------------
-- v1 wrote `ORDER BY embedding <=> q LIMIT n` with the slot filter in WHERE. If an approximate vector index (HNSW) on
-- agent_note ever exists -- one was tried in this very migration -- the planner may answer that from the index: the
-- ~ef_search globally nearest notes are fetched FIRST and the slot filter applied after, which typically leaves none of the
-- slot's notes. The vector channel would silently carry nothing and a real leak would read as NO_EVIDENCE. The MATERIALIZED
-- CTE fixes the candidate set (the slot's notes) before any ordering, so the search is an exact scan of that set whatever
-- indexes exist. tests/test_gateway.py demonstrates the hazard with an HNSW index and checks the gateway stays exact.
CREATE OR REPLACE FUNCTION wt_impl_vector_search(p_agent integer, p_query vector(384), p_limit integer DEFAULT 3)
RETURNS TABLE (note_id bigint, isin char(12), body text, similarity double precision, created_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE g record; r record; n integer := 0;
BEGIN
  SELECT * INTO g FROM wt_gate(p_agent, 'vector_memory', 'READ', NULL, NULL);
  IF NOT g.ok THEN RETURN; END IF;
  FOR r IN WITH cand AS MATERIALIZED (
             SELECT an.note_id, an.isin, an.body, an.embedding, an.created_at
             FROM agent_note an
             WHERE an.asset_id = g.asset_id AND an.embedding IS NOT NULL
               AND an.created_at >= lower(g.slot_period) AND an.created_at <= g.now_ts)
           SELECT cand.note_id, cand.isin, cand.body, 1 - (cand.embedding <=> p_query) AS sim, cand.created_at
           FROM cand ORDER BY cand.embedding <=> p_query, cand.note_id LIMIT p_limit
  LOOP
    n := n + 1;
    PERFORM wt_log_read(p_agent, g.asset_id, 'agent_note:' || r.note_id, g.now_ts);
    note_id := r.note_id; isin := r.isin; body := r.body; similarity := r.sim; created_at := r.created_at;
    RETURN NEXT;
  END LOOP;
  IF n = 0 THEN PERFORM wt_log_read(p_agent, g.asset_id, NULL, g.now_ts, 'EMPTY'); END IF;
END
$$;

-- Market data (every trader, every slot). Profiled as 42% of all database function time in a campaign (1.28 ms/call): v1
-- numbered EVERY price row up to p_as_of with a window function to keep the last p_lookback per security. Top-N per
-- security from the (isin, trade_date) primary key reads only the rows returned. Same rows, same order (tested against
-- the v1 query in tests/test_gateway.py).
CREATE OR REPLACE FUNCTION wt_impl_read_prices(p_agent integer, p_as_of date, p_lookback integer DEFAULT 21)
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
    SELECT s.isin, s.symbol, x.trade_date, x.close_px
    FROM security s
    CROSS JOIN LATERAL (SELECT d.trade_date, d.close_px FROM daily_price d
                        WHERE d.isin = s.isin AND d.trade_date <= p_as_of
                        ORDER BY d.trade_date DESC LIMIT p_lookback) x
    ORDER BY s.isin, x.trade_date;
END
$$;

------------------------------------------------------------------------------------------------
-- 4. Compliance semantic search: "which notes in shared memory paraphrase this text?" -- EXACT, over distinct vectors
------------------------------------------------------------------------------------------------
-- HNSW was built, measured and REJECTED (scripts/index_studies.py -> docs/benchmarks/index-studies.json; DEVIATIONS D-26).
-- Distance-based recall@10 against brute force on the calibration corpus (199,200 notes, 23,780 distinct vectors):
--   HNSW over all rows        realistic queries 0.84-0.91, stored-vector queries 0.75-0.77 for ef_search 40..1000: a hard
--                             ceiling (clusters of identical vectors become unreachable once their neighbour lists fill)
--   HNSW over distinct vectors realistic 0.82-0.91, stored 0.70-0.89: no ceiling, but still short of 1
-- The embedding is a hashed bag of words (walltest/embedding.py): one query sees ~530 distinct distance values across
-- 23,780 vectors, and greedy graph search does not navigate such plateaus. A compliance search that silently drops 10-30% of
-- the closest paraphrases is not acceptable, so the search is exact: distance to every DISTINCT vector (an index-only scan of
-- agent_note_vector_reps_idx), cut at the p_limit-th distance INCLUDING ties, then expanded to every note carrying those
-- vectors. The result equals the brute-force search row for row (same distances, same (distance, note_id) order) at
-- ~15 ms instead of ~250 ms (docs/benchmarks/v2.json).
CREATE FUNCTION semantic_search(p_query vector(384), p_limit integer DEFAULT 10)
RETURNS TABLE (note_id bigint, author text, asset text, isin char(12), body text, created_at timestamptz, similarity double precision)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
SET jit = off          -- measured: the plan's (overestimated) cost triggers JIT, 105 ms of compilation for 11 ms of work
AS $$
  WITH d AS MATERIALIZED (
    SELECT n.note_id AS rep, n.embedding <=> p_query AS dist FROM agent_note n WHERE n.embedding_canonical),
  cut AS (SELECT d.dist FROM d ORDER BY d.dist LIMIT 1 OFFSET LEAST(GREATEST(p_limit, 1), 100) - 1),
  reps AS (SELECT DISTINCT ON (embedding_key(r.embedding)) embedding_key(r.embedding) AS key, d.dist
           FROM d JOIN agent_note r ON r.note_id = d.rep
           WHERE d.dist <= COALESCE((SELECT cut.dist FROM cut), 'Infinity'::double precision)
           ORDER BY embedding_key(r.embedding), d.dist)
  SELECT m.note_id, a.agent_name::text, da.asset_name::text, m.isin, m.body, m.created_at, 1 - r.dist
  FROM reps r
  -- all notes of one key share its distance, so only its p_limit lowest note_ids can survive the final (dist, note_id) order
  -- (measured: a frequent vector is carried by ~3,300 notes; fetching them all cost 0.3 s for 10 results)
  CROSS JOIN LATERAL (SELECT x.note_id, x.author_agent_id, x.asset_id, x.isin, x.body, x.created_at FROM agent_note x
                      WHERE x.embedding IS NOT NULL AND embedding_key(x.embedding) = r.key
                      ORDER BY x.note_id LIMIT LEAST(GREATEST(p_limit, 1), 100)) m
  JOIN agent a ON a.agent_id = m.author_agent_id JOIN data_asset da ON da.asset_id = m.asset_id
  ORDER BY r.dist, m.note_id LIMIT LEAST(GREATEST(p_limit, 1), 100)
$$;

-- Brute force over every note: the reference semantic_search is tested and benchmarked against.
CREATE FUNCTION semantic_search_exact(p_query vector(384), p_limit integer DEFAULT 10)
RETURNS TABLE (note_id bigint, similarity double precision)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
SET jit = off
AS $$
  WITH all_ AS MATERIALIZED (SELECT n.note_id, n.embedding FROM agent_note n WHERE n.embedding IS NOT NULL)
  SELECT all_.note_id, 1 - (all_.embedding <=> p_query) FROM all_
  ORDER BY all_.embedding <=> p_query, all_.note_id LIMIT LEAST(GREATEST(p_limit, 1), 100)
$$;

REVOKE EXECUTE ON FUNCTION semantic_search(vector, integer), semantic_search_exact(vector, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION embedding_key(vector) TO compliance, audit_engine;
GRANT EXECUTE ON FUNCTION semantic_search(vector, integer), semantic_search_exact(vector, integer) TO compliance, audit_engine;
