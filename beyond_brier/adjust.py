"""Difficulty-adjusted forecaster effect.

Forecasters answer *different* subsets of questions, so a high mean edge can
reflect an easier question mix rather than more skill. We net out question
difficulty with a crossed two-way model over the per-question edges::

    d_if = mu + alpha_f + gamma_i + eps

estimated by two-way fixed effects (forecaster dummies + question dummies). The
headline statistic is ``alpha_f``, the edge net of question difficulty. The raw
mean edge is reported only to expose the confound it carries.

This is implemented with a within-transform (demeaning) rather than building a
dense dummy matrix, so it scales to thousands of questions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def difficulty_adjusted_effects(long: pd.DataFrame,
                                forecaster: str = "forecaster",
                                question: str = "question",
                                edge: str = "edge") -> pd.DataFrame:
    """Two-way fixed-effects forecaster effects from a long edge table.

    Parameters
    ----------
    long : DataFrame with one row per (forecaster, question) and a per-question
        ``edge`` column.

    Returns a DataFrame indexed by forecaster with columns ``alpha`` (the
    difficulty-adjusted effect, centered to mean zero) and ``n`` (questions),
    sorted by ``alpha`` descending.

    Method: iterative two-way demeaning (alternating projection / the
    Frisch-Waugh-Lovell within estimator). For a balanced panel this converges
    in one pass; for the sparse market panel it converges in a handful.
    """
    df = long[[forecaster, question, edge]].copy()
    df.columns = ["f", "q", "d"]
    y = df["d"].to_numpy(dtype=float)
    f_codes, f_levels = pd.factorize(df["f"])
    q_codes, _ = pd.factorize(df["q"])
    nf = len(f_levels)

    af = np.zeros(nf)
    aq = np.zeros(q_codes.max() + 1)
    mu = y.mean()
    resid = y - mu
    for _ in range(200):
        # forecaster means of current residual (holding question effects)
        r = y - mu - aq[q_codes]
        new_af = np.bincount(f_codes, weights=r, minlength=nf) / np.bincount(f_codes, minlength=nf)
        # question means of current residual (holding forecaster effects)
        r2 = y - mu - new_af[f_codes]
        new_aq = np.bincount(q_codes, weights=r2) / np.bincount(q_codes)
        if np.max(np.abs(new_af - af)) < 1e-10 and np.max(np.abs(new_aq - aq)) < 1e-10:
            af, aq = new_af, new_aq
            break
        af, aq = new_af, new_aq

    af = af - af.mean()  # center: alpha_f is relative skill, identified up to a constant
    counts = np.bincount(f_codes, minlength=nf)
    out = pd.DataFrame({"forecaster": f_levels, "alpha": af, "n": counts})
    out = out.set_index("forecaster").sort_values("alpha", ascending=False)
    return out
