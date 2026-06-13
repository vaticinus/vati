"""beyond-brier: rank forecasters by the information they add over the market,
not by Brier.

Quickstart
----------
>>> import numpy as np, pandas as pd
>>> from beyond_brier import build_leaderboard, reorder_stats
>>> # long table: one row per (forecaster, question)
>>> board = build_leaderboard(df)           # df has forecaster, question, p_f, p_ref, y
>>> print(reorder_stats(board))

The single most useful import for a one-forecaster check is
:func:`marginal_edge`; for a whole field, :func:`build_leaderboard`.
"""
from .scores import brier, log_score, logit, expit, get_score
from .edge import marginal_edge, per_question_edge, debias_logscore, EdgeResult
from .adjust import difficulty_adjusted_effects
from .decompose import encompassing_regression, Encompass
from .leaderboard import build_leaderboard, reorder_stats, Reorder
from .stats import bootstrap_ci, bootstrap_test, diebold_mariano, benjamini_hochberg

__version__ = "0.1.0"

__all__ = [
    "brier", "log_score", "logit", "expit", "get_score",
    "marginal_edge", "per_question_edge", "debias_logscore", "EdgeResult",
    "difficulty_adjusted_effects",
    "encompassing_regression", "Encompass",
    "build_leaderboard", "reorder_stats", "Reorder",
    "bootstrap_ci", "bootstrap_test", "diebold_mariano", "benjamini_hochberg",
]
