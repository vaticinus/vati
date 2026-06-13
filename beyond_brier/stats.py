"""Inference helpers: bootstrap CIs, Diebold-Mariano, Benjamini-Hochberg.

Kept deliberately small and dependency-light. These are the honesty knobs of
the metric: a per-question edge of +0.03 means nothing without an interval and a
multiplicity correction, and the paper's whole posture is to report both.
"""
from __future__ import annotations

import numpy as np
from scipy import stats as _ss


def bootstrap_ci(values, B: int = 10000, alpha: float = 0.05, seed: int = 20240721):
    """Percentile bootstrap CI for the mean of ``values`` (resampling questions)."""
    lo, hi, _ = bootstrap_test(values, B=B, alpha=alpha, seed=seed)
    return (lo, hi)


def bootstrap_test(values, B: int = 10000, alpha: float = 0.05, seed: int = 20240721):
    """Percentile bootstrap CI *and* a two-sided p-value for ``mean(values) == 0``.

    Resamples questions with replacement. Returns ``(lo, hi, p)``. The p-value is
    coherent with the CI: ``p < alpha`` iff the ``(1 - alpha)`` CI excludes 0
    (up to bootstrap noise), so "CI strictly above zero" and "passes the test"
    agree. This is the test the FDR step uses.
    """
    v = np.asarray(values, dtype=float)
    n = v.size
    if n < 2:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(B, n))
    boots = v[idx].mean(axis=1)
    lo = float(np.percentile(boots, 100 * alpha / 2))
    hi = float(np.percentile(boots, 100 * (1 - alpha / 2)))
    frac_le = float((boots <= 0).mean())
    p = 2 * min(frac_le, 1 - frac_le)
    p = max(p, 1.0 / B)  # floor at bootstrap resolution
    return (lo, hi, float(p))


def diebold_mariano(d, h: int = 1):
    """Diebold-Mariano test that mean(d) == 0, with the Harvey-Leybourne-Newbold
    small-sample correction (Harvey, Leybourne & Newbold, 1997).

    ``d`` is the per-question score-difference series (the edge). Returns
    ``(stat, p_two_sided)``. For ``h == 1`` (no multi-step overlap) this reduces
    to a t-test on the mean with the HLN finite-sample shrinkage applied.
    """
    d = np.asarray(d, dtype=float)
    n = d.size
    if n < 2:
        return (float("nan"), float("nan"))
    dbar = d.mean()
    # long-run variance with Newey-West style lags up to h-1
    gamma0 = np.sum((d - dbar) ** 2) / n
    var = gamma0
    for k in range(1, h):
        gk = np.sum((d[k:] - dbar) * (d[:-k] - dbar)) / n
        var += 2 * (1 - k / h) * gk
    if var <= 0:
        return (float("nan"), float("nan"))
    dm = dbar / np.sqrt(var / n)
    # HLN finite-sample correction
    corr = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    dm *= corr
    p = 2 * _ss.t.sf(np.abs(dm), df=n - 1)
    return (float(dm), float(p))


def benjamini_hochberg(pvals, q: float = 0.10):
    """Benjamini-Hochberg FDR control. Returns a boolean array of rejections at
    level ``q`` aligned to the input order."""
    p = np.asarray(pvals, dtype=float)
    m = p.size
    order = np.argsort(p)
    ranked = p[order]
    thresh = q * (np.arange(1, m + 1) / m)
    passed = ranked <= thresh
    reject = np.zeros(m, dtype=bool)
    if passed.any():
        kmax = np.max(np.where(passed)[0])
        reject_sorted = np.zeros(m, dtype=bool)
        reject_sorted[: kmax + 1] = True
        reject[order] = reject_sorted
    return reject


def spearman(a, b):
    rho, p = _ss.spearmanr(a, b)
    return (float(rho), float(p))


def kendall(a, b):
    tau, p = _ss.kendalltau(a, b)
    return (float(tau), float(p))
