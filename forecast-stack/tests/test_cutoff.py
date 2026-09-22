"""The cutoff probe: measure recall, gate on it, refuse to over-claim."""
from __future__ import annotations

from forecast_stack.eval.cutoff import outcome_after_checkpoint, measure_cutoff, recall_cutoff

PROBES = [
    {"q": "fact A", "year": 2020, "keys": ["alpha"]},
    {"q": "fact B", "year": 2023, "keys": ["beta"]},
    {"q": "fact C", "year": 2025, "keys": ["gamma"]},
]


def test_recall_cutoff_returns_latest_known_year():
    answers = {"fact A": "alpha", "fact B": "beta", "fact C": "I don't know"}
    cutoff, results = recall_cutoff(answers.__getitem__, PROBES)
    assert cutoff == 2023
    assert [r.knows for r in results] == [True, True, False]


def test_recall_cutoff_blind_returns_none():
    cutoff, results = recall_cutoff(lambda _q: "I don't know", PROBES)
    assert cutoff is None
    assert all(not r.knows for r in results)


def test_recall_cutoff_treats_errors_as_blind():
    def flaky(question: str) -> str:
        if question == "fact B":
            raise RuntimeError("filtered")
        return "alpha"

    cutoff, results = recall_cutoff(flaky, PROBES)
    assert cutoff == 2020
    assert results[1].error is not None


def test_recall_cutoff_is_case_insensitive():
    cutoff, _ = recall_cutoff(lambda _q: "The answer is BETA.", PROBES)
    assert cutoff == 2023


def test_checkpoint_eligibility_excludes_same_day_and_earlier_outcomes():
    assert outcome_after_checkpoint("2024-05-01", "2023-12-31")
    assert not outcome_after_checkpoint("2023-12-31", "2023-12-31")
    assert not outcome_after_checkpoint("2022-01-01", "2023-12-31")


def test_checkpoint_eligibility_refuses_recall_years_and_unknown_provenance():
    assert not outcome_after_checkpoint("2026-01-01", None)
    assert not outcome_after_checkpoint("2026-01-01", 2023)
    assert not outcome_after_checkpoint("2026-01-01", "2023")


def test_checkpoint_eligibility_rejects_invalid_calendar_dates():
    assert not outcome_after_checkpoint("2026-99-99", "2023-12-31")
    assert not outcome_after_checkpoint("2026-01-01", "2023-02-29")


def test_measure_cutoff_with_mock_provider_is_blind():
    measurement = measure_cutoff(provider="mock")
    assert measurement.provider == "mock"
    assert measurement.is_blind
    assert measurement.cutoff_year is None
