"""The properties that make the metric trustworthy, as executable tests."""
import numpy as np
import pandas as pd
import pytest

from beyond_brier import (brier, log_score, marginal_edge, per_question_edge,
                          build_leaderboard, debias_logscore)


def test_brier_basic():
    assert brier(1.0, 1)[()] == 0.0
    assert brier(0.0, 1)[()] == 1.0
    assert np.isclose(brier(0.5, 1), 0.25)


def test_log_score_proper_direction():
    # confident-correct beats confident-wrong
    assert log_score(0.9, 1) < log_score(0.1, 1)


def test_edge_sign():
    # a forecaster that nails it beats a coin-flip prior
    y = np.array([1, 0, 1, 0])
    p_f = np.array([0.9, 0.1, 0.9, 0.1])
    p_ref = np.full(4, 0.5)
    er = marginal_edge(p_f, p_ref, y)
    assert er.edge > 0


def test_identity_constant_prior():
    """With a constant prior, ranking by edge == ranking by Brier, exactly."""
    rng = np.random.default_rng(1)
    N = 200
    y = rng.integers(0, 2, N)
    rows = []
    for name in ["a", "b", "c"]:
        p = np.clip(rng.uniform(size=N), 1e-3, 1 - 1e-3)
        rows.append(pd.DataFrame({"forecaster": name, "question": np.arange(N),
                                  "p_f": p, "p_ref": 0.5, "y": y}))
    long = pd.concat(rows)
    board = build_leaderboard(long, min_n=10, difficulty_adjust=False)
    b = board.sort_values("forecaster")
    assert (b["edge_rank"].values == b["brier_rank"].values).all()


def test_edge_is_score_difference():
    y = np.array([1, 0, 1])
    p_f = np.array([0.7, 0.2, 0.6])
    p_ref = np.array([0.5, 0.5, 0.4])
    d = per_question_edge(p_f, p_ref, y)
    expected = brier(p_ref, y) - brier(p_f, y)
    assert np.allclose(d, expected)


def test_debias_subtracts_half_over_n():
    assert np.isclose(debias_logscore(0.01, 100), 0.01 - 0.005)
    with pytest.raises(ValueError):
        debias_logscore(0.01, 0)


def test_missing_columns_raise():
    with pytest.raises(ValueError):
        build_leaderboard(pd.DataFrame({"forecaster": ["a"], "p_f": [0.5]}))
