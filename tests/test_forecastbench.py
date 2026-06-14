"""Integration: the public ForecastBench round reproduces the paper headline.

Skips cleanly when the public files have not been fetched (CI without network).
Run `python -m data.fetch` first to exercise this test locally.
"""
import numpy as np
import pytest

pytest.importorskip("statsmodels")

from beyond_brier import build_leaderboard, reorder_stats


def _load():
    from beyond_brier.forecastbench import load_round
    try:
        return load_round()
    except FileNotFoundError:
        pytest.skip("public ForecastBench files not fetched; run `python -m data.fetch`")


def test_round_loads_with_expected_schema():
    df = _load()
    for c in ("forecaster", "question", "track", "p_f", "p_ref", "y"):
        assert c in df.columns
    assert set(df["y"].unique()) <= {0.0, 1.0}
    assert df["p_f"].between(0, 1).all()


def test_market_track_reorders():
    df = _load()
    board = build_leaderboard(df[df.track == "market"], min_n=20,
                              difficulty_adjust=True)
    r = reorder_stats(board)
    # the headline: an informative prior reorders the board well below rho=1
    assert r.n_forecasters >= 20
    assert 0.5 < r.spearman_rho < 0.85
    assert r.n_edge_positive_fdr >= 3


def test_data_track_is_the_identity():
    df = _load()
    board = build_leaderboard(df[df.track == "data"], min_n=30,
                              difficulty_adjust=False)
    r = reorder_stats(board)
    # constant prior -> edge rank == Brier rank to machine precision
    assert np.isclose(r.spearman_rho, 1.0, atol=1e-6)
