"""The model layer: quant estimators, the ensemble metrics, and the crowd anchor."""
from __future__ import annotations

from datetime import date, timedelta

from forecast_stack.eval.score import crowd_forecast
from forecast_stack.model import ensemble, market, quant


def _series(values, start=date(2015, 1, 1), step_days=30):
    return [(start + timedelta(days=i * step_days), value) for i, value in enumerate(values)]


# ── quant estimators ─────────────────────────────────────────────────────────
def test_baserate_is_high_on_a_clean_uptrend():
    history = _series([100 + i for i in range(30)])
    p = quant.p_higher_baserate(history, date(2018, 1, 1), 365)
    assert p is not None and p > 0.8


def test_baserate_is_low_on_a_flat_series():
    history = _series([100 for _ in range(30)])
    p = quant.p_higher_baserate(history, date(2018, 1, 1), 365)
    assert p is not None and p < 0.2


def test_baserate_needs_history():
    assert quant.p_higher_baserate(_series([1, 2, 3]), date(2016, 1, 1), 30) is None


def test_baserate_ignores_observations_after_the_due_date():
    """Point-in-time discipline: post-due values must not enter the estimate."""
    base = _series([100 + i for i in range(20)])
    contaminated = base + [(date(2020, 1, 1), 100), (date(2020, 2, 1), 100)]
    clean = quant.p_higher_baserate(base, date(2018, 6, 1), 90)
    dirty = quant.p_higher_baserate(contaminated, date(2018, 6, 1), 90)
    assert clean == dirty


def test_drift_returns_a_probability_or_none():
    history = _series([100 + i for i in range(30)])
    p = quant.p_higher_drift(history, date(2018, 1, 1), 90, False)
    assert p is None or 0.0 <= p <= 1.0


def test_seasonal_returns_a_probability_or_none():
    values = []
    for year in range(3):
        for month in range(1, 13):
            values.append(100 + 10 * (month >= 6) + year)
    history = [(date(2015 + i // 12, i % 12 + 1, 1), value) for i, value in enumerate(values)]
    p = quant.p_higher_seasonal(history, date(2018, 1, 1), date(2018, 7, 1))
    assert p is None or 0.0 <= p <= 1.0


def test_joint_up_stays_a_probability_and_rises_with_correlation():
    for rho in (-0.9, -0.5, 0.0, 0.5, 0.9):
        value = quant.joint_up(0.6, 0.6, rho)
        assert 0.0 <= value <= 1.0
    assert quant.joint_up(0.6, 0.6, 0.9) > quant.joint_up(0.6, 0.6, -0.9)


def test_dataset_calibration_stays_in_range():
    for source in ("yfinance", "fred", "dbnomics", "wikipedia", "unknown"):
        value = quant.calibrate_dataset_probability(source, 0.7)
        assert 0.0 < value < 1.0


# ── ensemble metrics ─────────────────────────────────────────────────────────
def test_ensemble_brier():
    assert ensemble.brier([1.0, 0.0], [1, 0]) == 0.0
    assert ensemble.brier([0.0, 1.0], [1, 0]) == 1.0


def test_ensemble_auc_orders_correctly():
    assert ensemble.auc([0.9, 0.1], [1, 0]) == 1.0
    assert ensemble.auc([0.1, 0.9], [1, 0]) == 0.0
    assert ensemble.auc([0.5, 0.5], [1, 0]) == 0.5
    assert ensemble.auc([0.5], [1]) is None


def test_ensemble_corr():
    assert ensemble.corr([1, 2, 3], [1, 2, 3]) == 1.0
    assert round(ensemble.corr([1, 2, 3], [3, 2, 1]), 6) == -1.0
    assert ensemble.corr([1], [2]) is None
    assert ensemble.corr([1, 1, 1], [1, 2, 3]) is None


def test_best_linear_blend_prefers_the_informative_column():
    ys = [1, 1, 1, 0, 0, 0]
    good = [0.9, 0.9, 0.9, 0.1, 0.1, 0.1]
    noise = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
    idx = list(range(len(ys)))
    weights, rep_brier = ensemble.best_linear_blend([good, noise], ys, idx, idx, step=0.25)
    assert weights[0] > weights[1]
    assert rep_brier >= 0.0


# ── crowd anchor ─────────────────────────────────────────────────────────────
def test_calibrate_is_identity_at_extremize_one():
    assert market.calibrate(0.5) == 0.5


def test_calibrate_clamps_to_the_floor():
    assert market.calibrate(0.0) == 0.02
    assert market.calibrate(1.0) == 0.98


def test_calibrate_extremize_sharpens():
    assert market.calibrate(0.6, extremize=2.0) > 0.6
    assert market.calibrate(0.4, extremize=2.0) < 0.4


def test_unknown_market_source_passes_through_calibration():
    assert market.calibrate_market_probability("unknown-source", 0.5) == 0.5


def test_crowd_anchor_covers_only_market_questions():
    questions = [
        {"id": "m1", "source": "polymarket", "freeze_datetime_value": 0.8},
        {"id": "m2", "source": "metaculus", "freeze_datetime_value": 0.2},
        {"id": "d1", "source": "fred", "freeze_datetime_value": None},
    ]
    anchor = crowd_anchor = market.crowd_anchor(questions)
    assert set(anchor) == {"m1", "m2"}
    assert all(0.0 < value < 1.0 for value in anchor.values())
    assert anchor["m1"] > anchor["m2"]


def test_crowd_anchor_skips_questions_without_a_value():
    questions = [{"id": "m1", "source": "polymarket"}]
    assert market.crowd_anchor(questions) == {}


def test_crowd_forecast_and_crowd_anchor_agree_on_ordering():
    questions = [
        {"id": "m1", "source": "polymarket", "freeze_datetime_value": 0.8},
        {"id": "m2", "source": "metaculus", "freeze_datetime_value": 0.2},
    ]
    assert crowd_forecast(questions)["m1"] > crowd_forecast(questions)["m2"]
