"""Scoring rules: propriety, orientation, and the logit helpers."""
import numpy as np
import pytest

from beyond_brier import brier, log_score, logit, expit, get_score


def test_brier_corners():
    assert brier(1.0, 1)[()] == 0.0
    assert brier(0.0, 1)[()] == 1.0
    assert np.isclose(brier(0.5, 1), 0.25)


def test_brier_is_a_loss():
    # confident-correct loses less than confident-wrong
    assert brier(0.9, 1) < brier(0.1, 1)


def test_log_score_proper_in_expectation():
    # under truth p*, the expected log loss is minimized at report == p*
    rng = np.random.default_rng(0)
    pstar = 0.7
    y = (rng.uniform(size=200_000) < pstar).astype(float)
    losses = {r: log_score(r, y).mean() for r in (0.5, 0.6, 0.7, 0.8, 0.9)}
    assert min(losses, key=losses.get) == 0.7


def test_log_score_clip_keeps_finite():
    assert np.isfinite(log_score(1.0, 0))   # would be inf without the clip
    assert np.isfinite(log_score(0.0, 1))


def test_logit_expit_roundtrip():
    p = np.array([0.1, 0.3, 0.5, 0.8])
    assert np.allclose(expit(logit(p)), p, atol=1e-6)


def test_get_score_unknown_raises():
    with pytest.raises(ValueError):
        get_score("crps")


def test_get_score_returns_callables():
    assert get_score("brier")(0.5, 1) == brier(0.5, 1)
    assert get_score("log") is log_score
