"""UCDP GED — keyless conflict battle-related-deaths time-series collector.

A self-contained KEYLESS collector for Vati's data layer. UCDP (Uppsala Conflict Data Program)
publishes the Georeferenced Event Dataset (GED): one row per geolocated lethal event, each carrying
its REAL event date (`date_start`) and a best/high/low estimate of deaths (`best`).

KEYLESS PATH (important): the live JSON API at https://ucdpapi.pcr.uu.se/api/gedevents/<ver> now
returns `{"API token required..."}` for EVERY version/endpoint we probed (24.1/23.1/.../17.2,
gedevents + ucdpprioconflict) as of 2026-06 — it was made token-gated. The PUBLIC STATIC EXPORT
host https://ucdp.uu.se/downloads/ged/ged241-csv.zip is still fully open (HTTP 200, no key, no
cookie) and is the canonical citable download. This collector pulls that keyless zip and aggregates
it to dated monthly series — NO API key anywhere.

What it builds: per a small basket of conflict-active countries, the GED is reduced to a MONTHLY
battle-related-deaths series. Each event's `best` death count is bucketed by the month of its REAL
`date_start`; the observation date is the LAST day of that month (the point in time the month's toll
is fully knowable). Months with no recorded events are simply absent — never zero-filled.

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every observation carries its REAL event-derived date. `date_start` is the actual day the lethal
    event occurred; we aggregate to month and stamp month-end. Nothing is synthesized, backfilled, or
    interpolated — a month with no events is DROPPED, not zeroed; estimates are UCDP's own, never ours.
  • Leak class = COINCIDENT-to-LAG. Deaths are recorded as/after violence happens, and UCDP's coded
    vintage is published with a multi-month-to-annual lag (this static export is the v24.1 yearly
    release, data through 2023). So as a forecasting signal it moves WITH or slightly BEHIND the
    priced conflict outcome — it confirms/escalates an ongoing conflict regime rather than leading it.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/ucdp.py
"""

from __future__ import annotations

import calendar
import csv
import io
import json
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"

# Canonical keyless static export (UCDP GED v24.1, full georeferenced event dataset, data 1989-2023).
# The token-gated JSON API (ucdpapi.pcr.uu.se) is deliberately NOT used — this host needs no key.
GED_ZIP_URL = "https://ucdp.uu.se/downloads/ged/ged241-csv.zip"
CSV_NAME = "GEDEvent_v24_1.csv"

OUT_PATH = DATA_DIR / "feeds" / "ucdp.jsonl"

# A small basket of representative conflict-active countries (GED `country` field, exact spelling).
# These are the headline structural-conflict theatres in the v24.1 vintage.
COUNTRIES = ["Ukraine", "Syria", "Ethiopia"]

UNIT = "battle-related deaths (best estimate)"


def _download_zip(url: str, *, retries: int = 2) -> bytes | None:
    """GET the keyless static zip → bytes. Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 keyless public host
                return resp.read()
        except Exception:  # noqa: BLE001 — network/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            return None


def _month_end(year: int, month: int) -> str:
    """Last calendar day of (year, month) → 'YYYY-MM-DD' (the point the month's toll is knowable)."""
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last:02d}"


def aggregate(raw_zip: bytes, countries: list[str]) -> dict[str, dict[str, float]]:
    """Stream the GED CSV out of the zip and SUM `best` deaths per (country, month) by REAL
    `date_start`. Returns {country: {'YYYY-MM': total_best}}. Rows for other countries / unparseable
    dates / missing `best` are skipped — never zero-filled."""
    wanted = set(countries)
    # {country: {'YYYY-MM': summed best}}
    buckets: dict[str, dict[str, float]] = {c: defaultdict(float) for c in countries}

    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        with zf.open(CSV_NAME) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
            reader = csv.DictReader(text)
            for row in reader:
                country = (row.get("country") or "").strip()
                if country not in wanted:
                    continue
                ds = (row.get("date_start") or "").strip()  # 'YYYY-MM-DD HH:MM:SS.000'
                if len(ds) < 7 or ds[4] != "-":
                    continue
                ym = ds[:7]  # 'YYYY-MM'
                try:
                    best = float(row.get("best") or "")
                except (TypeError, ValueError):
                    continue
                buckets[country][ym] += best
    return buckets


def normalize(buckets: dict[str, dict[str, float]]) -> list[dict]:
    """(country → month → best) → normalized Vati observations stamped at month-end."""
    out: list[dict] = []
    for country, months in buckets.items():
        series_id = f"ucdp:ged241:battle_deaths:{country.replace(' ', '_')}"
        for ym, total in months.items():
            try:
                year, month = int(ym[:4]), int(ym[5:7])
            except ValueError:
                continue
            out.append({
                "series_id": series_id,
                "date": _month_end(year, month),          # REAL event-month, month-end point-in-time
                "value": float(total),
                "unit": UNIT,
                "title": f"UCDP GED monthly battle-related deaths — {country}",
            })
    out.sort(key=lambda o: (o["series_id"], o["date"]))
    return out


def collect(*, log=print) -> list[dict]:
    """Download the keyless GED zip, aggregate to monthly series, write the jsonl. Returns the
    observations actually written. $0. Never fabricates: a failed download writes nothing."""
    log(f"downloading keyless GED export … {GED_ZIP_URL}")
    raw = _download_zip(GED_ZIP_URL)
    if raw is None:
        log("  ! download failed (host unreachable) — no data written")
        return []
    log(f"  got {len(raw):,} bytes; streaming {CSV_NAME} …")
    buckets = aggregate(raw, COUNTRIES)
    obs = normalize(buckets)
    if not obs:
        log("  ! no observations parsed (unexpected CSV shape) — no data written")
        return []

    by_series: dict[str, list[dict]] = defaultdict(list)
    for o in obs:
        by_series[o["series_id"]].append(o)
    for sid, recs in by_series.items():
        log(f"  + {sid}  {recs[0]['date'][:7]}–{recs[-1]['date'][:7]}  {len(recs)} months")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(obs)} observations → {OUT_PATH}")
    return obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — UCDP static export unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
