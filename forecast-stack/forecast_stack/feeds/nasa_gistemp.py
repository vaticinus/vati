"""NASA GISTEMP global temperature-anomaly collector.

Official NASA GISS Surface Temperature Analysis tables. The source publishes monthly and annual
land-ocean temperature anomalies in degrees Celsius relative to 1951-1980. This collector lands the
global, Northern Hemisphere, and Southern Hemisphere tables as point-in-time climate-state series.
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
BASE = "https://data.giss.nasa.gov/gistemp/tabledata_v4"
OUT_PATH = DATA_DIR / "feeds" / "nasa_gistemp.jsonl"
REQUEST_TIMEOUT_S = 30
MIN_REFRESH_FRACTION = 0.8

REGIONS: tuple[dict[str, str], ...] = (
    {"slug": "global", "title": "Global", "url": f"{BASE}/GLB.Ts+dSST.csv"},
    {"slug": "northern_hemisphere", "title": "Northern Hemisphere", "url": f"{BASE}/NH.Ts+dSST.csv"},
    {"slug": "southern_hemisphere", "title": "Southern Hemisphere", "url": f"{BASE}/SH.Ts+dSST.csv"},
)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


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
    s = str(raw or "").strip()
    if not s or "*" in s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_region(region: dict[str, str], text: str) -> list[dict[str, Any]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header_idx = next((i for i, line in enumerate(lines) if line.startswith("Year,")), None)
    if header_idx is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    rows: list[dict[str, Any]] = []
    for rec in reader:
        year_s = str(rec.get("Year") or "").strip()
        if len(year_s) != 4 or not year_s.isdigit():
            continue
        year = int(year_s)
        for month_num, month_name in enumerate(MONTHS, start=1):
            value = _to_float(rec.get(month_name))
            if value is None:
                continue
            day = monthrange(year, month_num)[1]
            rows.append({
                "series_id": f"nasa_gistemp:{region['slug']}:monthly_anomaly",
                "date": date(year, month_num, day).isoformat(),
                "value": value,
                "unit": "degC anomaly vs 1951-1980",
                "metric": "temperature_anomaly_monthly",
                "title": f"NASA GISTEMP — {region['title']} monthly temperature anomaly",
                "region": region["title"],
            })
        annual = _to_float(rec.get("J-D"))
        if annual is not None:
            rows.append({
                "series_id": f"nasa_gistemp:{region['slug']}:annual_anomaly",
                "date": date(year, 12, 31).isoformat(),
                "value": annual,
                "unit": "degC anomaly vs 1951-1980",
                "metric": "temperature_anomaly_annual",
                "title": f"NASA GISTEMP — {region['title']} annual temperature anomaly",
                "region": region["title"],
            })
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for region in REGIONS:
        try:
            text = _fetch_text(region["url"])
        except Exception as exc:  # noqa: BLE001 — public endpoint; skip rather than fabricate
            log(f"  - {region['slug']}: fetch failed: {exc}")
            continue
        region_rows = normalize_region(region, text)
        if region_rows:
            dates = sorted({str(r["date"]) for r in region_rows})
            log(f"  + {region['slug']:<20s} {len(region_rows):5d} obs  {dates[0]}–{dates[-1]}")
            rows.extend(region_rows)
        else:
            log(f"  - {region['slug']}: no parseable rows")

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
    print("NASA GISTEMP temperature anomalies (official public CSV):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — NASA GISTEMP CSV unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
