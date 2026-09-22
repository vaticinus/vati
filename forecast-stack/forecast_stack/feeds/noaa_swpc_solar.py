"""NOAA SWPC observed solar and space-weather collector.

This collector uses only observed NOAA Space Weather Prediction Center public JSON endpoints:
historical monthly solar-cycle indices, rolling planetary Kp, and GOES X-ray flux. Forecast
endpoints are deliberately excluded so future probability rows cannot leak into world-state facts.
High-frequency observations are compacted to daily aggregates before landing in the feed JSONL.
"""

from __future__ import annotations

import json
import math
import urllib.request
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from statistics import mean
from typing import Any, Iterable

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
BASE = "https://services.swpc.noaa.gov/json"
SOLAR_CYCLE_URL = f"{BASE}/solar-cycle/observed-solar-cycle-indices.json"
KP_URL = f"{BASE}/planetary_k_index_1m.json"
XRAY_URL = f"{BASE}/goes/primary/xrays-7-day.json"
OUT_PATH = DATA_DIR / "feeds" / "noaa_swpc_solar.jsonl"
REQUEST_TIMEOUT_S = 30
MIN_REFRESH_FRACTION = 0.8

SOLAR_CYCLE_FIELDS: tuple[dict[str, str], ...] = (
    {
        "field": "ssn",
        "slug": "ssn",
        "metric": "sunspot_number",
        "unit": "sunspot number",
        "title": "NOAA SWPC - solar-cycle sunspot number",
    },
    {
        "field": "smoothed_ssn",
        "slug": "smoothed_ssn",
        "metric": "sunspot_number_smoothed",
        "unit": "sunspot number",
        "title": "NOAA SWPC - solar-cycle smoothed sunspot number",
    },
    {
        "field": "observed_swpc_ssn",
        "slug": "observed_swpc_ssn",
        "metric": "sunspot_number_observed_swpc",
        "unit": "sunspot number",
        "title": "NOAA SWPC - SWPC observed sunspot number",
    },
    {
        "field": "smoothed_swpc_ssn",
        "slug": "smoothed_swpc_ssn",
        "metric": "sunspot_number_smoothed_swpc",
        "unit": "sunspot number",
        "title": "NOAA SWPC - SWPC smoothed sunspot number",
    },
    {
        "field": "f10.7",
        "slug": "f107",
        "metric": "solar_radio_flux_f107",
        "unit": "sfu",
        "title": "NOAA SWPC - F10.7 cm solar radio flux",
    },
    {
        "field": "smoothed_f10.7",
        "slug": "smoothed_f107",
        "metric": "solar_radio_flux_f107_smoothed",
        "unit": "sfu",
        "title": "NOAA SWPC - smoothed F10.7 cm solar radio flux",
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


def _fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official data endpoint
        return json.loads(resp.read().decode("utf-8", "replace"))


def _to_float(raw: Any) -> float | None:
    try:
        value = float(str(raw if raw is not None else "").strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _month_end(raw: Any) -> date | None:
    try:
        year_s, month_s = str(raw or "").strip().split("-", 1)
        year, month = int(year_s), int(month_s)
    except ValueError:
        return None
    if not 1 <= month <= 12:
        return None
    return date(year, month, monthrange(year, month)[1])


def _parse_dt(raw: Any) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _energy_slug(energy: str) -> str:
    return (
        energy.lower()
        .replace(" ", "")
        .replace(".", "_")
        .replace("-", "_")
        .replace("/", "_")
    )


def normalize_solar_cycle(records: Iterable[dict[str, Any]], *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    rows: list[dict[str, Any]] = []
    for rec in records:
        as_of = _month_end(rec.get("time-tag"))
        if as_of is None or as_of > today:
            continue
        for spec in SOLAR_CYCLE_FIELDS:
            value = _to_float(rec.get(spec["field"]))
            if value is None:
                continue
            rows.append({
                "series_id": f"noaa_swpc_solar:solar_cycle:{spec['slug']}",
                "date": as_of.isoformat(),
                "value": value,
                "unit": spec["unit"],
                "metric": spec["metric"],
                "title": spec["title"],
            })
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def normalize_kp(records: Iterable[dict[str, Any]], *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    by_day: dict[date, list[float]] = defaultdict(list)
    for rec in records:
        dt = _parse_dt(rec.get("time_tag"))
        value = _to_float(rec.get("estimated_kp"))
        if dt is None or value is None:
            continue
        day = dt.date()
        if day > today:
            continue
        by_day[day].append(value)

    rows: list[dict[str, Any]] = []
    for day, values in sorted(by_day.items()):
        rows.extend([
            {
                "series_id": "noaa_swpc_solar:geomagnetic:daily_max_estimated_kp",
                "date": day.isoformat(),
                "value": max(values),
                "unit": "Kp",
                "metric": "geomagnetic_kp",
                "title": "NOAA SWPC - geomagnetic daily max estimated Kp",
            },
            {
                "series_id": "noaa_swpc_solar:geomagnetic:daily_mean_estimated_kp",
                "date": day.isoformat(),
                "value": mean(values),
                "unit": "Kp",
                "metric": "geomagnetic_kp",
                "title": "NOAA SWPC - geomagnetic daily mean estimated Kp",
            },
            {
                "series_id": "noaa_swpc_solar:geomagnetic:daily_minutes_kp_ge_5",
                "date": day.isoformat(),
                "value": float(sum(1 for v in values if v >= 5.0)),
                "unit": "minutes",
                "metric": "geomagnetic_storm_minutes",
                "title": "NOAA SWPC - geomagnetic minutes at Kp >= 5",
            },
        ])
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def normalize_xrays(records: Iterable[dict[str, Any]], *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    by_energy_day: dict[tuple[str, date], list[float]] = defaultdict(list)
    for rec in records:
        dt = _parse_dt(rec.get("time_tag"))
        value = _to_float(rec.get("flux"))
        energy = str(rec.get("energy") or "").strip()
        if dt is None or value is None or not energy:
            continue
        day = dt.date()
        if day > today:
            continue
        by_energy_day[(energy, day)].append(value)

    rows: list[dict[str, Any]] = []
    for (energy, day), values in sorted(by_energy_day.items()):
        slug = _energy_slug(energy)
        rows.extend([
            {
                "series_id": f"noaa_swpc_solar:goes_xray:{slug}:daily_max_flux",
                "date": day.isoformat(),
                "value": max(values),
                "unit": "W/m^2",
                "metric": "solar_xray_flux",
                "title": f"NOAA SWPC - GOES X-ray {energy} daily max flux",
                "energy": energy,
            },
            {
                "series_id": f"noaa_swpc_solar:goes_xray:{slug}:daily_mean_flux",
                "date": day.isoformat(),
                "value": mean(values),
                "unit": "W/m^2",
                "metric": "solar_xray_flux",
                "title": f"NOAA SWPC - GOES X-ray {energy} daily mean flux",
                "energy": energy,
            },
        ])
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    endpoints = (
        ("solar cycle", SOLAR_CYCLE_URL, normalize_solar_cycle),
        ("planetary Kp", KP_URL, normalize_kp),
        ("GOES X-ray", XRAY_URL, normalize_xrays),
    )
    for label, url, normalizer in endpoints:
        try:
            payload = _fetch_json(url)
        except Exception as exc:  # noqa: BLE001 - public endpoint; preserve existing file on failure
            log(f"  - {label}: fetch failed: {exc}")
            continue
        parsed = normalizer(payload if isinstance(payload, list) else [])
        if parsed:
            dates = sorted({str(r["date"]) for r in parsed})
            series = len({str(r["series_id"]) for r in parsed})
            log(f"  + {label:<12s} {len(parsed):5d} obs across {series} series  {dates[0]}-{dates[-1]}")
            rows.extend(parsed)
        else:
            log(f"  - {label}: no parseable observed rows")

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
    log(f"\nwrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("NOAA SWPC observed solar and space-weather indicators:")
    observations = collect()
    if not observations:
        print("\nNO observations collected - NOAA SWPC endpoints unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
