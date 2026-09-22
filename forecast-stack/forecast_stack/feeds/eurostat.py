"""Eurostat API Statistics (JSON-stat 2.0) — keyless EU OUTCOMES collector.

A self-contained KEYLESS collector for Vati's data layer. Eurostat's dissemination "API Statistics"
endpoint (https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/<DATASET>?format=JSON)
is open, no API key, and returns each dataset as a JSON-stat 2.0 document.

This module builds out the **terminal "outcomes" spine layer** for the EU — realized macro / labour /
innovation / energy / environment outcomes — WIDE: many indicators (GDP, GDP per capita, employment
rate, unemployment rate, R&D intensity, gross R&D expenditure, final energy consumption, greenhouse-gas
emissions, industrial production, high-tech-sector employment, EPO patent applications, population,
tertiary enrolment) across the EU27 aggregate + the member states. Each (indicator × geo) becomes its
own series_id with a row-level `metric`, `domain`, `unit` and `title`. Annual, dated to reference
year-end. These are the slow, authoritative structural series that reliably fire the changepoint
detector and ground the calibration moat with a European baseline.

JSON-stat 2.0 decoding (the tricky part — verified against a real payload):
  • `size` is the length of each dimension in `id` order, e.g. [freq, unit, na_item, geo, time].
  • `value` is a FLAT dict keyed by the ROW-MAJOR linear index over those dimensions (last dimension
    varies fastest). A key may be missing entirely (sparse) — that cell is simply absent / null.
  • Each dimension's `dimension[<name>].category.index` maps a category code -> its position. To turn a
    flat index back into (geo, time) we un-flatten it into per-dimension positions and look up the code
    for the geo and time dimensions. All other dimensions are pinned to a single category by the query,
    so only geo and time actually vary.

Leak discipline (matches forecast_stack/feeds/world_bank.py + engine/pillars/forces.py):
  • Every observation carries its REAL reference date — Eurostat reports annual figures, so `date` =
    December 31 of the reference year (the point in time the year's value is knowable). Nothing is
    synthesized, backfilled, or interpolated: a missing / null cell is DROPPED, never filled, so the
    jsonl is only ground-truth reported points.
  • Annual macro / labour / energy / environment series land months-to-a-year after the reference year
    and are REVISED across several vintages. As a forecasting signal this is a LAG / CONFIRMATION
    channel: it confirms a structural shift AFTER it has happened — it does not lead it. It grounds the
    EU outcome layer with an authoritative baseline / kill-metric, not an early-warning.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-12-31', value:float, metric:str, domain:str, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/eurostat.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
ES_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
OUT_PATH = DATA_DIR / "feeds" / "eurostat.jsonl"

SINCE = 2005        # ~two decades of annual history
CUTOFF_YEAR = 2026  # cap at the data cutoff: drop any reference year strictly after this

# EU OUTCOME datasets. Each spec pins every non-(geo,time) dimension to a single category via
# `params`, so only geo and time vary in the returned cube. metric/domain/unit/title are written
# onto every row. All dataset codes + every pinned dimension value were verified against the live
# JSON-stat API (non-empty value cube) before inclusion.
DATASETS: list[dict] = [
    # ── macro ─────────────────────────────────────────────────────────────────────────────────
    {
        "dataset": "nama_10_gdp", "metric": "gdp_current_prices", "domain": "macro",
        "unit": "current prices, million EUR", "title": "GDP (current prices)",
        "params": {"na_item": "B1GQ", "unit": "CP_MEUR"},
    },
    {
        "dataset": "nama_10_pc", "metric": "gdp_per_capita", "domain": "macro",
        "unit": "current prices, EUR per capita", "title": "GDP per capita (current prices)",
        "params": {"na_item": "B1GQ", "unit": "CP_EUR_HAB"},
    },
    # ── labour outcomes ────────────────────────────────────────────────────────────────────────
    {
        "dataset": "lfsi_emp_a", "metric": "employment_rate", "domain": "labour",
        "unit": "% of population aged 15-64", "title": "Employment rate (15-64)",
        "params": {"sex": "T", "age": "Y15-64", "unit": "PC_POP", "indic_em": "EMP_LFS"},
    },
    {
        "dataset": "une_rt_a", "metric": "unemployment_rate", "domain": "labour",
        "unit": "% of active population aged 15-74", "title": "Unemployment rate (15-74)",
        "params": {"sex": "T", "age": "Y15-74", "unit": "PC_ACT"},
    },
    {
        "dataset": "htec_emp_nat2", "metric": "high_tech_employment_share", "domain": "labour",
        "unit": "% of total employment", "title": "Employment in high-tech sectors",
        "params": {"sex": "T", "nace_r2": "HTC", "unit": "PC_EMP"},
    },
    # ── innovation ─────────────────────────────────────────────────────────────────────────────
    {
        "dataset": "sdg_09_10", "metric": "rd_intensity", "domain": "innovation",
        "unit": "% of GDP", "title": "R&D intensity (gross R&D expenditure)",
        "params": {"sectperf": "TOTAL", "unit": "PC_GDP"},
    },
    {
        "dataset": "rd_e_gerdtot", "metric": "rd_expenditure", "domain": "innovation",
        "unit": "million EUR", "title": "Gross domestic expenditure on R&D",
        "params": {"sectperf": "TOTAL", "unit": "MIO_EUR"},
    },
    {
        "dataset": "pat_ep_ntot", "metric": "epo_patent_applications", "domain": "innovation",
        "unit": "number (by priority year)", "title": "Patent applications to the EPO",
        "params": {"unit": "NR"},
    },
    # ── industry ───────────────────────────────────────────────────────────────────────────────
    {
        "dataset": "sts_inpr_a", "metric": "industrial_production_index", "domain": "industry",
        "unit": "index, 2021=100", "title": "Industrial production index (mining/manufacturing/energy)",
        "params": {"nace_r2": "B-D", "indic_bt": "PRD", "s_adj": "NSA", "unit": "I21"},
    },
    # ── energy & environment ───────────────────────────────────────────────────────────────────
    {
        "dataset": "nrg_bal_c", "metric": "final_energy_consumption", "domain": "energy",
        "unit": "thousand tonnes of oil equivalent", "title": "Final energy consumption (energy use)",
        "params": {"nrg_bal": "FC_E", "siec": "TOTAL", "unit": "KTOE"},
    },
    {
        "dataset": "env_air_gge", "metric": "greenhouse_gas_emissions", "domain": "environment",
        "unit": "thousand tonnes CO2 equivalent", "title": "Greenhouse gas emissions (total, excl. LULUCF)",
        "params": {"airpol": "GHG", "src_crf": "TOTX4_MEMO", "unit": "THS_T"},
    },
    # ── demography & human capital ─────────────────────────────────────────────────────────────
    {
        "dataset": "demo_pjan", "metric": "population", "domain": "demography",
        "unit": "persons (1 January)", "title": "Population on 1 January",
        "params": {"sex": "T", "age": "TOTAL"},
    },
    {
        "dataset": "educ_uoe_enrt01", "metric": "tertiary_enrolment", "domain": "education",
        "unit": "number of students", "title": "Students enrolled in tertiary education",
        "params": {"isced11": "ED5-8", "sex": "T", "unit": "NR",
                   "worktime": "TOTAL", "sector": "TOT_SEC"},
    },
]

# EU27 aggregate (the structural baseline) + the member states. Eurostat geo codes as the API
# expects them; EU27_2020 = the post-Brexit EU27 aggregate. A geo that a dataset does not cover is
# simply absent from that dataset's cube (sparse) — never filled.
GEOS: list[str] = [
    "EU27_2020",
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
]


def _fetch_json(url: str, *, retries: int = 2):
    """GET a keyless Eurostat API URL → parsed JSON. Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 keyless public endpoint
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — network/parse/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None


def fetch_dataset(spec: dict, *, retries: int = 2):
    """Fetch one dataset for ALL geos at once (geo is a repeatable query param) → the raw JSON-stat
    document, or None. Non-(geo,time) dimensions are pinned via spec['params']."""
    params: list[tuple[str, str]] = [("format", "JSON"), ("sinceTimePeriod", str(SINCE))]
    for g in GEOS:
        params.append(("geo", g))
    for k, v in spec["params"].items():
        params.append((k, v))
    url = f"{ES_BASE}/{urllib.parse.quote(spec['dataset'])}?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url, retries=retries)
    # A valid JSON-stat document has class=='dataset' with value/dimension/size/id.
    if not isinstance(data, dict) or "value" not in data or "dimension" not in data:
        return None
    return data


def _unflatten(flat: int, size: list[int]) -> list[int]:
    """Row-major flat index → list of per-dimension positions (last dimension varies fastest)."""
    pos = [0] * len(size)
    for i in range(len(size) - 1, -1, -1):
        pos[i] = flat % size[i]
        flat //= size[i]
    return pos


def normalize(spec: dict, doc: dict) -> list[dict]:
    """Decode a JSON-stat 2.0 document → normalized Vati observations. `date` = Dec-31 of the reference
    year. Only geo and time vary; every other dimension is pinned to one category. Missing/null cells
    and post-cutoff years are dropped (never backfilled)."""
    ids: list[str] = doc["id"]
    size: list[int] = doc["size"]
    dim = doc["dimension"]
    geo_axis = ids.index("geo")
    time_axis = ids.index("time")

    def pos_to_code(name: str) -> dict[int, str]:
        return {p: code for code, p in dim[name]["category"]["index"].items()}

    geo_code = pos_to_code("geo")
    time_code = pos_to_code("time")

    out: list[dict] = []
    for flat_key, raw_val in doc["value"].items():
        if raw_val is None:
            continue  # explicit null — drop (leak discipline)
        try:
            flat = int(flat_key)
        except (TypeError, ValueError):
            continue
        pos = _unflatten(flat, size)
        geo = geo_code.get(pos[geo_axis])
        year = time_code.get(pos[time_axis])
        if geo is None or year is None or len(year) != 4 or not year.isdigit():
            continue
        if int(year) > CUTOFF_YEAR:
            continue  # leak/projection guard: never carry a value dated past the cutoff
        try:
            value = float(raw_val)
        except (TypeError, ValueError):
            continue
        out.append({
            "series_id": f"eurostat:{spec['dataset']}:{geo}",
            "date": f"{year}-12-31",  # REAL reference year, reported point-in-time (annual)
            "value": value,
            "metric": spec["metric"],
            "domain": spec["domain"],
            "unit": spec["unit"],
            "title": f"{spec['title']} — {geo}",
        })
    out.sort(key=lambda o: (o["series_id"], o["date"]))
    return out


def collect(*, log=print) -> list[dict]:
    """Fetch every dataset (all geos in one call each) keyless, decode JSON-stat, normalize, write the
    jsonl. Returns the list of observations actually written. $0. Never fabricates: a dataset that fails
    to fetch is logged and skipped, not filled."""
    all_obs: list[dict] = []
    n_dropped = 0
    for spec in DATASETS:
        doc = fetch_dataset(spec)
        if doc is None:
            n_dropped += 1
            log(f"  - DROP {spec['dataset']:<16} (no JSON-stat document returned)")
            continue
        obs = normalize(spec, doc)
        if not obs:
            n_dropped += 1
            log(f"  - DROP {spec['dataset']:<16} (no dated observations decoded)")
            continue
        all_obs.extend(obs)
        geos = sorted({o["series_id"].rsplit(":", 1)[1] for o in obs})
        log(f"  + {spec['domain']:<11} {spec['metric']:<28} {spec['dataset']:<16} "
            f"{len(geos):>2} geos  {len(obs):>4} obs")
        time.sleep(0.3)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in all_obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}  ({n_dropped} datasets dropped)")
    return all_obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — Eurostat API unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
