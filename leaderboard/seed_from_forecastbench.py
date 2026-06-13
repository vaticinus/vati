"""Seed the public board with reference points from public ForecastBench data.

Adds the superforecaster-median and public-median ensembles (market track) as the
human anchors everyone else is measured against. Run once after `python -m data.fetch`:

    python -m leaderboard.seed_from_forecastbench
"""
from __future__ import annotations

import os
import tempfile

import pandas as pd

from beyond_brier.forecastbench import load_round
from leaderboard.submit import add as board_add


class _A:  # tiny args shim for submit.add
    def __init__(self, name, forecasts, kind, track, score="brier", date="2024-07-21", notes=""):
        self.name, self.forecasts, self.kind = name, forecasts, kind
        self.track, self.score, self.date, self.notes = track, score, date, notes


def _median_ensemble(df, tag, track="market"):
    sub = df[(df.tag == tag) & (df.track == track)]
    med = sub.groupby("question").agg(
        p_f=("p_f", "median"), p_ref=("p_ref", "first"), y=("y", "first")).reset_index()
    return med


def main():
    df = load_round()
    for tag, name in [("super", "Superforecaster median (ForecastBench 2024-07-21)"),
                      ("public", "Public median (ForecastBench 2024-07-21)")]:
        med = _median_ensemble(df, tag)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            med.to_csv(f.name, index=False)
            path = f.name
        board_add(_A(name=name, forecasts=path, kind="human", track="market"))
        os.unlink(path)


if __name__ == "__main__":
    main()
