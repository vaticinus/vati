"""NOAA PSL ENSO climate-index collector.

Official NOAA Physical Sciences Laboratory monthly climate-index text files. This lands the
Oceanic Nino Index, Nino 3.4 sea-surface temperature, and Southern Oscillation Index as
timestamped ocean-atmosphere state series.
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
OUT_PATH = DATA_DIR / "feeds" / "noaa_enso.jsonl"
REQUEST_TIMEOUT_S = 30
MIN_REFRESH_FRACTION = 0.8

DATASETS: tuple[dict[str, Any], ...] = (
    {
        "slug": "oni",
        "title": "Oceanic Nino Index",
        "url": f"{BASE}/oni.data",
        "unit": "degC anomaly",
        "metric": "enso_oni",
    },
    {
        "slug": "nino34_sst",
        "title": "Nino 3.4 sea-surface temperature",
        "url": f"{BASE}/nina34.data",
        "unit": "degC",
        "metric": "enso_nino34_sst",
    },
    {
        "slug": "soi",
        "title": "Southern Oscillation Index",
        "url": f"{BASE}/soi.data",
        "unit": "index",
        "metric": "enso_soi",
    },
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
    if value <= -90.0:
        return None
    return value


def normalize_dataset(spec: dict[str, Any], text: str) -> list[dict[str, Any]]:
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
                "series_id": f"noaa_enso:{spec['slug']}",
                "date": date(year, month, monthrange(year, month)[1]).isoformat(),
                "value": value,
                "unit": spec["unit"],
                "metric": spec["metric"],
                "title": f"NOAA PSL ENSO — {spec['title']}",
            })
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in DATASETS:
        try:
            text = _fetch_text(str(spec["url"]))
        except Exception as exc:  # noqa: BLE001 — public endpoint; skip rather than fabricate
            log(f"  - {spec['slug']}: fetch failed: {exc}")
            continue
        ds_rows = normalize_dataset(spec, text)
        if ds_rows:
            dates = sorted({str(r["date"]) for r in ds_rows})
            log(f"  + {spec['slug']:<12s} {len(ds_rows):5d} obs  {dates[0]}–{dates[-1]}")
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
    print("NOAA PSL ENSO indices (official public text files):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — NOAA PSL ENSO files unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
