"""Command line entry point: `beyond-brier <command>`.

Commands
--------
score      Score a CSV of (forecaster, question, p_f, p_ref, y) -> ranked board.
reproduce  Recompute the paper's ForecastBench market/data tracks.
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd


def _score(args):
    from .leaderboard import build_leaderboard, reorder_stats
    df = pd.read_csv(args.csv)
    board = build_leaderboard(df, score=args.score, min_n=args.min_n)
    if board.empty:
        print("No forecaster met the min_n threshold.", file=sys.stderr)
        return 1
    cols = ["forecaster", "n", "mean_brier", "edge", "edge_lo", "edge_hi", "alpha",
            "brier_rank", "edge_rank", "rank_change", "edge_positive_fdr"]
    print(board[[c for c in cols if c in board.columns]].to_string(index=False))
    print("\n" + repr(reorder_stats(board)))
    if args.out:
        board.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")
    return 0


def _reproduce(args):
    from .forecastbench import load_round
    from .leaderboard import build_leaderboard, reorder_stats
    df = load_round(args.data_dir) if args.data_dir else load_round()
    for track in ["market", "data"]:
        sub = df[df.track == track]
        min_n = 20 if track == "market" else 30
        board = build_leaderboard(sub, score=args.score, min_n=min_n,
                                  difficulty_adjust=(track == "market"))
        print(f"\n=== {track.upper()} track: {len(board)} forecasters (min_n={min_n}) ===")
        if not board.empty:
            print(repr(reorder_stats(board)))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="beyond-brier")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="rank a CSV of forecasts by marginal edge")
    s.add_argument("csv")
    s.add_argument("--score", default="brier", choices=["brier", "log"])
    s.add_argument("--min-n", dest="min_n", type=int, default=20)
    s.add_argument("--out")
    s.set_defaults(func=_score)

    r = sub.add_parser("reproduce", help="recompute the paper's ForecastBench tracks")
    r.add_argument("--data-dir", default=None)
    r.add_argument("--score", default="brier", choices=["brier", "log"])
    r.set_defaults(func=_reproduce)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
