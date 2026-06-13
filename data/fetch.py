"""Fetch the public ForecastBench files needed to reproduce the paper.

The 2024-07-21 round is the one public, leak-free slice with raw per-question
*human* forecasts. We pull the four files from the official ForecastBench data
repository (or copy them from a local checkout).

Usage
-----
    python -m data.fetch                 # download from GitHub
    python -m data.fetch --local PATH    # copy from an existing checkout

The files are large (~30 MB total) and are gitignored; this script populates
``data/forecastbench/``.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "forecastbench")

# Official ForecastBench public datasets repository.
BASE = "https://raw.githubusercontent.com/forecastingresearch/forecastbench-datasets/main"
FILES = {
    "q.json": f"{BASE}/datasets/question_sets/2024-07-21-llm.json",
    "r.json": f"{BASE}/datasets/resolution_sets/2024-07-21_resolution_set.json",
    "human_super.json": f"{BASE}/datasets/forecast_sets/2024-07-21/2024-07-21.ForecastBench.human_super.json",
    "human_public.json": f"{BASE}/datasets/forecast_sets/2024-07-21/2024-07-21.ForecastBench.human_public.json",
    "leaderboard_dataset.csv": f"{BASE}/leaderboards/csv/leaderboard_overall.csv",
}

# NOTE: ForecastBench occasionally reorganizes paths. If a URL 404s, locate the
# 2024-07-21 question set, resolution set, and the two human forecast files in
# the current repo layout and pass --local, or edit FILES above.


def fetch_remote():
    os.makedirs(DEST, exist_ok=True)
    for name, url in FILES.items():
        out = os.path.join(DEST, name)
        if os.path.exists(out):
            print(f"  have {name}")
            continue
        print(f"  downloading {name} ...", flush=True)
        try:
            urllib.request.urlretrieve(url, out)
        except Exception as e:
            print(f"    FAILED ({e}).\n    Path may have moved; see the note in data/fetch.py "
                  f"or use --local.", file=sys.stderr)


def fetch_local(src):
    os.makedirs(DEST, exist_ok=True)
    # Try a few common names under the local checkout for each target file.
    candidates = {
        "q.json": ["q.json", "datasets/question_sets/2024-07-21-llm.json"],
        "r.json": ["r.json", "datasets/resolution_sets/2024-07-21_resolution_set.json"],
        "human_super.json": ["human_super.json",
                              "forecast_sets/2024-07-21/human_super.json",
                              "data/forecast_sets/2024-07-21/human_super.json"],
        "human_public.json": ["human_public.json",
                               "forecast_sets/2024-07-21/human_public.json",
                               "data/forecast_sets/2024-07-21/human_public.json"],
        "leaderboard_dataset.csv": ["leaderboard_dataset.csv", "data/leaderboard_dataset.csv"],
    }
    for name, rels in candidates.items():
        for rel in rels:
            p = os.path.join(src, rel)
            if os.path.exists(p):
                shutil.copy(p, os.path.join(DEST, name))
                print(f"  copied {name} <- {rel}")
                break
        else:
            print(f"  MISSING {name} under {src}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", help="copy from a local ForecastBench checkout instead of downloading")
    args = ap.parse_args()
    if args.local:
        fetch_local(args.local)
    else:
        fetch_remote()
    print(f"\nfiles in {DEST}:")
    for f in sorted(os.listdir(DEST)):
        print(f"  {f}")


if __name__ == "__main__":
    main()
