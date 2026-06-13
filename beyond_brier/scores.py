"""Strictly proper scoring rules, oriented as *losses* (lower is better).

Everything downstream is built on these two rules. Both are strictly proper:
a forecaster minimizes expected score only by reporting its true belief
(Gneiting & Raftery, 2007). We keep losses, not rewards, so that a positive
*edge* (Section ``edge``) always means "beat the reference".
"""
from __future__ import annotations

import numpy as np

EPS = 1e-3  # log-score clip; matches the paper's reported robustness setting


def _arr(x):
    return np.asarray(x, dtype=float)


def brier(p, y):
    """Brier (quadratic) loss, ``(p - y)**2``, for binary ``y`` in {0, 1}.

    Defined for ``y`` in [0, 1] too (the score stays proper for probabilistic
    outcomes), which is why it survives ForecastBench's occasional fractional
    resolutions untouched.
    """
    return (_arr(p) - _arr(y)) ** 2


def log_score(p, y, eps: float = EPS):
    """Negative log (logarithmic) loss, clipped to ``[eps, 1 - eps]``.

    ``-[y ln p + (1 - y) ln(1 - p)]``. Reported as the robustness check in the
    paper: any conclusion that holds under both Brier and log is not a quadratic
    artifact.
    """
    p = np.clip(_arr(p), eps, 1 - eps)
    y = _arr(y)
    return -(y * np.log(p) + (1 - y) * np.log1p(-p))


SCORES = {"brier": brier, "log": log_score}


def get_score(name: str):
    try:
        return SCORES[name]
    except KeyError:
        raise ValueError(f"unknown score {name!r}; choose from {sorted(SCORES)}")


def logit(p, eps: float = EPS):
    p = np.clip(_arr(p), eps, 1 - eps)
    return np.log(p / (1 - p))


def expit(z):
    return 1.0 / (1.0 + np.exp(-_arr(z)))
