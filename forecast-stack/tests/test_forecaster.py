"""The LLM forecaster: probability parsing and self-consistency blending."""
from __future__ import annotations

import pytest

from forecast_stack.model import llm_forecaster


def test_parse_probability_standard_line():
    assert llm_forecaster.parse_probability("reasoning...\nProbability: 0.42") == 0.42


def test_parse_probability_accepts_equals_and_bare_decimals():
    assert llm_forecaster.parse_probability("probability = .3") == 0.3
    assert llm_forecaster.parse_probability("probability: .75") == 0.75


def test_parse_probability_takes_the_last_mention():
    assert llm_forecaster.parse_probability("Probability: 0.2 ... revised\nProbability: 0.8") == 0.8


def test_parse_probability_falls_back_to_a_trailing_decimal():
    assert llm_forecaster.parse_probability("I estimate 0.65 overall.") == 0.65


def test_parse_probability_clamps_extremes():
    assert llm_forecaster.parse_probability("Probability: 1.0") == 0.99
    assert llm_forecaster.parse_probability("Probability: 0.0") == 0.01


def test_parse_probability_returns_none_without_a_number():
    assert llm_forecaster.parse_probability("I cannot say.") is None
    assert llm_forecaster.parse_probability("") is None


def test_build_prompt_carries_the_as_of_bound():
    prompt = llm_forecaster.build_prompt("Will X happen?", asof="2026-09-01",
                                         resolution_criteria="X per source Y",
                                         context="news")
    assert "Will X happen?" in prompt
    assert "2026-09-01" in prompt
    assert "Resolution criteria: X per source Y" in prompt


def test_forecast_blends_samples_with_the_mock_provider():
    result = llm_forecaster.forecast("Will the Fed cut in December 2026?",
                                     asof="2026-09-01", provider="mock", n=3)
    assert 0.0 < result.probability < 1.0
    assert len(result.samples) == 3
    assert result.failed == 0
    assert result.provider == "mock"
    assert result.spread >= 0.0


def test_forecast_single_sample_is_a_valid_probability():
    result = llm_forecaster.forecast("Will it rain tomorrow?", provider="mock", n=1)
    assert len(result.samples) == 1
    assert 0.01 <= result.probability <= 0.99


def test_forecast_reports_spread_when_samples_disagree():
    result = llm_forecaster.forecast("An uncertain question?", provider="mock", n=5)
    assert result.spread > 0.0
