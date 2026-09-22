"""Scoring: Brier, coverage imputation, per-source breakdown, baselines."""
from __future__ import annotations
import pytest

from forecast_stack.eval.score import (
    brier, crowd_forecast, resolved_rows, score_submission, single_questions,
    uniform_forecast,
)

QUESTIONS = [
    {"id": "m1", "source": "polymarket", "freeze_datetime_value": 0.80},
    {"id": "m2", "source": "metaculus", "freeze_datetime_value": 0.20},
    {"id": "d1", "source": "fred", "resolution_dates": ["2026-01-01"]},
    {"id": ["m1", "d1"], "source": "polymarket", "freeze_datetime_value": 0.5},  # combo
]

RESOLUTIONS = [
    {"id": "m1", "source": "polymarket", "resolution_date": None, "resolved_to": 1, "resolved": True},
    {"id": "m2", "source": "metaculus", "resolution_date": None, "resolved_to": 0, "resolved": True},
    {"id": "d1", "source": "fred", "resolution_date": "2026-01-01", "resolved_to": 1, "resolved": True},
    {"id": "d2", "source": "fred", "resolution_date": "2026-01-01", "resolved_to": 0, "resolved": False},
]


def test_single_questions_drops_combos():
    assert [q["id"] for q in single_questions(QUESTIONS)] == ["m1", "m2", "d1"]


def test_resolved_rows_filters_unresolved_and_combos():
    rows = resolved_rows(RESOLUTIONS)
    assert len(rows) == 3


def test_resolved_rows_source_filter():
    assert len(resolved_rows(RESOLUTIONS, sources={"fred"})) == 1


def test_brier_perfect_and_worst():
    rows = [{"id": "a", "source": "fred", "resolution_date": "x", "resolved_to": 1},
            {"id": "b", "source": "fred", "resolution_date": "x", "resolved_to": 0}]
    perfect = brier({"a": 1.0, "b": 0.0}, rows)
    assert perfect["brier"] == 0.0
    assert perfect["missing"] == 0
    worst = brier({"a": 0.0, "b": 1.0}, rows)
    assert worst["brier"] == 1.0


def test_brier_imputes_missing_at_half_and_counts_it():
    rows = [{"id": "a", "source": "fred", "resolution_date": "x", "resolved_to": 1},
            {"id": "gone", "source": "fred", "resolution_date": "x", "resolved_to": 1}]
    result = brier({"a": 0.9}, rows)
    assert result["missing"] == 1
    assert result["n"] == 2
    assert result["brier"] == (0.9 - 1) ** 2 / 2 + 0.25 / 2


def test_brier_by_source_breakdown():
    rows = [{"id": "a", "source": "fred", "resolution_date": "x", "resolved_to": 1},
            {"id": "b", "source": "yfinance", "resolution_date": "x", "resolved_to": 1}]
    result = brier({"a": 1.0, "b": 0.0}, rows)
    assert result["by_source"]["fred"] == 0.0
    assert result["by_source"]["yfinance"] == 1.0
    assert result["n_by_source"] == {"fred": 1, "yfinance": 1}


def test_uniform_forecast_covers_singles_only():
    forecast = uniform_forecast(QUESTIONS, p=0.4)
    assert forecast == {"m1": 0.4, "m2": 0.4, "d1": 0.4}


def test_crowd_forecast_uses_market_values_only():
    forecast = crowd_forecast(QUESTIONS)
    assert set(forecast) == {"m1", "m2"}
    assert forecast["m1"] == 0.80
    assert forecast["m2"] == 0.20


def test_score_submission_separates_market_and_dataset():
    submission = {"forecasts": [
        {"id": "m1", "source": "polymarket", "direction": None, "resolution_date": None, "forecast": 1.0},
        {"id": "d1", "source": "fred", "direction": None, "resolution_date": "2026-01-01", "forecast": 0.0},
    ]}
    result = score_submission(submission, RESOLUTIONS)
    # m1 correct (0.0); m2 uncovered -> imputed 0.5 -> 0.25; d1 wrong -> 1.0
    assert result["n_market"] == 2
    assert result["n_dataset"] == 1
    assert result["market"] == 0.125
    assert result["dataset"] == 1.0
    assert result["missing"] == 1
    assert result["overall"] == (0.125 + 1.0) / 2
    assert result["pooled_brier"] == (0.0 + 0.25 + 1.0) / 3
    assert result["coverage_resolved"] == {"market": 0.5, "dataset": 1.0}


def test_current_format_keeps_source_identity_and_market_date_semantics():
    submission = {"forecasts": [
        {"id": "42", "source": "kalshi", "resolution_date": None, "forecast": 1.0},
        {"id": "42", "source": "metaculus", "resolution_date": None, "forecast": 0.0},
    ]}
    rows = [
        {"id": "42", "source": "kalshi", "resolution_date": "2026-09-20", "resolved": True, "resolved_to": 1},
        {"id": "42", "source": "metaculus", "resolution_date": "2026-09-21", "resolved": True, "resolved_to": 0},
    ]
    result = score_submission(submission, rows)
    assert result["market"] == 0
    assert result["missing"] == 0
    assert result["n_market"] == 2
    assert result["overall"] is None  # No dataset category, not an overall benchmark score.
    with pytest.raises(ValueError, match="source-colliding"):
        brier({"42": 1}, rows)


def test_scorer_rejects_duplicate_and_invalid_probability_records():
    row = {"id": "x", "source": "kalshi", "resolution_date": None, "forecast": 0.4}
    resolution = {**row, "resolved": True, "resolved_to": 1}
    with pytest.raises(ValueError, match="duplicate forecast"):
        score_submission({"forecasts": [row, row]}, [resolution])
    with pytest.raises(ValueError, match="duplicate resolved"):
        score_submission({"forecasts": [row]}, [resolution, resolution])
    for invalid in (float("nan"), True, -0.1, 1.1):
        with pytest.raises(ValueError, match="finite number"):
            score_submission({"forecasts": [{**row, "forecast": invalid}]}, [resolution])
    with pytest.raises(ValueError, match="binary outcome"):
        score_submission({"forecasts": [row]}, [{**resolution, "resolved_to": 0.3}])
    with pytest.raises(ValueError, match="unknown forecast source"):
        score_submission({"forecasts": [{**row, "source": "typo"}]}, [])


def test_panel_scoring_does_not_join_different_platforms_or_clip_certainty(tmp_path):
    import json
    from forecast_stack.eval.harness import load_panel
    questions = [
        {"id": "42", "source": s, "freeze_datetime_value": p, "question": s}
        for s, p in [("kalshi", 0.0), ("metaculus", 1.0)]
    ]
    forecasts = [{**q, "forecast": q["freeze_datetime_value"], "resolution_date": None} for q in questions]
    resolutions = [{**q, "resolved": True, "resolved_to": q["freeze_datetime_value"],
                    "resolution_date": "2026-09-21"} for q in questions]
    for prefix, doc in [("q", {"questions": questions}), ("r", {"resolutions": resolutions}),
                        ("submission", {"forecasts": forecasts})]:
        (tmp_path / f"{prefix}_2026-09-01.json").write_text(json.dumps(doc))
    panel = load_panel(tmp_path)
    assert len(panel) == 2
    assert sum((r.mechanical - r.outcome) ** 2 for r in panel) == 0
    assert sum((r.prior - r.outcome) ** 2 for r in panel) == 0


def test_calibration_bins_partition_predictions_including_probability_one():
    from forecast_stack.eval.harness import _ece
    assert _ece([0.1, 0.9], [0, 1]) == pytest.approx(0.1)
    assert _ece([0.0, 1.0], [0, 1]) == 0


def test_cluster_bootstrap_has_uncertainty_and_matches_equal_category_metric():
    from forecast_stack.eval.harness import PanelRow, Fit, bootstrap_delta
    fit = Fit({}, {}, 1, (1, 0), {}, [], [])
    rows = [
        PanelRow(str(i), "2026-01-01", "metaculus", "market", str(i),
                 "2026-01-02", 1, 0.5, p, False, "synthetic")
        for i, p in enumerate([0.1, 0.9])
    ]
    result = bootstrap_delta(rows, fit, "mechanical", "uniform", iters=1000)
    assert result["mean"] == pytest.approx(-0.16)
    assert result["lo95"] < 0 < result["hi95"]
    assert result["clusters"] == 2
    # Duplicate horizons of one series are not independent events.
    rows += [PanelRow(f"d{i}", "2026-01-01", "fred", "dataset", "series",
                      f"2026-02-{i+1:02d}", 1, 0.5, 1, False, "synthetic")
             for i in range(10)]
    result = bootstrap_delta(rows, fit, "mechanical", "uniform", iters=1000)
    assert result["mean"] == pytest.approx((-0.16 + 0.25) / 2)
    assert result["clusters"] == 3


def test_temporal_split_excludes_labels_not_available_before_test_issue():
    from forecast_stack.eval.harness import PanelRow, chronological_split
    rows = [
        PanelRow("eligible", "2026-01-01", "fred", "dataset", "A", "2026-01-10", 1, .5, .6, False, "A"),
        PanelRow("future-label", "2026-01-01", "fred", "dataset", "B", "2026-03-01", 1, .5, .6, False, "B"),
        PanelRow("test", "2026-02-01", "fred", "dataset", "C", "2026-02-10", 0, .5, .6, False, "C"),
    ]
    train, held = chronological_split(rows, split_date="2026-02-01")
    assert [r.key for r in train] == ["eligible"]
    assert [r.key for r in held] == ["test"]


def test_method_selection_cannot_change_when_only_test_outcomes_change(tmp_path):
    import json
    from forecast_stack.eval.harness import run
    for due, resolution in [("2026-01-01", "2026-01-10"), ("2026-02-01", "2026-02-10")]:
        questions = [{"id": str(i), "source": "fred", "question": str(i)} for i in range(6)]
        forecasts = [{**q, "resolution_date": resolution, "forecast": .8 if i % 2 else .2}
                     for i, q in enumerate(questions)]
        resolved = [{**q, "resolution_date": resolution, "resolved": True, "resolved_to": i % 2}
                    for i, q in enumerate(questions)]
        for prefix, doc in [("q", {"questions": questions}), ("r", {"resolutions": resolved}),
                            ("submission", {"forecasts": forecasts})]:
            (tmp_path / f"{prefix}_{due}.json").write_text(json.dumps(doc))
    first = run(data_dir=tmp_path, test_rounds=1, bootstrap_iters=100)
    path = tmp_path / "r_2026-02-01.json"
    outcomes = json.loads(path.read_text())
    for row in outcomes["resolutions"]:
        row["resolved_to"] = 1 - row["resolved_to"]
    path.write_text(json.dumps(outcomes))
    second = run(data_dir=tmp_path, test_rounds=1, bootstrap_iters=100)
    assert first["selected_method"] == second["selected_method"]
    assert first["scores"][first["selected_method"]]["brier"] != second["scores"][second["selected_method"]]["brier"]
    assert first["promotion_eligible"] is False
