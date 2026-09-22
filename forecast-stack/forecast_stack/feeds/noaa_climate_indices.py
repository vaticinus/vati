"""NOAA PSL broad climate-regime index collector.

Official NOAA Physical Sciences Laboratory monthly climate-index text files for large-scale
ocean-atmosphere circulation regimes: PDO, NAO, AO, PNA, and West Pacific. These complement ENSO
with additional physical-state context for weather, crops, energy demand, and hazards.
"""

from __future__ import annotations

import json
import urllib.request
from calendar import monthrange
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
BASE = "https://psl.noaa.gov/data/correlation"
OUT_PATH = DATA_DIR / "feeds" / "noaa_climate_indices.jsonl"
REQUEST_TIMEOUT_S = 30
MIN_REFRESH_FRACTION = 0.8

DATASETS: tuple[dict[str, str], ...] = (
    {"slug": "pdo", "title": "Pacific Decadal Oscillation", "url": f"{BASE}/pdo.data"},
    {"slug": "nao", "title": "North Atlantic Oscillation", "url": f"{BASE}/nao.data"},
    {"slug": "ao", "title": "Arctic Oscillation", "url": f"{BASE}/ao.data"},
    {"slug": "pna", "title": "Pacific North American Pattern", "url": f"{BASE}/pna.data"},
    {"slug": "wp", "title": "West Pacific Pattern", "url": f"{BASE}/wp.data"},
)


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official data file
        return resp.read().decode("utf-8-sig", "replace")


def _to_float(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= -9.0:
        return None
    return value


def normalize_dataset(spec: dict[str, str], text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 13 or len(parts[0]) != 4 or not parts[0].isdigit():
            continue
        year = int(parts[0])
        for month, raw in enumerate(parts[1:], start=1):
            value = _to_float(raw)
            if value is None:
                continue
            rows.append({
                "series_id": f"noaa_climate_indices:{spec['slug']}",
                "date": date(year, month, monthrange(year, month)[1]).isoformat(),
                "value": value,
                "unit": "index",
                "metric": "climate_regime_index",
                "title": f"NOAA PSL Climate Indices — {spec['title']}",
            })
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in DATASETS:
        try:
            text = _fetch_text(spec["url"])
        except Exception as exc:  # noqa: BLE001 — public endpoint; skip rather than fabricate
            log(f"  - {spec['slug']}: fetch failed: {exc}")
            continue
        ds_rows = normalize_dataset(spec, text)
        if ds_rows:
            dates = sorted({str(r["date"]) for r in ds_rows})
            log(f"  + {spec['slug']:<8s} {len(ds_rows):5d} obs  {dates[0]}–{dates[-1]}")
            rows.extend(ds_rows)
        else:
            log(f"  - {spec['slug']}: no parseable rows")

    existing = _existing_line_count()
    if not rows:
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial refresh fetched {len(rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"]))))
    log(f"\nwrote {len(rows)} observations → {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("NOAA PSL broad climate indices (official public text files):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — NOAA PSL climate-index files unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
