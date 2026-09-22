"""Epoch AI notable-model training compute feed.

Keyless public CSV, emitted through the generic feed/rawstore path so the
existing provider="epoch_ai" frontier-compute series have exact feed-byte
provenance.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
EPOCH_CSV = "https://epoch.ai/data/notable_ai_models.csv"
OUT_PATH = DATA_DIR / "feeds" / "epoch_ai.jsonl"
WINDOW_START = 2010
CUTOFF_YEAR = 2025
DOMAINS: tuple[str, ...] = ("Language", "Vision", "Image generation", "Speech", "Games")


def _fetch_csv(*, timeout: int = 45) -> str:
    req = urllib.request.Request(EPOCH_CSV, headers={"User-Agent": UA, "Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 public CSV
        return resp.read().decode("utf-8-sig", "replace")


def _year(raw: Any) -> int | None:
    text = str(raw or "").strip()
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _flop(raw: Any) -> float | None:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize(text: str) -> list[dict[str, Any]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    out: list[dict[str, Any]] = []
    for domain in DOMAINS:
        by_year: dict[int, float] = {}
        for row in rows:
            domains = [d.strip() for d in str(row.get("Domain") or "").split(",")]
            if domain not in domains:
                continue
            year = _year(row.get("Publication date"))
            flop = _flop(row.get("Training compute (FLOP)"))
            if year is None or flop is None or not (WINDOW_START <= year <= CUTOFF_YEAR):
                continue
            if flop > by_year.get(year, 0.0):
                by_year[year] = flop
        if len(by_year) < 8:
            continue
        for year, value in sorted(by_year.items()):
            day = date(year, 12, 31).isoformat()
            out.append({
                "series_id": domain,
                "date": day,
                "event_time": day,
                "observed_at": day,
                "published_at": day,
                "value": float(value),
                "unit": "FLOP",
                "uncertainty": 0.5 * float(value),
                "metric": "frontier_training_compute",
                "domain": "AI",
                "title": f"Frontier training compute ({domain})",
            })
    return sorted(out, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    rows = normalize(_fetch_csv())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)
    by_domain: dict[str, int] = {}
    for row in rows:
        by_domain[str(row["series_id"])] = by_domain.get(str(row["series_id"]), 0) + 1
    for domain, count in sorted(by_domain.items()):
        log(f"  + {domain:<18s} {count:2d} frontier compute observations")
    log(f"\nwrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("Epoch AI frontier training compute (keyless CSV):")
    observations = collect()
    print(f"\njsonl rows: {len(observations)}")
