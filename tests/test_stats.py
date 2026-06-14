"""Inference helpers: bootstrap, Diebold-Mariano, Benjamini-Hochberg."""
import numpy as np

from beyond_brier import bootstrap_ci, bootstrap_test, diebold_mariano, benjamini_hochberg
from beyond_brier.stats import spearman, kendall


def test_bootstrap_ci_brackets_the_mean():
    rng = np.random.default_rng(0)
    x = rng.normal(0.5, 1.0, 2000)
    lo, hi = bootstrap_ci(x, B=2000)
    assert lo < x.mean() < hi


def test_bootstrap_ci_coverage():
    # a nominal 95% CI should cover the true mean ~95% of the time
    rng = np.random.default_rng(1)
    hits = 0
    trials = 200
    for _ in range(trials):
        x = rng.normal(0.0, 1.0, 120)
        lo, hi = bootstrap_ci(x, B=1000)
        hits += lo <= 0.0 <= hi
    assert 0.88 <= hits / trials <= 1.0


def test_bootstrap_test_pvalue_coheres_with_ci():
    # CI excludes 0  <=>  p < alpha
    rng = np.random.default_rng(2)
    x = rng.normal(0.4, 1.0, 300)
    lo, hi, p = bootstrap_test(x, B=4000, alpha=0.05)
    assert (lo > 0 or hi < 0) == (p < 0.05)


def test_bootstrap_handles_degenerate():
    lo, hi, p = bootstrap_test([1.0])
    assert np.isnan(lo) and np.isnan(hi) and np.isnan(p)


def test_diebold_mariano_detects_signal():
    rng = np.random.default_rng(3)
    d = rng.normal(0.3, 0.5, 400)   # clearly positive mean
    stat, p = diebold_mariano(d)
    assert stat > 0 and p < 0.01


def test_diebold_mariano_null():
    rng = np.random.default_rng(4)
    d = rng.normal(0.0, 1.0, 500)
    _, p = diebold_mariano(d)
    assert p > 0.05


def test_benjamini_hochberg_basic():
    # three tiny p-values and many nulls -> the tiny ones reject
    p = np.array([0.001, 0.002, 0.003] + [0.6] * 50)
    rej = benjamini_hochberg(p, q=0.10)
    assert rej[:3].all()
    assert not rej[3:].any()


def test_benjamini_hochberg_all_null():
    p = np.full(40, 0.8)
    assert not benjamini_hochberg(p, q=0.10).any()


def test_rank_correlations_extremes():
    a = np.arange(10)
    assert np.isclose(spearman(a, a)[0], 1.0)
    assert np.isclose(spearman(a, a[::-1])[0], -1.0)
    assert np.isclose(kendall(a, a)[0], 1.0)
