"""NOAA GML atmospheric greenhouse-gas trends collector.

Official NOAA Global Monitoring Laboratory public trend files for atmospheric CO2, CH4, and N2O.
The collector emits monthly mean concentrations plus trend/seasonally-adjusted concentrations for
global marine-surface means and Mauna Loa CO2.
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
OUT_PATH = DATA_DIR / "feeds" / "noaa_gml_greenhouse_gases.jsonl"
REQUEST_TIMEOUT_S = 30
MIN_REFRESH_FRACTION = 0.8

DATASETS: tuple[dict[str, Any], ...] = (
    {
        "slug": "co2_global",
        "gas": "CO2",
        "region": "Global",
        "unit": "ppm",
        "url": "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_gl.txt",
        "mean_idx": 3,
        "trend_idx": 5,
        "trend_label": "trend",
    },
    {
        "slug": "co2_mauna_loa",
        "gas": "CO2",
        "region": "Mauna Loa",
        "unit": "ppm",
        "url": "https://gml.noaa.gov/webdata/ccgg/trends/co2/co2_mm_mlo.txt",
        "mean_idx": 3,
        "trend_idx": 4,
        "trend_label": "seasonally adjusted",
    },
    {
        "slug": "ch4_global",
        "gas": "CH4",
        "region": "Global",
        "unit": "ppb",
        "url": "https://gml.noaa.gov/webdata/ccgg/trends/ch4/ch4_mm_gl.txt",
        "mean_idx": 3,
        "trend_idx": 5,
        "trend_label": "trend",
    },
    {
        "slug": "n2o_global",
        "gas": "N2O",
        "region": "Global",
        "unit": "ppb",
        "url": "https://gml.noaa.gov/webdata/ccgg/trends/n2o/n2o_mm_gl.txt",
        "mean_idx": 3,
        "trend_idx": 5,
        "trend_label": "trend",
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


def _to_float(raw: str | None) -> float | None:
    try:
        value = float(str(raw or "").strip())
    except ValueError:
        return None
    if value <= -9.0:
        return None
    return value


def normalize_dataset(spec: dict[str, Any], text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) <= max(int(spec["mean_idx"]), int(spec["trend_idx"])):
            continue
        year_s, month_s = parts[0], parts[1]
        if len(year_s) != 4 or not year_s.isdigit() or not month_s.isdigit():
            continue
        year = int(year_s)
        month = int(month_s)
        if not 1 <= month <= 12:
            continue
        as_of = date(year, month, monthrange(year, month)[1]).isoformat()
        mean = _to_float(parts[int(spec["mean_idx"])])
        trend = _to_float(parts[int(spec["trend_idx"])])
        base = f"NOAA GML — {spec['region']} {spec['gas']}"
        if mean is not None:
            rows.append({
                "series_id": f"noaa_gml:{spec['slug']}:monthly_mean",
                "date": as_of,
                "value": mean,
                "unit": spec["unit"],
                "metric": "greenhouse_gas_monthly_mean",
                "title": f"{base} monthly mean concentration",
                "gas": spec["gas"],
                "region": spec["region"],
            })
        if trend is not None:
            rows.append({
                "series_id": f"noaa_gml:{spec['slug']}:trend",
                "date": as_of,
                "value": trend,
                "unit": spec["unit"],
                "metric": "greenhouse_gas_trend",
                "title": f"{base} {spec['trend_label']} concentration",
                "gas": spec["gas"],
                "region": spec["region"],
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
            log(f"  + {spec['slug']:<16s} {len(ds_rows):5d} obs  {dates[0]}–{dates[-1]}")
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
    print("NOAA GML greenhouse-gas trends (official public text files):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — NOAA GML files unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
