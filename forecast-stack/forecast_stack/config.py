"""Paths and runtime configuration.

Data lives under the working directory's ``data/`` by default. Override with
``FORECAST_STACK_DATA``. Never write into an installed package directory.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("FORECAST_STACK_DATA") or Path.cwd() / "data").expanduser().resolve()

FEED_DIR = DATA_DIR / "feeds"
FORECASTBENCH_DIR = DATA_DIR / "forecastbench"
LEDGER_DIR = DATA_DIR / "forecasting"


def data_path(*parts: str) -> Path:
    """A path under the data dir; parent directories are created on demand."""
    path = DATA_DIR.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_dirs() -> None:
    for path in (FEED_DIR, FORECASTBENCH_DIR, LEDGER_DIR):
        path.mkdir(parents=True, exist_ok=True)
