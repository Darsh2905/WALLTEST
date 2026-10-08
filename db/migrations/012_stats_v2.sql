-- 012: Statistics v2 (docs/METHODS.md). New kernels, two-sided bounds, anytime-valid e-values and confidence sequences.
-- Every function is pure SQL/PL/pgSQL, IMMUTABLE, log-space, and validated against independent references in
-- backend/tests/test_stats_v2.py (scipy, numerical integration, simulation).

------------------------------------------------------------------------------------------------
-- Kernels
------------------------------------------------------------------------------------------------
-- ln Γ(x) by the Lanczos approximation (g = 7, 9 coefficients; relative error ~1e-15). PostgreSQL 16 has no lgamma().
CREATE FUNCTION ln_gamma(x double precision) RETURNS double precision
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
  c double precision[] := ARRAY[0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
                                -176.61502916214059, 12.507343278686905, -0.13857109526572012,
                                9.9843695780195716e-6, 1.5056327351493116e-7];
  a double precision; t double precision; y double precision;
BEGIN
  IF x <= 0 AND x = floor(x) THEN RAISE EXCEPTION 'ln_gamma has a pole at %', x; END IF;
  IF x < 0.5 THEN RETURN ln(pi() / abs(sin(pi() * x))) - ln_gamma(1 - x); END IF;   -- reflection
  y := x - 1; a := c[1]; t := y + 7.5;
  FOR i IN 1..8 LOOP a := a + c[i + 1] / (y + i); END LOOP;
  RETURN 0.5 * ln(2 * pi()) + (y + 0.5) * ln(t) - t + ln(a);
END
$$;

CREATE FUNCTION ln_choose(n integer, k integer) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT ln_gamma(n + 1.0) - ln_gamma(k + 1.0) - ln_gamma(n - k + 1.0) $$;

CREATE FUNCTION ln_beta(a double precision, b double precision) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT ln_gamma(a) + ln_gamma(b) - ln_gamma(a + b) $$;

-- v2 binomial upper tail: start at the exact log-pmf of k (via ln_choose) and sum UPWARD, stopping once past the mode
-- and the terms are below e^-50 of the running sum (they then decay geometrically). Typically O(sqrt n) instead of O(n).
-- Same name and signature as v1, so every dependent function becomes faster; accuracy re-validated against scipy.
CREATE OR REPLACE FUNCTION binom_log_sf(n integer, k integer, p double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lt double precision; acc double precision; lr double precision; d double precision; j integer; mode double precision;
BEGIN
  IF n < 0 THEN RAISE EXCEPTION 'n must be >= 0'; END IF;
  IF p < 0 OR p > 1 THEN RAISE EXCEPTION 'p must be in [0,1]'; END IF;
  IF k <= 0 THEN RETURN 0; END IF;
  IF k > n THEN RETURN '-Infinity'; END IF;
  IF p = 0 THEN RETURN '-Infinity'; END IF;
  IF p = 1 THEN RETURN 0; END IF;
  lr := ln(p) - ln(1 - p);
  lt := ln_choose(n, k) + k * ln(p) + (n - k) * ln(1 - p);         -- ln P(X = k)
  acc := lt; j := k; mode := (n + 1) * p;
  WHILE j < n LOOP
    lt := lt + ln((n - j)::double precision / (j + 1)) + lr;        -- ln P(X = j + 1)
    j := j + 1;
    d := lt - acc;
    IF d < -50 AND j > mode THEN EXIT; END IF;
    IF d > 40 THEN acc := lt;
    ELSIF d > 0 THEN acc := lt + ln(1 + exp(-d));
    ELSIF d > -40 THEN acc := acc + ln(1 + exp(d));
    END IF;
  END LOOP;
  RETURN LEAST(acc, 0);
END
$$;

-- ln P(X <= k), via the reflection X -> n - X (keeps full relative precision in the lower tail)
CREATE FUNCTION binom_log_cdf(n integer, k integer, p double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT CASE WHEN k < 0 THEN '-Infinity'::double precision WHEN k >= n THEN 0::double precision
                  ELSE binom_log_sf(n, n - k, 1 - p) END $$;

-- v2 exact critical value: start from the normal-approximation guess and walk to the exact boundary (a few O(sqrt n) tail
-- evaluations instead of an O(n) scan). Same contract as v1: smallest k with P(X >= k | n, 1/2) <= alpha; n + 1 if none.
CREATE OR REPLACE FUNCTION binom_critical_k(n integer, alpha double precision)
RETURNS integer LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE k integer; la double precision := ln(alpha);
BEGIN
  IF n <= 0 THEN RETURN n + 1; END IF;
  k := LEAST(n + 1, GREATEST(0, ceil(n / 2.0 + norm_ppf(1 - alpha) * sqrt(n) / 2.0)::integer));
  WHILE k > 0 AND binom_log_sf(n, k - 1) <= la LOOP k := k - 1; END LOOP;   -- move down while k-1 still rejects
  WHILE k <= n AND binom_log_sf(n, k) > la LOOP k := k + 1; END LOOP;       -- move up until k rejects
  RETURN k;
END
$$;

------------------------------------------------------------------------------------------------
-- Two-sided exact bounds (fixed n)
------------------------------------------------------------------------------------------------
-- One-sided Clopper-Pearson (1 - alpha) UPPER bound: the p at which P(X <= k | n, p) = alpha.
CREATE FUNCTION clopper_pearson_upper(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := 0; hi double precision := 1; mid double precision; la double precision := ln(alpha);
BEGIN
  IF k < 0 OR k > n OR n < 1 THEN RAISE EXCEPTION 'need 0 <= k <= n, n >= 1'; END IF;
  IF k = n THEN RETURN 1; END IF;
  FOR i IN 1..60 LOOP
    mid := (lo + hi) / 2;
    IF binom_log_cdf(n, k, mid) > la THEN lo := mid; ELSE hi := mid; END IF;
  END LOOP;
  RETURN (lo + hi) / 2;
END
$$;

------------------------------------------------------------------------------------------------
-- Anytime-valid inference: beta-binomial mixture e-values and confidence sequences
------------------------------------------------------------------------------------------------
-- E-value for H0: accuracy <= p0, mixing the likelihood ratio uniformly over alternatives q in [p0, 1]:
--   E(n, k) = (1/(1-p0)) * Integral_{p0}^{1} (q/p0)^k ((1-q)/(1-p0))^(n-k) dq
--           = B(k+1, n-k+1) * P(Bin(n+1, p0) <= k) / ((1-p0) p0^k (1-p0)^(n-k))
-- Each likelihood ratio is a nonnegative supermartingale under every p <= p0 (its one-step mean 1 + (p-p0)(q-p0)/(p0(1-p0)) <= 1),
-- so the mixture is too; Ville's inequality gives P(sup_t E_t >= 1/alpha) <= alpha: valid under continuous monitoring and
-- optional stopping. It depends only on (n, k). At p0 = 1/2: E = 2^(n+1) P(Bin(n+1, 1/2) <= k) / ((n+1) C(n, k)).
CREATE FUNCTION log_evalue_mix(n integer, k integer, p0 double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT ln_beta(k + 1.0, n - k + 1.0) + binom_log_cdf(n + 1, k, p0) - ln(1 - p0) - k * ln(p0) - (n - k) * ln(1 - p0) $$;

-- Mirror image for H0: accuracy >= p0 (mixture over q in [0, p0]); used for anytime-valid UPPER bounds.
CREATE FUNCTION log_evalue_mix_upper(n integer, k integer, p0 double precision)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT ln_beta(k + 1.0, n - k + 1.0) + binom_log_sf(n + 1, k + 1, p0) - ln(p0) - k * ln(p0) - (n - k) * ln(1 - p0) $$;

CREATE FUNCTION evalue_mix(n integer, k integer, p0 double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT exp(LEAST(log_evalue_mix(n, k, p0), 700)) $$;

-- An e-value converts to an anytime-valid p-value: P(1/E_tau <= alpha) <= alpha at ANY stopping time tau.
CREATE FUNCTION p_from_log_e(log_e double precision)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT CASE WHEN log_e <= 0 THEN 1::double precision WHEN log_e > 700 THEN 0::double precision ELSE exp(-log_e) END $$;

-- (1 - alpha) anytime-valid confidence sequence for the accuracy: lower and upper ends.
-- CS_t = { p0 : E_t(p0) < 1/alpha }. The e-value is decreasing in p0 below the estimate (tested), so bisection finds the end.
CREATE FUNCTION cs_lower(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := 1e-12; hi double precision; mid double precision; thr double precision := -ln(alpha);
BEGIN
  IF n = 0 OR k = 0 THEN RETURN 0; END IF;
  hi := k::double precision / n;
  IF hi >= 1 THEN hi := 1 - 1e-12; END IF;
  IF log_evalue_mix(n, k, lo) < thr THEN RETURN 0; END IF;
  FOR i IN 1..60 LOOP
    mid := (lo + hi) / 2;
    IF log_evalue_mix(n, k, mid) >= thr THEN lo := mid; ELSE hi := mid; END IF;
  END LOOP;
  RETURN lo;
END
$$;

CREATE FUNCTION cs_upper(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision; hi double precision := 1 - 1e-12; mid double precision; thr double precision := -ln(alpha);
BEGIN
  IF n = 0 OR k = n THEN RETURN 1; END IF;
  lo := k::double precision / n;
  IF lo <= 0 THEN lo := 1e-12; END IF;
  IF log_evalue_mix_upper(n, k, hi) < thr THEN RETURN 1; END IF;
  FOR i IN 1..60 LOOP
    mid := (lo + hi) / 2;
    IF log_evalue_mix_upper(n, k, mid) >= thr THEN hi := mid; ELSE lo := mid; END IF;
  END LOOP;
  RETURN hi;
END
$$;

GRANT EXECUTE ON FUNCTION ln_gamma(double precision), ln_choose(integer, integer), ln_beta(double precision, double precision),
                          binom_log_cdf(integer, integer, double precision), clopper_pearson_upper(integer, integer, double precision),
                          log_evalue_mix(integer, integer, double precision), log_evalue_mix_upper(integer, integer, double precision),
                          evalue_mix(integer, integer, double precision), p_from_log_e(double precision),
                          cs_lower(integer, integer, double precision), cs_upper(integer, integer, double precision)
      TO audit_engine, compliance;
