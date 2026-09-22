"""FAOSTAT (UN FAO) crop & livestock production — keyless physical-supply collector.

A self-contained KEYLESS collector for Vati's data layer. FAOSTAT publishes the world's official
agricultural production statistics. The live JSON API host (faostatservices.fao.org/api/v1) NOW
requires an Authorization header on every data call ("Missing Authorization Header") and the legacy
fenixservices host is down (521), so the only remaining KEYLESS route is the public bulk-download
host (https://bulks-faostat.fao.org) — open, no key, served from S3/CloudFront. This module pulls the
QCL domain (Production: Crops and livestock products) normalized bulk zip, parses its CSV in memory,
filters to a small basket of staple crops (wheat / maize / rice) × the World aggregate + a few major
producers for the last ~15 years, normalizes each row to a Vati observation, and writes them to
data/feeds/faostat.jsonl. Annual physical production = a supply-elasticity signal (pillar 5).

Leak discipline (matches forecast_stack/feeds/world_bank.py + pillars/forces.py):
  • Every observation carries its REAL reference year — FAOSTAT reports annual production, so
    `date` = December 31 of the reference year (the point at which the year's tonnage is knowable).
    Nothing is synthesized, backfilled, or interpolated: a null/empty `Value` row is DROPPED, never
    filled, so the jsonl is only ground-truth reported points.
  • FAOSTAT production figures are PUBLISHED WITH A LAG and REVISED across vintages. A reference
    year's crop production lands ~6-18 months after the year closes and is revised for several
    vintages afterward. So as a forecasting signal this is a LAG / CONFIRMATION channel: it confirms
    a structural supply shift (a yield regime, a planted-area move, a climate/conflict shock to a
    staple) AFTER it has happened — it does not lead it. It grounds the supply-elasticity pillar as a
    slow, authoritative baseline / kill-metric, not an early-warning.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/faostat.py
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.request
import zipfile
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
# KEYLESS public bulk-download host. The live JSON API (faostatservices.fao.org/api/v1) requires an
# Authorization header now; this S3/CloudFront bucket is the open route.
QCL_ZIP_URL = (
    "https://bulks-faostat.fao.org/production/"
    "Production_Crops_Livestock_E_All_Data_(Normalized).zip"
)
DOMAIN = "QCL"  # Production: Crops and livestock products
OUT_PATH = DATA_DIR / "feeds" / "faostat.jsonl"

ELEMENT_PRODUCTION = "5510"  # FAOSTAT element code for "Production" (unit: tonnes)
YEARS_BACK = 15

# Staple crops: (FAOSTAT item code, item label). Verified live against the QCL ItemCodes table.
ITEMS: list[tuple[str, str]] = [
    ("15", "Wheat"),
    ("56", "Maize (corn)"),
    ("27", "Rice"),
]

# Reporters: (FAOSTAT area code, label). 5000 = World aggregate (the structural baseline); the rest
# are the largest staple producers. NB: China's production is reported under the aggregate code 351
# ("China", incl. mainland+Taiwan+HK+Macao) — the mainland-only code 41 carries no production rows.
AREAS: list[tuple[str, str]] = [
    ("5000", "World"),
    ("351", "China"),
    ("100", "India"),
    ("231", "United States of America"),
    ("185", "Russian Federation"),
]


def _fetch_bytes(url: str, *, retries: int = 2) -> bytes | None:
    """GET a keyless URL → raw bytes. Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310 keyless public endpoint
                return resp.read()
        except Exception:  # noqa: BLE001 — network/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
    return None


def fetch_rows(*, retries: int = 2, log=print) -> list[dict]:
    """Download the QCL normalized bulk zip keyless, parse its CSV in memory → list of RAW FAOSTAT
    rows (dicts) restricted to our element/item/area basket and the last ~YEARS_BACK years, with a
    non-empty Value. Returns [] on fetch failure (never fakes)."""
    blob = _fetch_bytes(QCL_ZIP_URL, retries=retries)
    if not blob:
        log("  - FAOSTAT bulk zip unreachable (no rows fetched)")
        return []

    item_codes = {c for c, _ in ITEMS}
    area_codes = {c for c, _ in AREAS}
    rows: list[dict] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
        # the big normalized data file (others are small code/flag tables)
        data_name = next(
            n for n in zf.namelist()
            if n.lower().endswith(".csv") and "all_data" in n.lower()
        )
        with zf.open(data_name) as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
            min_year = None  # set after we see the latest year; FAOSTAT spans 1961..latest
            buf: list[dict] = []
            for r in reader:
                if r.get("Element Code") != ELEMENT_PRODUCTION:
                    continue
                if r.get("Item Code") not in item_codes:
                    continue
                if r.get("Area Code") not in area_codes:
                    continue
                val = (r.get("Value") or "").strip()
                if not val:  # DROP empties — never backfill/interpolate (leak discipline)
                    continue
                buf.append(r)
            # restrict to the last YEARS_BACK reference years (computed from the data, not "today")
            years = [int(r["Year"]) for r in buf if (r.get("Year") or "").isdigit()]
            if years:
                min_year = max(years) - (YEARS_BACK - 1)
                rows = [r for r in buf if (r.get("Year") or "").isdigit()
                        and int(r["Year"]) >= min_year]
    except Exception as exc:  # noqa: BLE001 — corrupt zip/csv: report, return nothing
        log(f"  - FAOSTAT parse failed: {exc!r}")
        return []
    return rows


def normalize(raw_rows: list[dict]) -> list[dict]:
    """RAW FAOSTAT rows → normalized Vati observations. `date` = Dec-31 of the reference year (the
    point in time the annual production is knowable). value cast to float; unit/title from the row."""
    area_label = {c: lbl for c, lbl in AREAS}
    item_label = {c: lbl for c, lbl in ITEMS}
    out: list[dict] = []
    for r in raw_rows:
        year = (r.get("Year") or "").strip()
        if len(year) != 4 or not year.isdigit():
            continue
        try:
            value = float(r["Value"])
        except (KeyError, TypeError, ValueError):
            continue
        item_code = r.get("Item Code", "")
        area_code = r.get("Area Code", "")
        item = item_label.get(item_code, r.get("Item", item_code))
        area = area_label.get(area_code, r.get("Area", area_code))
        unit = (r.get("Unit") or "t").strip()
        out.append({
            "series_id": f"faostat:{DOMAIN}:{item_code}:{area_code}",
            "date": f"{year}-12-31",  # REAL reference year, reported point-in-time (annual)
            "value": value,
            "unit": unit,
            "title": f"{item} production — {area}",
        })
    out.sort(key=lambda o: (o["series_id"], o["date"]))
    return out


def collect(*, log=print) -> list[dict]:
    """Download the QCL bulk keyless, filter to the staple basket, normalize, write the jsonl. Returns
    the list of observations actually written. $0. Never fabricates: a failed fetch is logged and the
    jsonl is left empty rather than filled."""
    raw = fetch_rows(log=log)
    obs = normalize(raw)

    # log a per-series summary
    by_series: dict[str, list[dict]] = {}
    for o in obs:
        by_series.setdefault(o["series_id"], []).append(o)
    for sid, group in sorted(by_series.items()):
        log(f"  + {sid:<28} {group[0]['date'][:4]}–{group[-1]['date'][:4]}  {len(group)} obs")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(obs)} observations → {OUT_PATH}")
    return obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — FAOSTAT bulk host unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
