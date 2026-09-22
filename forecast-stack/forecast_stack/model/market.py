"""Market-question forecaster.

Market questions (~203/round, manifold/metaculus/polymarket/infer) ship with the
crowd's own probability (freeze_datetime_value). The crowd is a very strong,
hard-to-beat anchor (resolved-row Brier ~0.04-0.11). Top ForecastBench entries
all feed it in. So our market forecast = crowd prior, optionally nudged by an
LLM+news adjustment, then lightly calibrated.

Keyless-first: the LLM nudge runs on keyless models / in-session reasoning; any
paid news API is costed and approved first. Until then, crowd-anchor + calibration
is the baseline (already competitive on the market half).
"""
from __future__ import annotations

import math

from ..eval.score import MARKET_SOURCES, single_questions


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z):
    return 1 / (1 + math.exp(-z))


MARKET_CALIBRATION = {
    # Re-checked 2026-07-30 on 7,130 resolved rows across all eras: the infer and
    # polymarket transforms win in every era; the manifold and metaculus downshifts
    # LOSE in every era including the one they were fit on (metaculus raw 0.0350 vs
    # cal 0.0388 on 2026 rounds). Manifold/metaculus now pass through raw (identity).
    "infer": (0.80, -1.00),
    "polymarket": (1.10, -0.25),
}


def calibrate(p, extremize=1.0, floor=0.02):
    """Map a probability through a logit-scale extremization (>1 sharpens toward
    0/1, <1 softens toward 0.5) and a clamp. extremize=1.0 is identity."""
    z = _logit(p) * extremize
    q = _sigmoid(z)
    return min(max(q, floor), 1 - floor)


def calibrate_market_probability(src: str, p: float, extremize=1.0) -> float:
    """Source-wise crowd calibration, fit by leave-one-round-out checks.

    The crowd anchor is already strong; these coarse transforms only correct the
    repeated source-level bias seen historically. Unknown sources fall back to the
    old identity calibration.
    """
    params = MARKET_CALIBRATION.get(str(src).lower())
    if params is None:
        return calibrate(p, extremize=extremize)
    slope, intercept = params
    q = _sigmoid(slope * _logit(p) + intercept)
    if extremize != 1.0:
        q = calibrate(q, extremize=extremize)
    return min(max(q, 0.02), 0.98)


def crowd_anchor(questions, extremize=1.0) -> dict:
    """Market forecast = (calibrated) crowd value."""
    f = {}
    for q in single_questions(questions):
        if q["source"] in MARKET_SOURCES:
            try:
                p = float(q["freeze_datetime_value"])
            except (TypeError, ValueError, KeyError):
                continue          # no crowd value -> not anchored (LLM leg fills the gap)
            f[q["id"]] = calibrate_market_probability(q["source"], p, extremize=extremize)
    return f
