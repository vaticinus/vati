"""Federal Register policy/regulatory activity collector.

Keyless official US Federal Register documents API. This lands a per-topic COUNT-OVER-TIME of
regulatory/policy activity — the shape that actually fires a changepoint detector. A single-snapshot
permit record has no time-series and never fires; an annual (and monthly) count of published documents
matching a policy topic does.

For each of ~50 structural policy topics we emit:
  * an ANNUAL series  (`...:per_year`)  — documents published per publication year, and
  * a MONTHLY series  (`...:per_month`) — documents published per calendar month,

both keyed on the real Federal Register publication date and capped at the cutoff year (leak-safe).
The API exposes a top-level `count` for any query window, so one cheap request per topic-year (and one
per topic-month) yields the whole series without paging the documents themselves.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"
OUT_PATH = DATA_DIR / "feeds" / "federal_register.jsonl"

# Leak discipline: only count documents published on/before the cutoff. The annual bin for the cutoff
# year is partial-year, so we stop annual bins at the last COMPLETE year and cap monthly bins at the
# cutoff month. Real publication dates only; fetched_at is never an observation date.
START_YEAR = 2010
REQUEST_TIMEOUT_S = 20
REQUEST_SPACING_S = 0.20
MIN_REFRESH_FRACTION = 0.75
MONTHLY_LOOKBACK_YEARS = 0  # annual-only first pass (fast); monthly granularity added in a later increment

# ~50 structural policy/geo topics. term = Federal Register full-text search expression.
TOPICS: tuple[dict[str, str], ...] = (
    {"slug": "export_controls", "term": '"export control" OR "export controls"', "title": "Export controls"},
    {"slug": "semiconductors", "term": "semiconductor OR semiconductors OR microelectronics", "title": "Semiconductors / chips"},
    {"slug": "artificial_intelligence", "term": '"artificial intelligence" OR "machine learning"', "title": "Artificial intelligence"},
    {"slug": "ai_safety", "term": '"AI safety" OR "frontier model" OR "dual-use foundation model"', "title": "AI safety / frontier models"},
    {"slug": "critical_minerals", "term": '"critical minerals" OR "critical materials"', "title": "Critical minerals"},
    {"slug": "rare_earths", "term": '"rare earth" OR "rare earths"', "title": "Rare earths"},
    {"slug": "lithium", "term": "lithium", "title": "Lithium"},
    {"slug": "cobalt_nickel", "term": "cobalt OR nickel", "title": "Cobalt / nickel"},
    {"slug": "tariffs", "term": "tariff OR tariffs", "title": "Tariffs"},
    {"slug": "antidumping", "term": '"antidumping" OR "countervailing duty"', "title": "Antidumping / countervailing duties"},
    {"slug": "sanctions", "term": "sanctions", "title": "Sanctions"},
    {"slug": "entity_list", "term": '"entity list"', "title": "BIS Entity List"},
    {"slug": "cfius", "term": "CFIUS OR \"foreign investment\"", "title": "CFIUS / foreign investment"},
    {"slug": "nuclear_energy", "term": '"nuclear energy" OR "nuclear reactor"', "title": "Nuclear energy"},
    {"slug": "small_modular_reactor", "term": '"small modular reactor" OR "advanced reactor"', "title": "Small modular reactors"},
    {"slug": "grid_transmission", "term": '"transmission line" OR "electric transmission" OR "grid reliability"', "title": "Grid / transmission"},
    {"slug": "interconnection", "term": '"interconnection queue" OR "interconnection request"', "title": "Grid interconnection"},
    {"slug": "ev_vehicles", "term": '"electric vehicle" OR "electric vehicles" OR "zero-emission vehicle"', "title": "Electric vehicles"},
    {"slug": "battery", "term": "battery OR batteries", "title": "Batteries"},
    {"slug": "battery_manufacturing", "term": '"battery manufacturing" OR "battery cell" OR gigafactory', "title": "Battery manufacturing"},
    {"slug": "solar", "term": '"solar energy" OR photovoltaic OR "solar panel"', "title": "Solar"},
    {"slug": "offshore_wind", "term": '"offshore wind"', "title": "Offshore wind"},
    {"slug": "onshore_wind", "term": '"wind energy" OR "wind turbine"', "title": "Wind energy"},
    {"slug": "hydrogen", "term": '"clean hydrogen" OR "hydrogen energy" OR electrolyzer', "title": "Hydrogen"},
    {"slug": "carbon_capture", "term": '"carbon capture" OR "carbon sequestration" OR "carbon dioxide storage"', "title": "Carbon capture / storage"},
    {"slug": "emissions", "term": '"greenhouse gas" OR "carbon emissions" OR "emission standards"', "title": "Emissions"},
    {"slug": "methane", "term": "methane", "title": "Methane"},
    {"slug": "lng_natural_gas", "term": '"liquefied natural gas" OR "LNG export"', "title": "LNG / natural gas exports"},
    {"slug": "pfas", "term": "PFAS OR \"per- and polyfluoroalkyl\"", "title": "PFAS"},
    {"slug": "biotechnology", "term": "biotechnology OR biomanufacturing OR synthetic biology", "title": "Biotechnology"},
    {"slug": "biosecurity", "term": "biosecurity OR \"select agent\" OR \"dual-use research\"", "title": "Biosecurity"},
    {"slug": "gene_therapy", "term": '"gene therapy" OR "cell therapy" OR CRISPR', "title": "Gene / cell therapy"},
    {"slug": "drug_pricing", "term": '"drug pricing" OR "prescription drug" OR "Medicare drug"', "title": "Drug pricing"},
    {"slug": "drones_uas", "term": '"unmanned aircraft" OR drone OR "counter-UAS"', "title": "Drones / UAS"},
    {"slug": "quantum", "term": '"quantum computing" OR "quantum information"', "title": "Quantum"},
    {"slug": "space_launch", "term": '"commercial space" OR "launch vehicle" OR "spaceport"', "title": "Space / launch"},
    {"slug": "satellites_spectrum", "term": "satellite OR \"spectrum allocation\" OR \"orbital debris\"", "title": "Satellites / spectrum"},
    {"slug": "immigration_stem", "term": '"H-1B" OR "skilled worker" OR "STEM" OR "exchange visitor"', "title": "Immigration / STEM visas"},
    {"slug": "antitrust", "term": '"antitrust" OR "merger" OR "competition"', "title": "Antitrust"},
    {"slug": "data_privacy", "term": '"data privacy" OR "consumer data" OR "personal information"', "title": "Data privacy"},
    {"slug": "cybersecurity", "term": "cybersecurity OR \"critical infrastructure protection\"", "title": "Cybersecurity"},
    {"slug": "crypto_digital_assets", "term": '"digital asset" OR cryptocurrency OR "stablecoin"', "title": "Crypto / digital assets"},
    {"slug": "supply_chain", "term": '"supply chain" OR "supply chains"', "title": "Supply chain"},
    {"slug": "shipping_ports", "term": '"maritime" OR "port" OR "ocean shipping"', "title": "Shipping / ports"},
    {"slug": "steel_aluminum", "term": "steel OR aluminum", "title": "Steel / aluminum"},
    {"slug": "semiconductor_chips_act", "term": '"CHIPS Act" OR "CHIPS for America"', "title": "CHIPS Act"},
    {"slug": "water_infrastructure", "term": '"drinking water" OR "water infrastructure" OR "water reuse"', "title": "Water infrastructure"},
    {"slug": "agriculture_fertilizer", "term": "fertilizer OR \"agricultural production\"", "title": "Agriculture / fertilizer"},
    {"slug": "labor_workforce", "term": '"workforce" OR "apprenticeship" OR "labor shortage"', "title": "Labor / workforce"},
    {"slug": "telecom_5g", "term": '"5G" OR "broadband" OR "telecommunications network"', "title": "Telecom / 5G / broadband"},
    {"slug": "nuclear_weapons_nonprolif", "term": '"nonproliferation" OR "nuclear weapon" OR "missile technology"', "title": "Nonproliferation"},
)


def _cutoff_year_month() -> tuple[int, int]:
    now = datetime.now(timezone.utc).date()
    return now.year, now.month


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def _existing_count_same_schema() -> int:
    """Count existing rows already in the new count-over-time schema.

    The refresh guard compares against this, not the raw line count, so a one-time migration from an
    older row schema (e.g. per-day `policy_documents`) does not look like a partial fetch and block
    the write.
    """
    if not OUT_PATH.exists():
        return 0
    n = 0
    with OUT_PATH.open(encoding="utf-8") as f:
        for line in f:
            if '"policy_docs_per_year"' in line or '"policy_docs_per_month"' in line:
                n += 1
    return n


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _count(term: str, gte: str, lte: str) -> int | None:
    """Top-level document count for a topic over [gte, lte]; one cheap request, no doc paging."""
    params = {
        "conditions[term]": term,
        "conditions[publication_date][gte]": gte,
        "conditions[publication_date][lte]": lte,
        "per_page": "1",
        "page": "1",
        "fields[]": "document_number",
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params, doseq=True)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
            data = json.loads(resp.read().decode("utf-8", "replace"))
        count = data.get("count")
        return int(count) if isinstance(count, (int, float)) else None
    except Exception:  # noqa: BLE001 — keyless public endpoint; skip rather than fabricate
        return None


def _month_last_day(year: int, month: int) -> int:
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return (nxt - datetime(year, month, 1)).days


def fetch_topic(topic: dict[str, str], *, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cutoff_year, cutoff_month = _cutoff_year_month()
    # Annual bins through the last COMPLETE year (drop the partial cutoff year → leak/partial-safe).
    last_full_year = cutoff_year - 1
    annual_ok = 0
    for year in range(START_YEAR, last_full_year + 1):
        c = _count(topic["term"], f"{year}-01-01", f"{year}-12-31")
        time.sleep(REQUEST_SPACING_S)
        if c is None:
            log(f"    ! {topic['slug']} {year}: unreachable, skip")
            continue
        if c <= 0:
            continue
        annual_ok += 1
        rows.append({
            "series_id": f"federal_register:{topic['slug']}:per_year",
            "date": f"{year}-12-31",
            "value": float(c),
            "unit": "documents/yr",
            "metric": "policy_docs_per_year",
            "domain": "policy",
            "title": f"Federal Register — {topic['title']} documents per year",
            "topic": topic["title"],
            "term": topic["term"],
        })
    # Monthly bins for the recent window, capped at the cutoff month (leak-safe).
    first_month_year = max(START_YEAR, cutoff_year - MONTHLY_LOOKBACK_YEARS)
    for year in range(first_month_year, cutoff_year + 1):
        for month in range(1, 13):
            if year == cutoff_year and month >= cutoff_month:
                break  # current/future month is incomplete → drop
            last = _month_last_day(year, month)
            c = _count(topic["term"], f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}")
            time.sleep(REQUEST_SPACING_S)
            if c is None or c <= 0:
                continue
            rows.append({
                "series_id": f"federal_register:{topic['slug']}:per_month",
                "date": f"{year}-{month:02d}-{last:02d}",
                "value": float(c),
                "unit": "documents/mo",
                "metric": "policy_docs_per_month",
                "domain": "policy",
                "title": f"Federal Register — {topic['title']} documents per month",
                "topic": topic["title"],
                "term": topic["term"],
            })
    log(f"  + {topic['slug']:<28s} {annual_ok} annual yrs, {len(rows)} obs total")
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    for topic in TOPICS:
        rows = fetch_topic(topic, log=log)
        if rows:
            all_rows.extend(rows)
        else:
            log(f"  - {topic['slug']:<28s} no dated documents")

    existing = _existing_count_same_schema()
    if not all_rows:
        log(f"\nno observations fetched; preserved existing {_existing_line_count()} rows at {OUT_PATH}")
        return []
    if existing and len(all_rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial refresh fetched {len(all_rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    all_rows.sort(key=lambda r: (str(r["series_id"]), str(r["date"])))
    _write_jsonl_atomic(all_rows)
    series = len({r["series_id"] for r in all_rows})
    log(f"\nwrote {len(all_rows)} observations across {series} series → {OUT_PATH}")
    return all_rows


if __name__ == "__main__":
    print("Federal Register policy/regulatory activity per-topic count-over-time (keyless official API):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — Federal Register API unreachable/empty this run.")
    else:
        series = sorted({o["series_id"] for o in observations})
        print(f"\n{len(observations)} observations across {len(series)} series.")
        print("first 5 observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
