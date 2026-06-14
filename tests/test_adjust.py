"""Difficulty-adjusted two-way fixed-effects forecaster effects."""
import numpy as np
import pandas as pd

from beyond_brier.adjust import difficulty_adjusted_effects


def test_recovers_known_effects_balanced_panel():
    # construct edges = mu + alpha_f + gamma_q with known alphas, full panel.
    rng = np.random.default_rng(0)
    fs = ["a", "b", "c", "d"]
    true_alpha = {"a": 0.06, "b": 0.02, "c": -0.02, "d": -0.06}
    qs = [f"q{i}" for i in range(60)]
    gamma = {q: rng.normal(0, 0.1) for q in qs}
    rows = []
    for f in fs:
        for q in qs:
            rows.append({"forecaster": f, "question": q,
                         "edge": 0.01 + true_alpha[f] + gamma[q]})
    out = difficulty_adjusted_effects(pd.DataFrame(rows))
    # ordering recovered exactly
    assert list(out.index) == ["a", "b", "c", "d"]
    # values recovered up to the centering constant
    est = out["alpha"]
    centered_true = {k: v - np.mean(list(true_alpha.values())) for k, v in true_alpha.items()}
    for f in fs:
        assert np.isclose(est[f], centered_true[f], atol=1e-6)


def test_alphas_center_to_zero():
    rng = np.random.default_rng(1)
    rows = []
    for f in ["x", "y", "z"]:
        for q in range(40):
            rows.append({"forecaster": f, "question": f"q{q}",
                         "edge": rng.normal(0, 0.1)})
    out = difficulty_adjusted_effects(pd.DataFrame(rows))
    assert np.isclose(out["alpha"].mean(), 0.0, atol=1e-9)


def test_difficulty_confound_is_removed():
    # f_easy answers only easy (high-edge) questions, f_hard only hard ones,
    # but both have the SAME true skill -> adjusted alphas should be ~equal
    # even though raw mean edge differs a lot.
    easy = [("q_e%d" % i, 0.08) for i in range(30)]
    hard = [("q_h%d" % i, -0.05) for i in range(30)]
    shared = [("q_s%d" % i, 0.0) for i in range(30)]
    rows = []
    for q, g in easy + shared:
        rows.append({"forecaster": "f_easy", "question": q, "edge": g + 0.01})
    for q, g in hard + shared:
        rows.append({"forecaster": "f_hard", "question": q, "edge": g + 0.01})
    out = difficulty_adjusted_effects(pd.DataFrame(rows))
    assert abs(out.loc["f_easy", "alpha"] - out.loc["f_hard", "alpha"]) < 0.02
