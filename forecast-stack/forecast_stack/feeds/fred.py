"""FRED physical-supply bottleneck indicators.

Small keyless feed for the existing provider="fred" power/metals series that were
originally landed by DB-direct pillar collectors. This emits the same series keys
so a feed refresh updates those series to raw-preserved source provenance instead
of creating duplicate provider names.
"""

from __future__ import annotations

import csv
import io
import json
import math
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
OUT_PATH = DATA_DIR / "feeds" / "fred.jsonl"
WINDOW_START = 2005
CUTOFF_YEAR = 2025

SERIES: tuple[dict[str, str], ...] = (
    {
        "id": "PCU335311335311",
        "title": "Large-power transformer PPI",
        "metric": "transformer_ppi",
        "unit": "index (1982=100)",
        "domain": "energy/grid",
    },
    {
        "id": "PCU335313335313",
        "title": "HV switchgear PPI",
        "metric": "switchgear_ppi",
        "unit": "index (1982=100)",
        "domain": "energy/grid",
    },
    {
        "id": "PCU331420331420",
        "title": "Copper mill products PPI",
        "metric": "copper_ppi",
        "unit": "index",
        "domain": "energy/grid",
    },
    {
        "id": "WPU101",
        "title": "Iron & steel PPI (GOES proxy)",
        "metric": "steel_ppi",
        "unit": "index (1982=100)",
        "domain": "energy/grid",
    },
    {
        "id": "PCU335999335999",
        "title": "Other electrical equipment PPI",
        "metric": "electrical_equip_ppi",
        "unit": "index",
        "domain": "energy/grid",
    },
    {
        "id": "IPG21223S",
        "title": "Copper-base-metal mine output (US)",
        "metric": "copper_mine_output",
        "unit": "index (2017=100)",
        "domain": "metals / mining",
    },
    {
        "id": "IPG2122S",
        "title": "Metal-ore mine output (US)",
        "metric": "metal_ore_output",
        "unit": "index (2017=100)",
        "domain": "metals / mining",
    },
)


def _fetch_csv(series_id: str, *, timeout: int = 30) -> str:
    params = urllib.parse.urlencode({"id": series_id, "fq": "Annual", "fam": "avg"})
    req = urllib.request.Request(f"{FRED_CSV}?{params}", headers={"User-Agent": UA, "Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 official public CSV
        return resp.read().decode("utf-8-sig", "replace")


def _to_float(raw: Any) -> float | None:
    text = str(raw if raw is not None else "").strip()
    if not text or text == ".":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def normalize_series(spec: dict[str, str], text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    value_field = spec["id"]
    rows: list[dict[str, Any]] = []
    for rec in reader:
        raw_date = str(rec.get("observation_date") or "")[:10]
        if len(raw_date) < 4:
            continue
        try:
            year = int(raw_date[:4])
        except ValueError:
            continue
        if not (WINDOW_START <= year <= CUTOFF_YEAR):
            continue
        value = _to_float(rec.get(value_field))
        if value is None:
            continue
        rows.append({
            "series_id": spec["id"],
            "date": date(year, 12, 31).isoformat(),
            "value": value,
            "unit": spec["unit"],
            "metric": spec["metric"],
            "domain": spec["domain"],
            "title": spec["title"],
            "fred_series_id": spec["id"],
        })
    return sorted(rows, key=lambda r: str(r["date"]))


def _write_jsonl(rows: list[dict[str, Any]]) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in SERIES:
        try:
            text = _fetch_csv(spec["id"])
        except OSError as exc:
            log(f"  - {spec['id']}: fetch failed: {exc}")
            continue
        parsed = normalize_series(spec, text)
        if not parsed:
            log(f"  - {spec['id']}: no parseable annual observations")
            continue
        rows.extend(parsed)
        log(f"  + {spec['id']:<14s} {len(parsed):3d} obs  {parsed[0]['date']}-{parsed[-1]['date']}  {spec['title']}")
    if rows:
        _write_jsonl(sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"]))))
        log(f"\nwrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("FRED physical supply bottleneck indicators (keyless public annual CSV):")
    observations = collect()
    print(f"\njsonl rows: {len(observations)}")
