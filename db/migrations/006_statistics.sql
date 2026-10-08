-- 006: Statistics, all in SQL  (doc 6.3 op 8, 8: binom_upper_p, leakage bound; plus D-05 rigour)
--
-- Everything is computed in LOG SPACE. A naive 0.5^n * C(n,k) underflows for large n (PostgreSQL then
-- raises "value out of range: underflow"), and exp() of a very negative double can also raise, so every
-- exp() below is guarded (|x| <= 700).

-- ln P(X >= k) for X ~ Binomial(n, p), summed downward from j = n (terms grow towards the mode).
CREATE FUNCTION binom_log_sf(n integer, k integer, p double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
  lt double precision; acc double precision := '-Infinity'; d double precision; lr double precision; j integer;
BEGIN
  IF n < 0 THEN RAISE EXCEPTION 'n must be >= 0'; END IF;
  IF p < 0 OR p > 1 THEN RAISE EXCEPTION 'p must be in [0,1]'; END IF;
  IF k <= 0 THEN RETURN 0; END IF;
  IF k > n THEN RETURN '-Infinity'; END IF;
  IF p = 0 THEN RETURN '-Infinity'; END IF;
  IF p = 1 THEN RETURN 0; END IF;
  lt := n * ln(p);                                  -- ln P(X = n)
  lr := ln(1 - p) - ln(p);                          -- ln(q/p)
  j := n;
  LOOP
    IF acc = '-Infinity' THEN
      acc := lt;
    ELSE
      d := lt - acc;
      IF d > 40 THEN acc := lt;
      ELSIF d > 0 THEN acc := lt + ln(1 + exp(-d));
      ELSIF d > -40 THEN acc := acc + ln(1 + exp(d));
      END IF;
    END IF;
    EXIT WHEN j <= k;
    lt := lt + ln(j::double precision / (n - j + 1)) + lr;      -- ln P(X = j-1) from ln P(X = j)
    j := j - 1;
  END LOOP;
  RETURN LEAST(acc, 0);
END
$$;

-- Exact one-sided binomial p-value  P(X >= k | n, p)   (proposal name: binom_upper_p)
CREATE FUNCTION binom_upper_p(n integer, k integer, p double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE l double precision;
BEGIN
  l := binom_log_sf(n, k, p);
  IF l < -700 THEN RETURN 0; END IF;               -- below ~1e-304: report 0, use binom_log10_p for the magnitude
  RETURN LEAST(exp(l), 1);
END
$$;

CREATE FUNCTION binom_log10_p(n integer, k integer, p double precision DEFAULT 0.5)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT binom_log_sf(n, k, p) / ln(10.0::double precision) $$;

-- Leakage in bits: 1 - H(a) for a > 0.5, else 0   (capacity of a binary symmetric channel with accuracy a)
CREATE FUNCTION leakage_bits(a double precision)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT CASE WHEN a <= 0.5 THEN 0::double precision
              WHEN a >= 1   THEN 1::double precision
              ELSE 1 + (a * ln(a) + (1 - a) * ln(1 - a)) / ln(2.0::double precision) END
$$;

-- One-sided Clopper-Pearson (1 - alpha) LOWER confidence bound on the success probability:
-- the p at which P(X >= k | n, p) = alpha. Exact duality with the test: p_value(n,k) <= alpha  <=>  bound > 0.5.
CREATE FUNCTION clopper_pearson_lower(n integer, k integer, alpha double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := 0; hi double precision := 1; mid double precision; la double precision; i integer;
BEGIN
  IF k < 0 OR k > n OR n < 1 THEN RAISE EXCEPTION 'need 0 <= k <= n, n >= 1'; END IF;
  IF alpha <= 0 OR alpha >= 1 THEN RAISE EXCEPTION 'alpha must be in (0,1)'; END IF;
  IF k = 0 THEN RETURN 0; END IF;
  la := ln(alpha);
  FOR i IN 1..60 LOOP
    mid := (lo + hi) / 2;
    IF binom_log_sf(n, k, mid) < la THEN lo := mid; ELSE hi := mid; END IF;
  END LOOP;
  RETURN (lo + hi) / 2;
END
$$;

-- Smallest k with P(X >= k | n, 0.5) <= alpha (the exact rejection threshold); n+1 if no k qualifies.
CREATE FUNCTION binom_critical_k(n integer, alpha double precision)
RETURNS integer LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lt double precision; acc double precision := '-Infinity'; d double precision; la double precision; j integer;
BEGIN
  la := ln(alpha);
  lt := n * ln(0.5::double precision);
  j := n;
  LOOP
    IF acc = '-Infinity' THEN acc := lt;
    ELSE
      d := lt - acc;
      IF d > 40 THEN acc := lt;
      ELSIF d > 0 THEN acc := lt + ln(1 + exp(-d));
      ELSIF d > -40 THEN acc := acc + ln(1 + exp(d));
      END IF;
    END IF;
    IF acc > la THEN RETURN j + 1; END IF;            -- tail at j already exceeds alpha: threshold is j+1
    EXIT WHEN j <= 0;
    lt := lt + ln(j::double precision / (n - j + 1));
    j := j - 1;
  END LOOP;
  RETURN 0;
END
$$;

-- Exact power of the one-sided exact binomial test at true accuracy a.
CREATE FUNCTION power_exact(n integer, a double precision, alpha double precision)
RETURNS double precision LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT CASE WHEN binom_critical_k(n, alpha) > n THEN 0::double precision
              ELSE binom_upper_p(n, binom_critical_k(n, alpha), a) END
$$;

-- Minimum detectable accuracy: smallest a with power_exact(n, a, alpha) >= target. NULL if the test cannot reject at n.
CREATE FUNCTION min_detectable_acc(n integer, alpha double precision, target double precision DEFAULT 0.8)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE kc integer; lo double precision := 0.5; hi double precision := 1; mid double precision; i integer; lp double precision;
BEGIN
  kc := binom_critical_k(n, alpha);
  IF kc > n THEN RETURN NULL; END IF;
  lp := ln(target);
  FOR i IN 1..50 LOOP
    mid := (lo + hi) / 2;
    IF binom_log_sf(n, kc, mid) >= lp THEN hi := mid; ELSE lo := mid; END IF;
  END LOOP;
  RETURN hi;
END
$$;

-- Standard normal quantile by bisection on erfc (used only for the normal-approximation sample size).
CREATE FUNCTION norm_ppf(q double precision)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE lo double precision := -10; hi double precision := 10; mid double precision; i integer;
BEGIN
  IF q <= 0 OR q >= 1 THEN RAISE EXCEPTION 'q must be in (0,1)'; END IF;
  FOR i IN 1..100 LOOP
    mid := (lo + hi) / 2;
    IF 0.5 * erfc(-mid / sqrt(2.0::double precision)) < q THEN lo := mid; ELSE hi := mid; END IF;
  END LOOP;
  RETURN (lo + hi) / 2;
END
$$;

-- Normal-approximation sample size (the formula behind the proposal's table: 70% -> 94, 60% -> 384, 55% -> 1,543).
CREATE FUNCTION n_required_normal(a double precision, alpha double precision, target double precision DEFAULT 0.8)
RETURNS integer LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT ceil(power(norm_ppf(1 - alpha) * 0.5 + norm_ppf(target) * sqrt(a * (1 - a)), 2) / power(a - 0.5, 2))::integer
$$;

-- Smallest n whose EXACT power reaches the target. Exact power is a sawtooth in n (the test is discrete), so this is
-- the FIRST crossing; the scan starts well below the normal-approximation n (power there is far below target).
CREATE FUNCTION n_required_exact(a double precision, alpha double precision, target double precision DEFAULT 0.8)
RETURNS integer LANGUAGE plpgsql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE n0 integer; n integer;
BEGIN
  n0 := n_required_normal(a, alpha, target);
  FOR n IN GREATEST(1, (n0 * 0.7)::integer)..(n0 * 2 + 50) LOOP
    IF power_exact(n, a, alpha) >= target THEN RETURN n; END IF;
  END LOOP;
  RETURN NULL;
END
$$;

-- Holm-Bonferroni step-down adjusted p-values, returned in input order.
CREATE FUNCTION holm_adjust(p double precision[])
RETURNS double precision[] LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
  WITH x AS (
    SELECT i, pv, row_number() OVER (ORDER BY pv, i) AS r, count(*) OVER () AS m
    FROM unnest(p) WITH ORDINALITY AS u(pv, i)
  ), y AS (
    SELECT i, LEAST(1, max((m - r + 1) * pv) OVER (ORDER BY r)) AS adj FROM x
  )
  SELECT array_agg(adj ORDER BY i) FROM y
$$;

-- The family verdict used by calibration and tests: same rule as v_verdict (Holm-adjusted p <= alpha => LEAK).
CREATE FUNCTION family_verdict(alpha double precision, n integer[], k integer[])
RETURNS TABLE (idx integer, n_slots integer, n_correct integer, p_raw double precision, p_adj double precision, verdict text)
LANGUAGE sql IMMUTABLE STRICT
SET search_path = pg_catalog, public, pg_temp
AS $$
  WITH t AS (
    SELECT i::integer AS idx, nn AS n_slots, kk AS n_correct, binom_upper_p(nn, kk) AS p_raw
    FROM unnest(n, k) WITH ORDINALITY AS u(nn, kk, i)
  ), h AS (
    SELECT array_agg(p_raw ORDER BY idx) AS ps FROM t
  )
  SELECT t.idx, t.n_slots, t.n_correct, t.p_raw, (holm_adjust(h.ps))[t.idx] AS p_adj,
         CASE WHEN (holm_adjust(h.ps))[t.idx] <= alpha THEN 'LEAK' ELSE 'NO_EVIDENCE' END
  FROM t, h ORDER BY t.idx
$$;
