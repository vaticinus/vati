"""Ember electricity-data collector — the GRID pillar's generation-mix signal (keyless).

Ember (ember-energy.org, the energy-transition think-tank, ex-Sandbag) publishes the most
complete keyless, dated electricity dataset there is: generation, capacity, demand, emissions and
the fuel-source MIX (coal/gas/nuclear/hydro/wind/solar/...) by country and month/year. It is the
canonical open series behind most "share of renewables / coal" reporting. We pull the public
"full release, long format" CSVs — no API key, no auth, just an HTTPS GET against their public
download bucket (files.ember-energy.org/public-downloads/...).

Why this feeds the GRID pillar (and how it is leak-safe):
  • Generation BY SOURCE in TWh and as a % share is the physical state of the grid: how fast solar
    and wind are displacing fossil capacity. The structural shift Vati cares about (the AI-power
    build-out, the electrification of demand, the coal→clean transition) shows up here first as a
    measured quantity of electricity produced — not as price, not as news.
  • Every observation carries its REAL reporting date (the month it covers / year-end), parsed
    straight from the source row. Nothing is synthesized, backfilled, or smoothed: a month absent
    from the file is absent here. The value is exactly Ember's published number.

LEAK-CLASS — CONFIRMATION/LAG. Generation is a record of electricity ALREADY produced, published
with a reporting lag (the monthly file trails the calendar by ~1–2 months). It CONFIRMS that a
structural shift happened; it does not run ahead of the price of the binding input (that leading
role belongs to the transformer/switchgear PPI and the interconnection-queue series in power.py).
So a fired signal here is corroboration of a transition in progress, never a pre-consensus early
warning — exactly the honest placement Vati gives an attention/record channel.

This module is SELF-CONTAINED (per the build brief): it does NOT touch the sqlite DB, cli.py, or
the schemas. It fetches, normalizes to plain observation dicts {series_id,date,value,unit,title},
and (run as __main__) writes a real sample to data/feeds/ember.jsonl. Cost: $0, keyless.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"

# Public keyless download bucket (found from the ember-energy.org data catalogue pages
# /data/monthly-electricity-data/ and /data/yearly-electricity-data/). No key, no auth.
BASE = "https://files.ember-energy.org/public-downloads"
MONTHLY_URL = f"{BASE}/monthly_full_release_long_format.csv"
YEARLY_URL = f"{BASE}/yearly_full_release_long_format.csv"

# The long-format schema (one row per Area×Date×Variable×Unit):
#   Area, ISO 3 code, Date, Area type, Continent, Ember region, EU, OECD, G20, G7, ASEAN,
#   Category, Subcategory, Variable, Unit, Value, YoY absolute change, YoY % change
# Date is DD/MM/YYYY (first-of-month for the monthly file; 01/01/YYYY for the yearly file).

# The representative slices we surface. Keyed by a stable series_id; we pick a few high-signal
# country × fuel-source generation series (the grid-mix transition), plus aggregate demand.
# Each tuple: (series_id, iso3, category, variable, unit, human title).
MONTHLY_SERIES: list[tuple[str, str, str, str, str, str]] = [
    ("ember.monthly.WLD.solar.twh", "WLD", "Electricity generation", "Solar", "TWh",
     "World monthly solar electricity generation"),
    ("ember.monthly.WLD.wind.twh", "WLD", "Electricity generation", "Wind", "TWh",
     "World monthly wind electricity generation"),
    ("ember.monthly.WLD.coal.twh", "WLD", "Electricity generation", "Coal", "TWh",
     "World monthly coal electricity generation"),
    ("ember.monthly.USA.solar.twh", "USA", "Electricity generation", "Solar", "TWh",
     "United States monthly solar electricity generation"),
    ("ember.monthly.WLD.demand.twh", "WLD", "Electricity demand", "Demand", "TWh",
     "World monthly electricity demand"),
]
YEARLY_SERIES: list[tuple[str, str, str, str, str, str]] = [
    ("ember.yearly.WLD.solar.share", "WLD", "Electricity generation", "Solar", "%",
     "World yearly solar share of generation"),
    ("ember.yearly.WLD.coal.share", "WLD", "Electricity generation", "Coal", "%",
     "World yearly coal share of generation"),
]


def _parse_date(s: str) -> date | None:
    """Parse an Ember reporting date — the REAL date, never a synthesized one.

    The monthly file uses a 'Date' column (DD/MM/YYYY, first-of-month). The yearly file uses a
    bare 'Year' column (e.g. '2000') → we anchor it to that calendar year-end (31 Dec), the date a
    full year's generation is knowable. Either way the date is taken from the source row, not made up.
    """
    s = s.strip()
    if s.isdigit() and len(s) == 4:
        return date(int(s), 12, 31)        # yearly file: 'Year' column → year-end
    from datetime import datetime
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _stream_rows(url: str, *, timeout: int = 120):
    """Stream a remote CSV row-by-row (the monthly file is ~65 MB — don't buffer it whole).

    Yields csv.DictReader dicts. Keyless public GET; raises on network/HTTP error so the caller
    can degrade honestly rather than emit fake data.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 keyless public endpoint
        text = io.TextIOWrapper(resp, encoding="utf-8", errors="replace", newline="")
        reader = csv.DictReader(text)
        for row in reader:
            yield row


def collect(url: str, specs: list[tuple[str, str, str, str, str, str]],
            *, log=print) -> list[dict]:
    """Fetch one Ember long-format CSV and normalize the requested slices to observation dicts.

    Returns a list of {series_id, date:'YYYY-MM-DD', value:float, unit, title}. One pass over the
    streamed file matches every wanted (iso3, category, variable, unit) tuple at once. Values and
    dates are taken verbatim from the source — no fill, no backfill, no smoothing.
    """
    # index the specs by the (iso3, category, variable, unit) key they match
    wanted: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for series_id, iso3, category, variable, unit, title in specs:
        wanted[(iso3, category, variable, unit)] = (series_id, title)

    obs: list[dict] = []
    for row in _stream_rows(url):
        iso3 = (row.get("ISO 3 code") or "").strip()
        # Ember's world aggregate is the Area "World" with a blank ISO code → normalize to "WLD".
        if not iso3 and (row.get("Area") or "").strip() == "World":
            iso3 = "WLD"
        key = (iso3, (row.get("Category") or "").strip(),
               (row.get("Variable") or "").strip(), (row.get("Unit") or "").strip())
        hit = wanted.get(key)
        if not hit:
            continue
        d = _parse_date(row.get("Date") or row.get("Year") or "")
        raw = (row.get("Value") or "").strip()
        if d is None or not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        series_id, title = hit
        obs.append({
            "series_id": series_id,
            "date": d.isoformat(),
            "value": value,
            "unit": key[3],
            "title": title,
        })
    obs.sort(key=lambda o: (o["series_id"], o["date"]))
    log(f"  · {url.rsplit('/', 1)[-1]}: {len(obs)} observations across {len(specs)} series")
    return obs


if __name__ == "__main__":
    out_path = DATA_DIR / "feeds" / "ember.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Ember electricity data (keyless, files.ember-energy.org public-downloads):")
    all_obs: list[dict] = []
    # Yearly first (small file ~ a few MB), then monthly (the larger one). Both keyless.
    print("Yearly full release (long format):")
    all_obs += collect(YEARLY_URL, YEARLY_SERIES)
    print("Monthly full release (long format):")
    all_obs += collect(MONTHLY_URL, MONTHLY_SERIES)

    with out_path.open("w", encoding="utf-8") as fh:
        for o in all_obs:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(all_obs)} real observations to {out_path}")
    print("First few:")
    for o in all_obs[:5]:
        print("  " + json.dumps(o, ensure_ascii=False))
