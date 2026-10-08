"""Statistics v2 (migration 012) against independent references: scipy special functions, numerical integration, simulation."""
import math

import numpy as np
import pytest
from conftest import one
from scipy import integrate, special, stats
from scipy.special import logsumexp


def ref_log_cdf(n, k, p):
    if k < 0:
        return -math.inf
    if k >= n:
        return 0.0
    return float(logsumexp(stats.binom.logpmf(np.arange(0, k + 1), n, p)))


def ref_log_e(n, k, p0=0.5):
    """Mixture e-value via scipy's beta function and binomial cdf (the incomplete-beta identity)."""
    return special.betaln(k + 1, n - k + 1) + ref_log_cdf(n + 1, k, p0) - math.log(1 - p0) - k * math.log(p0) - (n - k) * math.log(1 - p0)


def quad_e(n, k, p0):
    """The DEFINITION, by numerical integration (independent of the identity)."""
    f = lambda q: math.exp(k * math.log(q / p0) + (n - k) * math.log((1 - q) / (1 - p0))) / (1 - p0)
    v, _ = integrate.quad(f, p0, 1, limit=200, epsabs=0, epsrel=1e-11)
    return v


@pytest.mark.parametrize("x", [0.1, 0.5, 1, 1.5, 2, 3.7, 10, 50.5, 100, 1544, 5001, 20000])
def test_ln_gamma_matches_scipy(conn, x):
    got = one(conn, "SELECT ln_gamma(%s)", (x,))
    assert got == pytest.approx(float(special.gammaln(x)), rel=1e-13, abs=1e-12)


def test_ln_choose_and_beta(conn):
    for n, k in ((10, 3), (152, 76), (1543, 830), (5000, 2500)):
        assert one(conn, "SELECT ln_choose(%s,%s)", (n, k)) == pytest.approx(float(special.gammaln(n + 1) - special.gammaln(k + 1) - special.gammaln(n - k + 1)), rel=1e-12)
    assert one(conn, "SELECT ln_beta(3.5, 7)") == pytest.approx(float(special.betaln(3.5, 7)), rel=1e-13)


@pytest.mark.parametrize("n", [1, 7, 30, 152, 1543])
@pytest.mark.parametrize("p", [0.5, 0.2, 0.71])
def test_binom_log_cdf(conn, n, p):
    for k in sorted({0, 1, n // 3, n // 2, n - 1, n}):
        assert one(conn, "SELECT binom_log_cdf(%s,%s,%s)", (n, k, p)) == pytest.approx(ref_log_cdf(n, k, p), rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("n", [1, 10, 30, 152, 1543])
@pytest.mark.parametrize("alpha", [0.05, 0.001, 0.05 / 3])
def test_clopper_pearson_upper(conn, n, alpha):
    for k in sorted({0, 1, n // 2, int(n * 0.6), n - 1, n}):
        got = one(conn, "SELECT clopper_pearson_upper(%s,%s,%s)", (n, k, alpha))
        ref = 1.0 if k == n else float(stats.beta.ppf(1 - alpha, k + 1, n - k))
        assert got == pytest.approx(ref, abs=1e-10), (n, k, alpha)


def test_two_sided_bounds_bracket_the_estimate(conn):
    for n, k in ((19, 18), (152, 133), (152, 78), (40, 20)):
        lo = one(conn, "SELECT clopper_pearson_lower(%s,%s,0.05)", (n, k))
        hi = one(conn, "SELECT clopper_pearson_upper(%s,%s,0.05)", (n, k))
        assert lo <= k / n <= hi


# ----- e-values ------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("n,k,p0", [(1, 1, 0.5), (10, 8, 0.5), (19, 19, 0.5), (40, 26, 0.5), (60, 40, 0.6), (25, 5, 0.3), (8, 0, 0.5)])
def test_mixture_evalue_equals_its_defining_integral(conn, n, k, p0):
    got = one(conn, "SELECT log_evalue_mix(%s,%s,%s)", (n, k, p0))
    assert got == pytest.approx(math.log(quad_e(n, k, p0)), abs=1e-8)


@pytest.mark.parametrize("n,k,p0", [(152, 133, 0.5), (1543, 830, 0.5), (500, 260, 0.52), (3000, 1490, 0.5)])
def test_mixture_evalue_large_n_vs_scipy_identity(conn, n, k, p0):
    assert one(conn, "SELECT log_evalue_mix(%s,%s,%s)", (n, k, p0)) == pytest.approx(ref_log_e(n, k, p0), rel=1e-9, abs=1e-9)


def test_closed_form_at_one_half(conn):
    for n, k in ((5, 3), (40, 30), (152, 100)):
        closed = (n + 1) * math.log(2) + ref_log_cdf(n + 1, k, 0.5) - math.log(n + 1) - float(special.gammaln(n + 1) - special.gammaln(k + 1) - special.gammaln(n - k + 1))
        assert one(conn, "SELECT log_evalue_mix(%s,%s)", (n, k)) == pytest.approx(closed, abs=1e-9)
    assert one(conn, "SELECT log_evalue_mix(0,0)") == pytest.approx(0.0, abs=1e-12)       # E_0 = 1


def test_evalue_is_decreasing_in_p0_below_the_estimate(conn):
    """The confidence-sequence bisection relies on it."""
    for n, k in ((20, 15), (152, 110), (152, 80), (1000, 560)):
        grid = np.linspace(0.02, k / n, 40)
        vals = [one(conn, "SELECT log_evalue_mix(%s,%s,%s)", (n, k, float(p))) for p in grid]
        assert all(a >= b - 1e-9 for a, b in zip(vals, vals[1:])), (n, k)


def _sup_e_paths(p, n_paths, T, rng, alpha):
    """Simulate Bernoulli(p) paths and report whether sup_t E_t >= 1/alpha (Python implementation of the SQL formula)."""
    x = rng.random((n_paths, T)) < p
    k = np.cumsum(x, axis=1)
    t = np.arange(1, T + 1)
    lb = special.betaln(k + 1, t - k + 1)
    lc = stats.binom.logcdf(k, t + 1, 0.5)
    loge = lb + lc - math.log(0.5) - k * math.log(0.5) - (t - k) * math.log(0.5)
    return (loge.max(axis=1) >= math.log(1 / alpha))


def test_python_reference_equals_sql_on_a_path(conn):
    rng = np.random.default_rng(3)
    x = rng.random(80) < 0.55
    k = np.cumsum(x)
    for t in (1, 5, 20, 80):
        kk = int(k[t - 1])
        py = special.betaln(kk + 1, t - kk + 1) + stats.binom.logcdf(kk, t + 1, 0.5) + (t + 1) * math.log(2)
        assert one(conn, "SELECT log_evalue_mix(%s,%s)", (t, kk)) == pytest.approx(py, abs=1e-8)


@pytest.mark.parametrize("p", [0.5, 0.45])
def test_anytime_validity_under_continuous_monitoring(p):
    """Ville: P(the e-process EVER crosses 1/alpha) <= alpha, even when checked after every single slot."""
    rng = np.random.default_rng(2026)
    for alpha in (0.05, 0.01):
        hits = _sup_e_paths(p, 20000, 600, rng, alpha)
        rate = hits.mean()
        assert rate <= alpha + 3 * math.sqrt(alpha * (1 - alpha) / 20000), (p, alpha, rate)


def test_naive_peeking_at_fixed_n_p_values_inflates_false_alarms():
    """The reason v1 forbade peeking: checking the exact binomial p after every slot crosses alpha far more than alpha."""
    rng = np.random.default_rng(7)
    x = rng.random((20000, 152)) < 0.5
    k = np.cumsum(x, axis=1)
    t = np.arange(1, 153)
    p = stats.binom.sf(k - 1, t, 0.5)
    rate = (p.min(axis=1) <= 0.05).mean()
    assert rate > 0.15                                           # roughly 3-5x the nominal 5%


def test_mixture_has_power_under_a_real_leak():
    rng = np.random.default_rng(11)
    assert _sup_e_paths(0.7, 2000, 152, rng, 0.05 / 3).mean() > 0.95


def test_confidence_sequence_sql(conn):
    for n, k, a in ((152, 133, 0.05), (152, 76, 0.05), (19, 19, 0.0167), (40, 0, 0.05), (40, 40, 0.05)):
        lo = one(conn, "SELECT cs_lower(%s,%s,%s)", (n, k, a))
        hi = one(conn, "SELECT cs_upper(%s,%s,%s)", (n, k, a))
        assert 0 <= lo <= k / n <= hi <= 1
        if 0 < k:
            assert one(conn, "SELECT log_evalue_mix(%s,%s,%s)", (n, k, max(lo, 1e-9))) == pytest.approx(-math.log(a), abs=1e-6) or lo == 0
        # the CS is wider than the fixed-n Clopper-Pearson interval at the same level (the price of anytime validity)
        if 0 < k < n:
            assert lo <= one(conn, "SELECT clopper_pearson_lower(%s,%s,%s)", (n, k, a)) + 1e-12
            assert hi >= one(conn, "SELECT clopper_pearson_upper(%s,%s,%s)", (n, k, a)) - 1e-12


def test_confidence_sequence_time_uniform_coverage(conn):
    """P(true accuracy leaves the CS at ANY time) <= alpha, by simulation with the Python mirror of the SQL function."""
    rng = np.random.default_rng(5)
    p, alpha, T, paths = 0.62, 0.05, 300, 4000
    x = rng.random((paths, T)) < p
    k = np.cumsum(x, axis=1)
    t = np.arange(1, T + 1)
    # p is excluded at time t iff the e-value for H0: acc <= p (or >= p) reaches 1/alpha; check both sides at alpha/2 each
    le_low = special.betaln(k + 1, t - k + 1) + stats.binom.logcdf(k, t + 1, p) - math.log(1 - p) - k * math.log(p) - (t - k) * math.log(1 - p)
    le_up = special.betaln(k + 1, t - k + 1) + stats.binom.logsf(k, t + 1, p) - math.log(p) - k * math.log(p) - (t - k) * math.log(1 - p)
    miss = ((le_low >= math.log(2 / alpha)) | (le_up >= math.log(2 / alpha))).any(axis=1).mean()
    assert miss <= alpha + 3 * math.sqrt(alpha * (1 - alpha) / paths), miss


def test_p_from_e(conn):
    assert one(conn, "SELECT p_from_log_e(%s)", (math.log(40),)) == pytest.approx(1 / 40)
    assert one(conn, "SELECT p_from_log_e(-3)") == 1.0 and one(conn, "SELECT p_from_log_e(800)") == 0.0


def test_v2_kernels_kept_v1_answers(conn):
    """binom_log_sf and binom_critical_k were replaced by faster algorithms: spot-check against scipy (full grid in test_stats.py)."""
    for n in (5, 94, 384, 1543, 4000):
        for alpha in (0.05, 0.001):
            ks = np.arange(0, n + 2)
            ok = np.where(stats.binom.sf(ks - 1, n, 0.5) <= alpha)[0]
            assert one(conn, "SELECT binom_critical_k(%s,%s)", (n, alpha)) == (int(ok[0]) if len(ok) else n + 1)


# ----- migration 016: incomplete-beta kernels (Lentz continued fraction + safeguarded Newton) ---------------------------------------
# Ground truth is mpmath at 60 digits: at extreme tails scipy's betainc is itself off by ~1e-7 in log (found while validating).
import mpmath  # noqa: E402

mpmath.mp.dps = 60


@pytest.mark.parametrize("x,a,b", [(0.40925271456881607, 800, 30), (0.2, 1600, 3500), (0.7, 3500, 800), (0.05, 30, 1600), (0.5, 1543, 714),
                                   (0.999, 2, 3500), (1e-6, 1, 5), (0.3, 0.5, 2), (0.9, 5, 0.5), (0.51, 1000, 1000), (0.01, 400, 2)])
def test_ln_ibeta_against_arbitrary_precision(conn, x, a, b):
    truth = float(mpmath.log(mpmath.betainc(a, b, 0, x, regularized=True)))
    assert one(conn, "SELECT ln_ibeta(%s,%s,%s)", (x, a, b)) == pytest.approx(truth, rel=1e-12, abs=1e-11)


def test_ln_ibeta_survives_tails_that_underflow_double_precision(conn):
    """PostgreSQL RAISES on float underflow (exp, *). The kernels must return finite logs / zeros instead."""
    assert one(conn, "SELECT ln_ibeta(0.01, 3500, 3500)") < -10000                      # I ~ e^-12000: no exception
    assert one(conn, "SELECT ln_ibeta(0.99, 3500, 3500)") == 0                          # 1 - e^-12000
    assert one(conn, "SELECT exp_safe(-1e6)") == 0 and one(conn, "SELECT clopper_pearson_upper(3500, 3500, 1e-12)") == 1
    assert one(conn, "SELECT clopper_pearson_lower(3500, 3500, 1e-12)") > 0.99
    assert one(conn, "SELECT cs_lower(3000, 3000, 1e-9)") > 0.98


def test_fast_kernels_agree_with_the_bisection_references_and_scipy(conn):
    rng = np.random.default_rng(16)
    for _ in range(120):
        n = int(rng.integers(1, 3600)); k = int(rng.integers(0, n + 1)); al = float(rng.choice([0.05, 0.001, 0.05 / 24, 1e-6]))
        lo, hi = one(conn, "SELECT clopper_pearson_lower(%s,%s,%s)", (n, k, al)), one(conn, "SELECT clopper_pearson_upper(%s,%s,%s)", (n, k, al))
        assert lo == pytest.approx(one(conn, "SELECT clopper_pearson_lower_bisect(%s,%s,%s)", (n, k, al)), abs=1e-12)
        assert hi == pytest.approx(one(conn, "SELECT clopper_pearson_upper_bisect(%s,%s,%s)", (n, k, al)), abs=1e-12)
        assert lo == pytest.approx(0.0 if k == 0 else stats.beta.ppf(al, k, n - k + 1), abs=1e-11)
        assert hi == pytest.approx(1.0 if k == n else stats.beta.ppf(1 - al, k + 1, n - k), abs=1e-11)
        assert one(conn, "SELECT log_evalue_mix(%s,%s)", (n, k)) == pytest.approx(one(conn, "SELECT log_evalue_mix_sum(%s,%s)", (n, k)), rel=1e-10, abs=1e-10)
        if n < 1200:
            cl, cu = one(conn, "SELECT cs_lower(%s,%s,%s)", (n, k, al)), one(conn, "SELECT cs_upper(%s,%s,%s)", (n, k, al))
            assert cl == pytest.approx(one(conn, "SELECT cs_lower_bisect(%s,%s,%s)", (n, k, al)), abs=1e-12)
            assert cu == pytest.approx(one(conn, "SELECT cs_upper_bisect(%s,%s,%s)", (n, k, al)), abs=1e-12)
            assert cl <= lo + 1e-15 and cu >= hi - 1e-15                                # anytime bounds are never narrower


def test_cs_ends_are_on_the_conservative_side_of_the_e_value_threshold(conn):
    for n, k, al in [(100, 70, 0.05), (500, 260, 0.001), (40, 40, 0.05), (40, 0, 0.05), (900, 450, 0.0021)]:
        thr = -math.log(al)
        lo, hi = one(conn, "SELECT cs_lower(%s,%s,%s)", (n, k, al)), one(conn, "SELECT cs_upper(%s,%s,%s)", (n, k, al))
        if lo > 0:
            assert one(conn, "SELECT log_evalue_mix(%s,%s,%s)", (n, k, lo)) >= thr - 1e-9      # lo itself is (just) rejected
        if hi < 1:
            assert one(conn, "SELECT log_evalue_mix_upper(%s,%s,%s)", (n, k, hi)) >= thr - 1e-9
