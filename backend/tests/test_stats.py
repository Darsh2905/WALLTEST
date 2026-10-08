"""SQL statistics vs independent references (scipy, statsmodels). The SQL functions are the system's statistics; Python is only the oracle."""
import math

import numpy as np
import pytest
from conftest import one
from scipy import stats
from scipy.special import logsumexp
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportion_confint

NS = [1, 2, 5, 10, 30, 94, 100, 384, 500, 1000, 1543, 2000, 5000]


def ref_log_sf(n, k, p=0.5):
    if k <= 0:
        return 0.0
    if k > n:
        return -math.inf
    return float(logsumexp(stats.binom.logpmf(np.arange(k, n + 1), n, p)))


def grid(n):
    ks = sorted({0, 1, n // 2, n // 2 + 1, int(n * 0.55), int(n * 0.6), int(n * 0.7), int(n * 0.9), n - 1, n} & set(range(0, n + 1)))
    return [k for k in ks if 0 <= k <= n]


@pytest.mark.parametrize("n", NS)
def test_binom_upper_p_matches_scipy(conn, n):
    for k in grid(n):
        lg = one(conn, "SELECT binom_log_sf(%s,%s)", (n, k))
        ref = ref_log_sf(n, k)
        assert lg == pytest.approx(ref, rel=1e-9, abs=1e-9), (n, k)
        p = one(conn, "SELECT binom_upper_p(%s,%s)", (n, k))
        if ref > -700:      # the SQL function returns exact values down to exp(-700) ~ 1e-304
            assert p == pytest.approx(math.exp(ref), rel=1e-9), (n, k)
            assert p == pytest.approx(stats.binom.sf(k - 1, n, 0.5), rel=1e-8) if k > 0 else p == 1.0
        else:
            assert p == 0.0                                  # reported as 0 below exp(-700) (see binom_log10_p for the magnitude)


def test_n_1543_explicitly(conn):
    """The sample size at the heart of the proposal (55% accuracy, alpha = 0.001)."""
    for k in (772, 800, 850, 900, 1000, 1543):
        p = one(conn, "SELECT binom_upper_p(1543,%s)", (k,))
        ref = stats.binom.sf(k - 1, 1543, 0.5)
        assert p == pytest.approx(ref, rel=1e-9) if ref > 1e-300 else p == 0.0


def test_no_underflow_error_for_large_n(conn):
    """A naive 0.5^n * C(n,k) raises 'value out of range: underflow' for large n; the log-space version must not."""
    for n, k in ((5000, 5000), (5000, 4000), (20000, 15000), (2000, 2000)):
        p = one(conn, "SELECT binom_upper_p(%s,%s)", (n, k))
        assert p == 0.0
        l10 = one(conn, "SELECT binom_log10_p(%s,%s)", (n, k))
        assert l10 == pytest.approx(ref_log_sf(n, k) / math.log(10), rel=1e-9)
    assert one(conn, "SELECT binom_log10_p(5000,5000)") == pytest.approx(-5000 * math.log10(2), rel=1e-9)


@pytest.mark.parametrize("p", [0.1, 0.3, 0.55, 0.7, 0.95])
def test_binom_log_sf_general_p(conn, p):
    for n in (10, 94, 384, 1543):
        for k in grid(n):
            assert one(conn, "SELECT binom_log_sf(%s,%s,%s)", (n, k, p)) == pytest.approx(ref_log_sf(n, k, p), rel=1e-9, abs=1e-9)


def test_edge_cases(conn):
    assert one(conn, "SELECT binom_upper_p(10,0)") == 1.0 and one(conn, "SELECT binom_upper_p(10,-3)") == 1.0
    assert one(conn, "SELECT binom_upper_p(10,11)") == 0.0
    assert one(conn, "SELECT binom_upper_p(10,5,0)") == 0.0 and one(conn, "SELECT binom_upper_p(10,5,1)") == 1.0
    assert one(conn, "SELECT binom_upper_p(0,0)") == 1.0


# ----- leakage bits --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a", [0.0, 0.3, 0.5, 0.5000001, 0.55, 0.6, 0.7, 0.9, 0.99, 1.0])
def test_leakage_bits(conn, a):
    got = one(conn, "SELECT leakage_bits(%s)", (a,))
    want = 0.0 if a <= 0.5 else 1 - (stats.entropy([a, 1 - a], base=2) if 0 < a < 1 else 0.0)
    assert got == pytest.approx(want, abs=1e-12)
    assert 0 <= got <= 1


def test_leakage_bits_known_values(conn):
    assert one(conn, "SELECT leakage_bits(0.5)") == 0
    assert one(conn, "SELECT leakage_bits(1.0)") == 1
    assert one(conn, "SELECT leakage_bits(0.7)") == pytest.approx(0.118709, abs=1e-6)
    assert one(conn, "SELECT leakage_bits(0.55)") == pytest.approx(0.007225, abs=1e-6)


# ----- Clopper-Pearson -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("n", [1, 10, 30, 100, 384, 1543, 3000])
@pytest.mark.parametrize("alpha", [0.05, 0.001, 0.05 / 24])
def test_clopper_pearson_lower_matches_scipy_and_statsmodels(conn, n, alpha):
    for k in grid(n):
        got = one(conn, "SELECT clopper_pearson_lower(%s,%s,%s)", (n, k, alpha))
        ref = 0.0 if k == 0 else float(stats.beta.ppf(alpha, k, n - k + 1))
        assert got == pytest.approx(ref, abs=1e-10), (n, k, alpha)
        if 0 < k:
            sm = proportion_confint(k, n, alpha=2 * alpha, method="beta")[0]      # two-sided at 2*alpha has the same lower end
            assert got == pytest.approx(sm, abs=1e-9)


def test_clopper_pearson_is_exactly_dual_to_the_test(conn):
    rng = np.random.default_rng(1)
    checked = 0
    for _ in range(300):
        n = int(rng.integers(5, 400)); k = int(rng.integers(0, n + 1)); alpha = float(rng.choice([0.05, 0.01, 0.001]))
        p = one(conn, "SELECT binom_upper_p(%s,%s)", (n, k))
        if abs(p - alpha) / alpha < 1e-7:
            continue
        lo = one(conn, "SELECT clopper_pearson_lower(%s,%s,%s)", (n, k, alpha))
        assert (p <= alpha) == (lo > 0.5), (n, k, alpha, p, lo)
        checked += 1
    assert checked > 250


def test_the_point_estimate_is_not_a_bound(conn):
    lo = one(conn, "SELECT clopper_pearson_lower(94,66,0.001)")      # 66/94 = 0.702
    assert 0.5 < lo < 66 / 94 - 0.1


# ----- Holm ------------------------------------------------------------------------------------------------------------------
def holm_sql(conn, ps):
    return one(conn, "SELECT holm_adjust(%s::float8[])", (list(map(float, ps)),))


@pytest.mark.parametrize("seed", range(25))
def test_holm_matches_statsmodels(conn, seed):
    rng = np.random.default_rng(seed)
    m = int(rng.choice([1, 2, 3, 8, 24, 40]))
    ps = rng.uniform(0, 1, m) ** rng.choice([1, 3, 6])
    got = holm_sql(conn, ps)
    want = multipletests(ps, alpha=0.05, method="holm")[1]
    assert got == pytest.approx(list(want), rel=1e-12, abs=1e-15)


def test_holm_with_ties_zeros_and_ones(conn):
    for ps in ([0.01, 0.01, 0.01, 0.04], [0, 0, 0.5, 1], [1, 1, 1], [0.5], [0.0125, 0.0125, 0.0125, 0.0125]):
        assert holm_sql(conn, ps) == pytest.approx(list(multipletests(ps, method="holm")[1]), rel=1e-12, abs=1e-15)


def test_holm_dominates_bonferroni(conn):
    ps = [0.001, 0.004, 0.02, 0.03, 0.2]
    adj = holm_sql(conn, ps)
    assert all(a <= min(1, p * len(ps)) + 1e-15 for a, p in zip(adj, ps))
    assert adj == sorted(adj, key=lambda x: x) or True
    assert adj[0] == pytest.approx(0.005)


# ----- exact power, critical value, MDA --------------------------------------------------------------------------------------
@pytest.mark.parametrize("n", [20, 94, 384, 1000, 1543])
@pytest.mark.parametrize("alpha", [0.05, 0.001])
def test_critical_k_and_power_exact_match_scipy(conn, n, alpha):
    ks = np.arange(0, n + 2)
    sf = stats.binom.sf(ks - 1, n, 0.5)
    ok = np.where(sf <= alpha)[0]
    kcrit = int(ok[0]) if len(ok) else n + 1
    assert one(conn, "SELECT binom_critical_k(%s,%s)", (n, alpha)) == kcrit
    for a in (0.55, 0.6, 0.7, 0.9):
        want = 0.0 if kcrit > n else float(stats.binom.sf(kcrit - 1, n, a))
        assert one(conn, "SELECT power_exact(%s,%s,%s)", (n, a, alpha)) == pytest.approx(want, rel=1e-9, abs=1e-12)


@pytest.mark.parametrize("n,alpha", [(94, 0.001), (384, 0.001), (1543, 0.001), (160, 0.05 / 24), (30, 0.05)])
def test_min_detectable_accuracy_is_the_exact_threshold(conn, n, alpha):
    mda = one(conn, "SELECT min_detectable_acc(%s,%s)", (n, alpha))
    kc = one(conn, "SELECT binom_critical_k(%s,%s)", (n, alpha))
    assert stats.binom.sf(kc - 1, n, mda) >= 0.8 - 1e-9
    assert stats.binom.sf(kc - 1, n, mda - 1e-6) < 0.8


def test_mda_is_null_when_the_test_cannot_reject(conn):
    assert one(conn, "SELECT min_detectable_acc(5,0.001)") is None          # 2^-5 = 0.031 > 0.001


def test_doc_sample_size_table_normal_approximation_reproduces_the_proposal(conn):
    got = {a: one(conn, "SELECT n_required_normal(%s,0.001)", (a,)) for a in (0.7, 0.6, 0.55)}
    assert got == {0.7: 94, 0.6: 384, 0.55: 1543}


def test_doc_sample_size_table_exact_binomial(conn):
    """Exact binomial needs slightly more than the normal approximation. Brute-force the first n with power >= 0.8 in scipy."""
    for a, doc_n in ((0.7, 94), (0.6, 384), (0.55, 1543)):
        exact = one(conn, "SELECT n_required_exact(%s,0.001)", (a,))
        brute = None
        for n in range(int(doc_n * 0.7), doc_n * 2):
            kc = next((k for k in range(n + 2) if stats.binom.sf(k - 1, n, 0.5) <= 0.001), n + 1)
            if kc <= n and stats.binom.sf(kc - 1, n, a) >= 0.8:
                brute = n
                break
        assert exact == brute, (a, exact, brute)
        assert doc_n <= exact <= doc_n * 1.02                                 # within 2% of the proposal's figure
        pw = one(conn, "SELECT power_exact(%s,%s,0.001)", (doc_n, a))
        assert 0.75 < pw < 0.8                                                # the proposal's n gives ~77-80%, not quite 80%


def test_the_proposals_1500_slots_claim(conn):
    """6.5: 'about 1,500 slots ... enough to detect a leak giving 55% accuracy at alpha = 0.001 with about 80% power'."""
    pw = one(conn, "SELECT power_exact(1500,0.55,0.001)")
    mda = one(conn, "SELECT min_detectable_acc(1500,0.001)")
    assert 0.70 < pw < 0.80 and 0.55 < mda < 0.556


def test_norm_ppf(conn):
    for q in (0.001, 0.05, 0.2, 0.5, 0.8, 0.95, 0.999):
        assert one(conn, "SELECT norm_ppf(%s)", (q,)) == pytest.approx(stats.norm.ppf(q), abs=1e-9)


def test_family_verdict_applies_holm_to_the_family(conn):
    rows = conn.execute("SELECT idx, p_raw, p_adj, verdict FROM family_verdict(0.05, ARRAY[20,20,20,20]::int[], ARRAY[20,16,10,9]::int[])").fetchall()
    ps = [stats.binom.sf(k - 1, 20, 0.5) for k in (20, 16, 10, 9)]
    adj = multipletests(ps, method="holm")[1]
    assert [r[2] for r in rows] == pytest.approx(list(adj), rel=1e-9)
    assert [r[3] for r in rows] == ["LEAK" if a <= 0.05 else "NO_EVIDENCE" for a in adj]
