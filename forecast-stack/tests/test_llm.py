"""The LLM layer: JSON extraction, the offline provider, provider resolution."""
from __future__ import annotations

import os

import pytest

from forecast_stack import llm


def test_extract_json_plain():
    assert llm.extract_json('{"p": 0.7}') == {"p": 0.7}


def test_extract_json_fenced():
    assert llm.extract_json('Here you go:\n```json\n{"p": 0.2}\n```\n') == {"p": 0.2}


def test_extract_json_embedded_in_prose():
    assert llm.extract_json('The answer is {"p": 0.4, "why": "base rate"} as stated.') == {
        "p": 0.4, "why": "base rate"}


def test_extract_json_nested():
    assert llm.extract_json('{"a": {"b": 1}}') == {"a": {"b": 1}}


def test_extract_json_raises_on_no_object():
    with pytest.raises(llm.LLMError):
        llm.extract_json("no json here")


def test_mock_provider_returns_a_probability():
    text = llm.complete("Forecast the probability it rains.", provider="mock")
    assert "Probability:" in text


def test_mock_provider_is_callable_without_keys():
    assert llm.complete("hello", provider="mock")


def test_mock_samples_vary():
    samples = {llm.complete("same prompt", provider="mock") for _ in range(5)}
    assert len(samples) > 1


def test_available_providers_honours_mock_flag(monkeypatch):
    monkeypatch.setenv("FORECAST_STACK_MOCK", "1")
    assert llm.available_providers() == ["mock"]


def test_default_provider_raises_a_helpful_error(monkeypatch):
    for provider in llm.PROVIDERS.values():
        if provider.key_env:
            monkeypatch.delenv(provider.key_env, raising=False)
    monkeypatch.delenv("FORECAST_STACK_PROVIDER", raising=False)
    monkeypatch.delenv("FORECAST_STACK_BASE_URL", raising=False)
    monkeypatch.delenv("FORECAST_STACK_MOCK", raising=False)
    monkeypatch.setattr(llm, "_ENV_LOADED", True)
    with pytest.raises(llm.LLMError, match="no LLM provider configured"):
        llm.default_provider()


def test_explicit_provider_beats_detection(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("FORECAST_STACK_MOCK", raising=False)
    monkeypatch.setattr(llm, "_ENV_LOADED", True)
    assert llm.default_provider() == "deepseek"


def test_unknown_provider_name_rejected():
    with pytest.raises(llm.LLMError, match="unknown provider"):
        llm._provider("not-a-provider")
