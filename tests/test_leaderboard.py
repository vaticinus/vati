"""End-to-end leaderboard build + reorder statistics."""
import numpy as np
import pandas as pd
import pytest

from beyond_brier import build_leaderboard, reorder_stats


def _field(seed=0, n=800):
    """A market with a copier (echoes price) and a contributor (own signal)."""
    rng = np.random.default_rng(seed)
    truth = rng.uniform(0.05, 0.95, n)
    y = (rng.uniform(size=n) < truth).astype(int)
    price = np.clip(truth + rng.normal(0, 0.12, n), 0.02, 0.98)
    copier = np.clip(price + rng.normal(0, 0.01, n), 0.02, 0.98)
    contributor = np.clip(truth + rng.normal(0, 0.05, n), 0.02, 0.98)
    frames = []
    for name, p in [("copier", copier), ("contributor", contributor)]:
        frames.append(pd.DataFrame({"forecaster": name, "question": np.arange(n),
                                    "p_f": p, "p_ref": price, "y": y}))
    return pd.concat(frames, ignore_index=True)


def test_required_columns_enforced():
    with pytest.raises(ValueError):
        build_leaderboard(pd.DataFrame({"forecaster": ["a"], "p_f": [0.5]}))


def test_board_has_expected_columns():
    board = build_leaderboard(_field(), min_n=50, bootstrap=1000,
                              difficulty_adjust=False)
    for c in ("edge", "edge_lo", "edge_hi", "mean_brier", "brier_rank",
              "edge_rank", "rank_change", "edge_positive", "edge_positive_fdr"):
        assert c in board.columns


def test_copier_edge_indistinguishable_from_zero():
    board = build_leaderboard(_field(), min_n=50, bootstrap=4000,
                              difficulty_adjust=False).set_index("forecaster")
    # the copier's CI straddles zero; the contributor's clears it
    assert board.loc["copier", "edge_lo"] <= 0 <= board.loc["copier", "edge_hi"]
    assert board.loc["contributor", "edge_lo"] > 0


def test_min_n_filters_small_forecasters():
    df = _field()
    df = pd.concat([df, pd.DataFrame({"forecaster": "tiny",
                                      "question": np.arange(5),
                                      "p_f": 0.5, "p_ref": 0.5, "y": 1})])
    board = build_leaderboard(df, min_n=50, bootstrap=500, difficulty_adjust=False)
    assert "tiny" not in set(board["forecaster"])


def test_constant_prior_identity():
    """Constant prior -> edge ranking equals Brier ranking, exactly."""
    df = _field()
    df["p_ref"] = 0.5
    board = build_leaderboard(df, min_n=50, bootstrap=500, difficulty_adjust=False)
    b = board.sort_values("forecaster")
    assert (b["edge_rank"].to_numpy() == b["brier_rank"].to_numpy()).all()
    r = reorder_stats(board)
    assert np.isclose(r.spearman_rho, 1.0)


def test_reorder_stats_shape():
    board = build_leaderboard(_field(), min_n=50, bootstrap=500,
                              difficulty_adjust=False)
    r = reorder_stats(board)
    d = r.as_dict()
    assert d["n_forecasters"] == 2
    assert -1.0 <= d["spearman_rho"] <= 1.0
