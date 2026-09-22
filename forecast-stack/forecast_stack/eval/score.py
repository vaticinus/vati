"""Scoring harness + baselines — the measurement backbone.

Raw Brier on a fixed resolved cohort is an internal diagnostic, not the official
ForecastBench leaderboard. The latter adjusts for question difficulty and gives
market and dataset categories equal weight before transforming to Brier Index.
Never tune LLM judgment on outcomes its checkpoint could know. Historical
replays require point-in-time inputs; promotion requires untouched evidence.

Data layout (gitignored, downloaded from forecastingresearch/forecastbench-datasets):
  data/forecastbench/q_<date>.json   question set   {forecast_due_date, questions:[...]}
  data/forecastbench/r_<date>.json   resolution set {resolutions:[{id,source,resolution_date,resolved_to,resolved}]}

A forecast is a dict keyed by (id, resolution_date) -> p in [0,1].
  - market questions: one entry, resolution_date = the row's date (forecaster sends null,
    but a round has exactly one market row per id, so we bind by id).
  - dataset questions: one entry per resolution_date horizon.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from forecast_stack.config import DATA_DIR

DATA = DATA_DIR / "forecastbench"
MARKET_SOURCES = {"kalshi", "manifold", "metaculus", "polymarket", "infer"}
DATASET_SOURCES = {"acled", "dbnomics", "fred", "wikipedia", "yfinance"}


def _key(qid):
    return tuple(qid) if isinstance(qid, list) else qid


def _probability(value, name="forecast"):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError(f"{name} must be a finite number in [0,1]")
    return value


def _group(source):
    if source in MARKET_SOURCES:
        return "market"
    if source in DATASET_SOURCES:
        return "dataset"
    raise ValueError(f"unknown forecast source: {source}")


def _unambiguous_ids(rows):
    sources = {}
    for row in rows:
        key = _key(row["id"])
        if key in sources and sources[key] != row["source"]:
            raise ValueError("source-colliding ids require source-aware score_submission")
        sources[key] = row["source"]


def load_round(date: str, data_dir: Path = DATA):
    """Return (questions, resolutions) for a round date 'YYYY-MM-DD'."""
    q = json.loads((data_dir / f"q_{date}.json").read_text())
    r = json.loads((data_dir / f"r_{date}.json").read_text())
    return q["questions"], r["resolutions"]


def single_questions(questions):
    """Drop combo questions (id is a list) — we forecast singles first."""
    return [q for q in questions if not isinstance(q["id"], list)]


def resolved_rows(resolutions, sources=None, singles_only=True):
    """Resolved resolution rows, optionally filtered to a source set."""
    out = []
    for x in resolutions:
        if not x.get("resolved"):
            continue
        if singles_only and isinstance(x["id"], list):
            continue
        if sources is not None and x["source"] not in sources:
            continue
        out.append(x)
    return out


def brier(forecast: dict, rows) -> dict:
    """Score a forecast {(id,resdate)->p or id->p for market} on resolved rows.

    Market rows are looked up by id alone (one row per id); dataset rows by
    (id, resolution_date). Missing forecasts are imputed 0.5 (FB rule) and
    counted, so coverage gaps are penalized honestly.
    """
    rows = list(rows)
    _unambiguous_ids(rows)
    se, n, missing = 0.0, 0, 0
    by_src = {}
    for x in rows:
        qid, src, rd, y = _key(x["id"]), x["source"], x["resolution_date"], x["resolved_to"]
        _group(src)
        _probability(y, "outcome")
        if y not in (0, 1):
            raise ValueError("resolved binary outcome must be 0 or 1")
        if src in MARKET_SOURCES:
            p = forecast.get(qid, forecast.get((qid, None), forecast.get((qid, rd))))
        else:
            p = forecast.get((qid, rd), forecast.get(qid))
        if p is None:
            p = 0.5
            missing += 1
        _probability(p)
        d = (p - y) ** 2
        se += d
        n += 1
        b = by_src.setdefault(src, [0.0, 0])
        b[0] += d
        b[1] += 1
    out = {"brier": se / n if n else None, "n": n, "missing": missing,
           "by_source": {s: v[0] / v[1] for s, v in sorted(by_src.items())},
           "n_by_source": {s: v[1] for s, v in sorted(by_src.items())}}
    return out


def score_submission(submission: dict, resolutions) -> dict:
    """Source-aware raw scores, NOT difficulty-adjusted leaderboard scores.

    Current submissions need no direction field; legacy combination directions
    remain part of their event identity. Market settlement dates are ignored.
    Missing predictions receive the declared ForecastBench 0.5 imputation.
    """
    def identity(row):
        source = row["source"]
        group = _group(source)
        return (source, _key(row["id"]),
                None if group == "market" else row["resolution_date"],
                _key(row.get("direction")))

    forecasts = {}
    for row in submission["forecasts"]:
        key = identity(row)
        if key in forecasts:
            raise ValueError(f"duplicate forecast identity: {key}")
        forecasts[key] = _probability(row["forecast"])
    agg = {"market": [0.0, 0, 0], "dataset": [0.0, 0, 0]}
    seen = set()
    for row in resolutions:
        if not row.get("resolved"):
            continue
        key = identity(row)
        if key in seen:
            raise ValueError(f"duplicate resolved identity: {key}")
        seen.add(key)
        y = _probability(row["resolved_to"], "outcome")
        if y not in (0, 1):
            raise ValueError("resolved binary outcome must be 0 or 1")
        missing = key not in forecasts
        p = forecasts.get(key, 0.5)
        values = agg[_group(row["source"])]
        values[0] += (p - y) ** 2
        values[1] += 1
        values[2] += int(missing)
    means = {g: a[0] / a[1] if a[1] else None for g, a in agg.items()}
    n = sum(a[1] for a in agg.values())
    return {
        "overall": (means["market"] + means["dataset"]) / 2
        if all(v is not None for v in means.values()) else None,
        "pooled_brier": sum(a[0] for a in agg.values()) / n if n else None,
        "metric": "raw Brier; overall is equal-category-weighted, not difficulty-adjusted",
        "n": n, "missing": sum(a[2] for a in agg.values()),
        **means,
        "n_market": agg["market"][1], "n_dataset": agg["dataset"][1],
        "coverage_resolved": {g: (a[1] - a[2]) / a[1] if a[1] else None for g, a in agg.items()},
    }


# ---- baselines -------------------------------------------------------------

def crowd_forecast(questions) -> dict:
    """Market: the crowd value (freeze_datetime_value). Dataset: 0.5 (no info)."""
    questions = list(questions)
    _unambiguous_ids(single_questions(questions))
    f = {}
    for q in single_questions(questions):
        if q["source"] in MARKET_SOURCES:
            try:
                f[q["id"]] = _probability(float(q.get("freeze_datetime_value")))
            except (TypeError, ValueError):
                pass
    return f


def uniform_forecast(questions, p=0.5) -> dict:
    questions = list(questions)
    _unambiguous_ids(single_questions(questions))
    _probability(p)
    return {q["id"]: p for q in single_questions(questions)}


if __name__ == "__main__":
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 else "2025-08-03"
    questions, resolutions = load_round(date)
    rows = resolved_rows(resolutions)
    print(f"round {date}: {len(rows)} resolved single rows")
    for name, f in [("uniform-0.5", uniform_forecast(questions)),
                    ("crowd", crowd_forecast(questions))]:
        s = brier(f, rows)
        print(f"\n[{name}] Brier={s['brier']:.4f}  n={s['n']}  missing={s['missing']}")
        for src in sorted(s["by_source"]):
            print(f"    {src:10s} {s['by_source'][src]:.4f}  (n={s['n_by_source'][src]})")
