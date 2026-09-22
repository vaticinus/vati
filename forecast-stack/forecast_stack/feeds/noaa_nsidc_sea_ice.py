"""NOAA/NSIDC Sea Ice Index collector.

Official NOAA@NSIDC Sea Ice Index v4 monthly CSV files. NSIDC publishes one file per hemisphere
and calendar month; each row contains sea-ice extent and area in million square kilometers. This
collector loops all months for both hemispheres, drops `-9999` missing sentinels, and emits
timestamped monthly physical-state series.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from calendar import monthrange
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
BASE = "https://noaadata.apps.nsidc.org/NOAA/G02135"
OUT_PATH = DATA_DIR / "feeds" / "noaa_nsidc_sea_ice.jsonl"
REQUEST_TIMEOUT_S = 30
MIN_REFRESH_FRACTION = 0.8

HEMISPHERES: tuple[dict[str, str], ...] = (
    {"slug": "arctic", "title": "Arctic", "path": "north", "prefix": "N"},
    {"slug": "antarctic", "title": "Antarctic", "path": "south", "prefix": "S"},
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
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official data file
        return resp.read().decode("utf-8-sig", "replace")


def _to_float(raw: Any) -> float | None:
    try:
        value = float(str(raw or "").strip())
    except ValueError:
        return None
    if value <= -999.0:
        return None
    return value


def file_url(hemisphere: dict[str, str], month: int) -> str:
    return f"{BASE}/{hemisphere['path']}/monthly/data/{hemisphere['prefix']}_{month:02d}_extent_v4.0.csv"


def normalize_month_file(hemisphere: dict[str, str], text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text), skipinitialspace=True)
    rows: list[dict[str, Any]] = []
    for rec in reader:
        try:
            year = int(str(rec.get("year") or "").strip())
            month = int(str(rec.get("mo") or "").strip())
        except ValueError:
            continue
        if not 1 <= month <= 12:
            continue
        as_of = date(year, month, monthrange(year, month)[1]).isoformat()
        for field, metric, label in (
            ("extent", "sea_ice_extent", "extent"),
            ("area", "sea_ice_area", "area"),
        ):
            value = _to_float(rec.get(field))
            if value is None:
                continue
            rows.append({
                "series_id": f"noaa_nsidc_sea_ice:{hemisphere['slug']}:{metric}",
                "date": as_of,
                "value": value,
                "unit": "million square kilometers",
                "metric": metric,
                "title": f"NOAA/NSIDC Sea Ice Index — {hemisphere['title']} sea ice {label}",
                "hemisphere": hemisphere["title"],
                "source_dataset": str(rec.get("source_dataset") or "").strip(),
            })
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hemi in HEMISPHERES:
        hemi_rows: list[dict[str, Any]] = []
        for month in range(1, 13):
            url = file_url(hemi, month)
            try:
                text = _fetch_text(url)
            except Exception as exc:  # noqa: BLE001 — public endpoint; skip rather than fabricate
                log(f"  - {hemi['slug']} {month:02d}: fetch failed: {exc}")
                continue
            hemi_rows.extend(normalize_month_file(hemi, text))
        if hemi_rows:
            dates = sorted({str(r["date"]) for r in hemi_rows})
            series = len({str(r["series_id"]) for r in hemi_rows})
            log(f"  + {hemi['slug']:<10s} {len(hemi_rows):5d} obs across {series} series  {dates[0]}–{dates[-1]}")
            rows.extend(hemi_rows)
        else:
            log(f"  - {hemi['slug']}: no parseable rows")

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
    print("NOAA/NSIDC Sea Ice Index (official public CSV files):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — NOAA/NSIDC sea-ice files unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
