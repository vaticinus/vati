"""Land ownership / tenure / concentration — keyless structural land-control collector.

The repo already carries `land_matrix` (large-scale land ACQUISITION/grab deals in developing
countries) — that is land *transactions*, not land *ownership*. This feed fills the OWNERSHIP /
TENURE / CONCENTRATION gap: who holds, controls and uses the world's physical land base, as a
slow structural constraint layer. Everything here is KEYLESS and FREE; anything that needs a key or
payment is skipped and noted, never faked.

Sources assembled (all keyless, all dated by their REAL reference/publication date):

  1. FAOSTAT RL "Land Use" normalized bulk (domain code RL) — the authoritative global land-tenure /
     land-use census. 284 areas (countries + regional + World aggregates), 1961-2025, per land item
     (Agricultural land, Arable land, Permanent crops, Cropland, Permanent meadows & pastures,
     Forest land, Country area, organic / irrigated / tillage sub-classes...). We surface two element
     families per (area × item):
        • absolute Area (1000 ha) — the physical land stock,
        • Share-in elements (% of Land area / Agricultural land / Cropland / Forest) — the structural
          composition / tenure-mix signal.
     This is the spine of the feed (most rows). Keyless bulk zip, parsed in memory.

  2. World Bank WDI land basket (AG.LND.* keyless Indicators API) — agricultural land km², arable
     land (ha and per-capita), forest km², permanent cropland %, total land area km², for the World
     aggregate + a wide basket of major economies, country × year. Decorrelated cross-check on (1)
     plus per-capita arable land (the scarcity ratio land_matrix never measures).

  3. Our World in Data HYDE long-run land use (`land-use-over-the-long-term` grapher CSV) — historical
     cropland / grazing / built-up land back to 10,000 BCE by country. UNIQUE long-horizon structural
     series (not in FAOSTAT, which starts 1961); the deep baseline for "how much land humanity has
     ever converted". Keyless grapher CSV endpoint.

  4. US PAD-US 4.1 Summary Statistics (USGS, keyless ScienceBase data release, published 2025-03-31) —
     the per-STATE breakdown of managed/protected land by manager TYPE (Federal / State / Private /
     American Indian / Local / NGO ...). We derive, per state: federally-managed acres and the FEDERAL
     SHARE of catalogued managed land — the canonical "share of land federally owned by state" metric.
     A dated SNAPSHOT (PAD-US 4.1), not a time series; `published_at` = the PAD-US 4.1 publication date.

  5. MiningTerminal local mineral-rights GeoJSON (OPTIONAL, off by default) — per-jurisdiction
     mineral-rights HOLDER-CONCENTRATION: top-10 holders' share of total active claim AREA per
     country/jurisdiction, derived by STREAMING the GeoJSON `properties` only (never geometry, capped
     feature budget). `forecast_stack/feeds/miningterminal_permits.py` already emits raw holder-group area
     counts; this adds the CONCENTRATION ratio (an HHI-style ownership signal) the other feed does not.
     Enable with `--mining`. Off by default so the standard run never touches the 30 GB local store.

Normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit, metric, domain:'land', title,
   source_url, published_at:'YYYY-MM-DD'}

LEAK DISCIPLINE (matches usgs_minerals.py / world_bank.py):
  • Every row carries its REAL reference date — `date` = Dec-31 of the data's reference YEAR (the
    point at which the year's figure is knowable), never today / fetched_at. For the dated PAD-US
    snapshot, `date` = the release date and `published_at` = same. For mining concentration, `date` =
    the GeoJSON artifact's snapshot date.
  • NOTHING is fabricated, backfilled or interpolated. Non-numeric / blank / withheld cells are
    DROPPED. Empty / 404 sources are skipped with a note.

LEAK-CLASS — SLOW STRUCTURAL BASELINE / KILL-METRIC. Land tenure and public-land ownership move on a
decade scale and are published as authoritative annual/periodic stocktakes AFTER the reference year.
This grounds a slow, hard-to-game constraint baseline (where land is owned/controlled and how that
composition shifts) — it does not run ahead of a priced market outcome.

GAPS / KNOWN LIMITATIONS (noted, not faked):
  • Land-holding INEQUALITY (agricultural-census land Gini, distribution by holding-size) has NO
    keyless machine-readable global dataset — FAO's Land Statistics yearbook tenure/Gini tables are
    PDF/Excel-behind-portal, and the OWID land-distribution / land-Gini slugs 404. Left as a gap.
  • FAOSTAT has no separate public "Land Tenure" normalized bulk (the tenure indicators live inside
    the RL Land-Use domain we already ingest, plus a portal-only yearbook); we take what RL exposes.
  • PAD-US covers only catalogued PROTECTED/MANAGED land (federal share of *managed* land per state),
    not every private parcel; it is the best keyless US public-land-ownership proxy.

$0, keyless. Run directly:  uv run python forecast_stack/feeds/land_ownership.py
                            uv run python forecast_stack/feeds/land_ownership.py --mining   (also derive mineral-rights concentration)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OUT_PATH = DATA_DIR / "feeds" / "land_ownership.jsonl"
DOMAIN = "land"
REQUEST_TIMEOUT_S = 180
FETCH_RETRIES = 2
MIN_REFRESH_FRACTION = 0.9

# ───────────────────────── shared keyless fetch / IO ─────────────────────────

def _fetch_bytes(url: str, *, timeout: int = REQUEST_TIMEOUT_S, retries: int = FETCH_RETRIES) -> bytes | None:
    """Keyless public GET → raw bytes. Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 keyless public endpoint
                return resp.read()
        except Exception:  # noqa: BLE001 — network/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None


def _to_float(raw) -> float | None:
    s = str(raw if raw is not None else "").strip().replace(",", "")
    if not s or s.lower() in {"na", "n/a", "nan", "null", "..", "-", "--"}:
        return None
    if s[0] in "<>~":  # inequality / approximation flag, not a reported number
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def _write_jsonl_atomic(rows: list[dict]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    tmp.replace(OUT_PATH)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower()).strip("_")


# ───────────────────────── 1. FAOSTAT RL (Land Use) ─────────────────────────

FAOSTAT_RL_ZIP = "https://bulks-faostat.fao.org/production/Inputs_LandUse_E_All_Data_(Normalized).zip"

# RL Element Code → (metric, unit). We surface the absolute physical land stock plus the structural
# share/composition elements (the tenure-mix signal). Per-capita / carbon / value-density elements are
# dropped to keep the series tight and unambiguous.
_RL_ELEMENTS = {
    "5110":  ("land_use_area",          "1000 ha"),
    "7209":  ("land_share_of_land_area", "%"),
    "7208":  ("land_share_of_agri_land", "%"),
    "7252":  ("land_share_of_cropland",  "%"),
    "7210":  ("land_share_of_forest",    "%"),
}


def collect_faostat_landuse(*, log=print) -> list[dict]:
    """FAOSTAT RL Land-Use normalized bulk → one series per (area × item × element). Keyless zip."""
    blob = _fetch_bytes(FAOSTAT_RL_ZIP)
    if blob is None:
        log("  - FAOSTAT RL: bulk zip unreachable (skipped)")
        return []
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        csv_name = next(n for n in z.namelist() if "Normalized" in n and n.endswith(".csv"))
        text = z.read(csv_name).decode("utf-8-sig", "replace")
    except Exception as exc:  # noqa: BLE001
        log(f"  - FAOSTAT RL: bad zip ({exc})")
        return []

    obs: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        ecode = (row.get("Element Code") or "").strip()
        if ecode not in _RL_ELEMENTS:
            continue
        year = (row.get("Year") or "").strip()
        if len(year) != 4 or not year.isdigit():
            continue
        value = _to_float(row.get("Value"))
        if value is None:
            continue
        metric, unit = _RL_ELEMENTS[ecode]
        area = (row.get("Area") or "").strip()
        item = (row.get("Item") or "").strip()
        area_code = (row.get("Area Code") or "").strip()
        item_code = (row.get("Item Code") or "").strip()
        obs.append({
            "series_id": f"faostat_rl:{area_code}:{item_code}:{ecode}",
            "date": f"{year}-12-31",   # REAL reference year (annual census), knowable at year-end
            "value": value,
            "unit": unit,
            "metric": metric,
            "domain": DOMAIN,
            "title": f"FAOSTAT Land Use — {item} ({metric.replace('land_', '').replace('_', ' ')}) — {area}",
            "source_url": "https://www.fao.org/faostat/en/#data/RL",
            "published_at": f"{year}-12-31",
        })
    n_series = len({o["series_id"] for o in obs})
    if obs:
        yrs = sorted({o["date"][:4] for o in obs})
        log(f"  + FAOSTAT RL Land Use: {len(obs)} obs / {n_series} series  {yrs[0]}–{yrs[-1]}")
    return obs


# ───────────────────────── 2. World Bank land basket ─────────────────────────

WB_BASE = "https://api.worldbank.org/v2"
WB_PER_PAGE = 1000

# Keyless WDI land indicators (verified live). Each (indicator × country) is its own series.
WB_INDICATORS: list[dict] = [
    {"code": "AG.LND.TOTL.K2",    "metric": "land_total_area",       "unit": "km2",            "label": "total land area"},
    {"code": "AG.LND.AGRI.K2",    "metric": "land_agricultural_area", "unit": "km2",           "label": "agricultural land"},
    {"code": "AG.LND.AGRI.ZS",    "metric": "land_agricultural_share", "unit": "%",            "label": "agricultural land share"},
    {"code": "AG.LND.ARBL.HA",    "metric": "land_arable_area",      "unit": "hectares",        "label": "arable land"},
    {"code": "AG.LND.ARBL.HA.PC", "metric": "land_arable_per_capita", "unit": "hectares/person", "label": "arable land per person"},
    {"code": "AG.LND.ARBL.ZS",    "metric": "land_arable_share",     "unit": "%",               "label": "arable land share"},
    {"code": "AG.LND.CROP.ZS",    "metric": "land_permcrop_share",   "unit": "%",               "label": "permanent cropland share"},
    {"code": "AG.LND.FRST.K2",    "metric": "land_forest_area",      "unit": "km2",             "label": "forest area"},
    {"code": "AG.LND.FRST.ZS",    "metric": "land_forest_share",     "unit": "%",               "label": "forest area share"},
]

# World aggregate + a wide basket of major land economies (ISO3).
WB_COUNTRIES = [
    "WLD", "USA", "CHN", "IND", "BRA", "RUS", "CAN", "AUS", "ARG", "IDN",
    "COD", "KAZ", "SAU", "MEX", "ZAF", "NGA", "UKR", "FRA", "DEU", "TUR",
    "PAK", "ETH", "SDN", "AGO", "COL", "BOL", "TZA", "MOZ", "ZMB", "MMR",
]


def fetch_wb_series(code: str, iso3: str) -> list[dict]:
    params = {"format": "json", "per_page": WB_PER_PAGE}
    url = f"{WB_BASE}/country/{urllib.parse.quote(iso3)}/indicator/{urllib.parse.quote(code)}?{urllib.parse.urlencode(params)}"
    raw = _fetch_bytes(url, timeout=30, retries=2)
    if raw is None:
        return []
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return []
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return []
    return payload[1]


def collect_world_bank(*, log=print) -> list[dict]:
    obs: list[dict] = []
    for ind in WB_INDICATORS:
        kept = 0
        for iso3 in WB_COUNTRIES:
            for row in fetch_wb_series(ind["code"], iso3):
                value = _to_float(row.get("value"))
                year = str(row.get("date") or "").strip()
                if value is None or len(year) != 4 or not year.isdigit():
                    continue
                cname = ((row.get("country") or {}).get("value") or iso3)
                obs.append({
                    "series_id": f"wb_land:{ind['code']}:{iso3}",
                    "date": f"{year}-12-31",
                    "value": value,
                    "unit": ind["unit"],
                    "metric": ind["metric"],
                    "domain": DOMAIN,
                    "title": f"World Bank {ind['label']} — {cname}",
                    "source_url": f"https://data.worldbank.org/indicator/{ind['code']}",
                    "published_at": f"{year}-12-31",
                })
                kept += 1
            time.sleep(0.05)
        log(f"  + World Bank {ind['code']:<16} {kept} obs")
    return obs


# ───────────────────────── 3. OWID HYDE long-run land use ─────────────────────────

OWID_LONGRUN = "https://ourworldindata.org/grapher/land-use-over-the-long-term.csv?useColumnShortNames=true"
# short-name column → (metric, label). HYDE historical land-use back to -10000.
_OWID_LONGRUN_COLS = {
    "cropland_c": ("land_cropland_historical", "historical cropland"),
    "grazing_c":  ("land_grazing_historical",  "historical grazing land"),
    "uopp_c":     ("land_builtup_historical",  "historical built-up land"),
}


def collect_owid_longrun(*, log=print) -> list[dict]:
    raw = _fetch_bytes(OWID_LONGRUN, timeout=60, retries=2)
    if raw is None:
        log("  - OWID long-run land use: unreachable (skipped)")
        return []
    text = raw.decode("utf-8", "replace")
    if text.lstrip().startswith("{"):  # 404/JSON error body
        log("  - OWID long-run land use: 404 / not a CSV (skipped)")
        return []
    obs: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    cols = [c for c in _OWID_LONGRUN_COLS if c in (reader.fieldnames or [])]
    for row in reader:
        entity = (row.get("entity") or "").strip()
        ccode = (row.get("code") or "").strip()
        year = (row.get("year") or "").strip()
        if not entity or not year.lstrip("-").isdigit():
            continue
        yr = int(year)
        if yr < 1:  # pre-1 CE: skip (negative-year date strings aren't valid ISO; structural deep base only)
            continue
        for col in cols:
            value = _to_float(row.get(col))
            if value is None:
                continue
            metric, label = _OWID_LONGRUN_COLS[col]
            obs.append({
                "series_id": f"owid_hyde:{col}:{ccode or _slug(entity)}",
                "date": f"{yr:04d}-12-31",
                "value": value,
                "unit": "hectares",
                "metric": metric,
                "domain": DOMAIN,
                "title": f"OWID/HYDE {label} — {entity}",
                "source_url": "https://ourworldindata.org/grapher/land-use-over-the-long-term",
                "published_at": f"{yr:04d}-12-31",
            })
    log(f"  + OWID/HYDE long-run land use: {len(obs)} obs / "
        f"{len({o['series_id'] for o in obs})} series")
    return obs


# ───────────────────────── 4. US PAD-US federal-ownership-by-state ─────────────────────────

PADUS_ITEM = "https://www.sciencebase.gov/catalog/item/6759b69fd34edfeb8710a3ea?format=json"
PADUS_STATE_CSV_NAME = "PADUS4_1VectorAnalysis_Uni_State_Clip_CENSUS2022.csv"
PADUS_PUBLISHED = "2025-03-31"   # PAD-US 4.1 publication date (from ScienceBase item dates)


def _resolve_sciencebase_file(item_url: str, name_substr: str) -> str | None:
    raw = _fetch_bytes(item_url, timeout=30, retries=2)
    if raw is None:
        return None
    try:
        item = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    for f in item.get("files", []) or []:
        if name_substr in (f.get("name") or "") and f.get("url"):
            return f["url"]
    return None


def collect_padus_federal(*, log=print) -> list[dict]:
    """US PAD-US 4.1 per-state managed-land by manager TYPE → federal acres + federal share per state.

    A dated SNAPSHOT (not a time series). `date`/`published_at` = PAD-US 4.1 release. Aggregates the
    per-record acreage (GIS_AcrsDb) by (state × manager type), then derives, per state, the federal
    share of all catalogued managed land.
    """
    url = _resolve_sciencebase_file(PADUS_ITEM, "PADUS4_1SummaryStatistics_TabularData_CSV")
    if url is None:
        log("  - PAD-US: could not resolve summary-statistics zip (skipped)")
        return []
    blob = _fetch_bytes(url, timeout=120, retries=2)
    if blob is None:
        log("  - PAD-US: download failed (skipped)")
        return []
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        text = z.read(PADUS_STATE_CSV_NAME).decode("utf-8-sig", "replace")
    except Exception as exc:  # noqa: BLE001
        log(f"  - PAD-US: bad zip / missing state CSV ({exc})")
        return []

    # acres by (state, manager type) and state totals
    by_type: dict[tuple[str, str], float] = defaultdict(float)
    state_total: dict[str, float] = defaultdict(float)
    for row in csv.DictReader(io.StringIO(text)):
        state = (row.get("ST_Name") or "").strip()
        mtype = (row.get("MngTp_Desc") or "").strip()
        acres = _to_float(row.get("GIS_AcrsDb"))
        if not state or not mtype or acres is None or acres <= 0:
            continue
        by_type[(state, mtype)] += acres
        state_total[state] += acres

    obs: list[dict] = []
    for (state, mtype), acres in sorted(by_type.items()):
        st = _slug(state)
        mt = _slug(mtype)
        obs.append({
            "series_id": f"padus_mng_acres:{st}:{mt}",
            "date": PADUS_PUBLISHED,
            "value": round(acres, 1),
            "unit": "acres",
            "metric": "managed_land_acres_by_manager_type",
            "domain": DOMAIN,
            "title": f"PAD-US managed land — {mtype}-managed acres — {state}",
            "source_url": "https://www.sciencebase.gov/catalog/item/6759b69fd34edfeb8710a3ea",
            "published_at": PADUS_PUBLISHED,
        })
    # federal share of catalogued managed land per state
    for state, total in sorted(state_total.items()):
        fed = by_type.get((state, "Federal"), 0.0)
        if total <= 0:
            continue
        obs.append({
            "series_id": f"padus_federal_share:{_slug(state)}",
            "date": PADUS_PUBLISHED,
            "value": round(100.0 * fed / total, 3),
            "unit": "% of catalogued managed land",
            "metric": "federal_share_of_managed_land",
            "domain": DOMAIN,
            "title": f"PAD-US federal share of catalogued managed land — {state}",
            "source_url": "https://www.sciencebase.gov/catalog/item/6759b69fd34edfeb8710a3ea",
            "published_at": PADUS_PUBLISHED,
        })
    log(f"  + PAD-US 4.1 US managed-land ownership: {len(obs)} obs / "
        f"{len({o['series_id'] for o in obs})} series  (snapshot {PADUS_PUBLISHED})")
    return obs


# ───────────────────────── 5. MiningTerminal holder-concentration (optional) ─────────────────────────

MINING_DIR = None
if os.environ.get("MININGTERMINAL_PERMITS_DIR"):
    MINING_DIR = Path(os.environ["MININGTERMINAL_PERMITS_DIR"])
MINING_MAX_FEATURES_PER_FILE = 400_000   # cap: stream properties only, never blow memory
MINING_MIN_JURIS_AREA_HA = 1_000.0       # ignore trivially small jurisdictions
MINING_SNAPSHOT_DATE = "2026-05-28"      # the local artifact snapshot date (from filenames)

# locate each `"properties":` key; the object that follows is brace-counted out (order-independent,
# so it works whether geometry precedes or follows properties). Geometry coordinate arrays are never
# parsed — we only ever scan/decode the small properties object.
_PROP_KEY = re.compile(rb'"properties"\s*:\s*\{')


def _extract_object(buf: bytes, brace_pos: int) -> tuple[bytes | None, int]:
    """Brace-count the JSON object starting at `brace_pos` (the opening '{'). String-aware so braces
    inside string values don't miscount. Returns (object_bytes, end_index) or (None, -1) if the
    object is not fully present in `buf` yet (needs more bytes)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(brace_pos, len(buf)):
        c = buf[i]
        if in_str:
            if esc:
                esc = False
            elif c == 0x5C:      # backslash
                esc = True
            elif c == 0x22:      # closing quote
                in_str = False
            continue
        if c == 0x22:            # opening quote
            in_str = True
        elif c == 0x7B:          # {
            depth += 1
        elif c == 0x7D:          # }
            depth -= 1
            if depth == 0:
                return buf[brace_pos:i + 1], i + 1
    return None, -1


def _iter_feature_props(path: Path, cap: int):
    """Yield up to `cap` feature `properties` dicts from a GeoJSON file WITHOUT loading geometry.

    Reads in chunks, finds each `"properties":{` key and brace-counts out just that object — robust to
    feature key order (geometry before OR after properties). Memory-bounded; coordinate arrays are
    never parsed.
    """
    buf = b""
    seen = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk and not buf:
                break
            buf += chunk
            consumed = 0
            for m in _PROP_KEY.finditer(buf):
                obj, end = _extract_object(buf, m.end() - 1)
                if obj is None:
                    break  # object spans past current buffer; wait for more bytes
                try:
                    props = json.loads(obj.decode("utf-8", "replace"))
                except ValueError:
                    consumed = end
                    continue
                yield props
                seen += 1
                consumed = end
                if seen >= cap:
                    return
            if not chunk:
                break
            # keep the unconsumed tail (bridges chunk boundaries / a half-read properties object)
            buf = buf[consumed:] if consumed else buf[-(2 << 20):]


def _clean_holder(name: str) -> str:
    s = re.sub(r"\(\s*\d+(?:\.\d+)?\s*%\s*\)", "", str(name))     # strip "(100%)" stake suffix
    s = re.sub(r"[\"'`]+", "", s)
    return re.sub(r"\s+", " ", s).strip().upper()


def collect_mining_concentration(*, log=print) -> list[dict]:
    """Per-jurisdiction mineral-rights holder concentration: top-10 holders' share of total active
    claim AREA per country. Streams GeoJSON properties only (capped); derives an HHI too. OPTIONAL."""
    if MINING_DIR is None or not MINING_DIR.exists():
        log(f"  - mining: local dir not found {MINING_DIR} (skipped)")
        return []
    # country → holder → area_ha
    by_country: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    files = sorted(p for p in MINING_DIR.glob("*.geojson") if p.stat().st_size > 1000)
    for path in files:
        n = 0
        for props in _iter_feature_props(path, MINING_MAX_FEATURES_PER_FILE):
            holder = props.get("holder_name") or props.get("holder") or props.get("owner")
            area = _to_float(props.get("area_hectares") or props.get("area_ha") or props.get("AREA_HA"))
            country = (props.get("country") or props.get("COUNTRY") or "").strip().upper()
            if not holder or area is None or area <= 0 or not country:
                continue
            by_country[country][_clean_holder(holder)] += area
            n += 1
        if n:
            log(f"    mining {path.name}: {n} priced features")

    today = date.today().isoformat()
    obs: list[dict] = []
    for country, holders in sorted(by_country.items()):
        total = sum(holders.values())
        if total < MINING_MIN_JURIS_AREA_HA or len(holders) < 2:
            continue
        ranked = sorted(holders.values(), reverse=True)
        top10 = sum(ranked[:10])
        shares = [a / total for a in holders.values()]
        hhi = round(10000.0 * sum(s * s for s in shares), 1)   # 0..10000
        obs.append({
            "series_id": f"mining_top10_share:{_slug(country)}",
            "date": MINING_SNAPSHOT_DATE,
            "value": round(100.0 * top10 / total, 2),
            "unit": "% of active claim area",
            "metric": "mineral_rights_top10_holder_share",
            "domain": DOMAIN,
            "title": f"Mineral-rights concentration — top-10 holders' share of active claim area — {country}",
            "source_url": "local:miningterminal/permits",
            "published_at": MINING_SNAPSHOT_DATE,
        })
        obs.append({
            "series_id": f"mining_hhi:{_slug(country)}",
            "date": MINING_SNAPSHOT_DATE,
            "value": hhi,
            "unit": "HHI (0-10000) by claim area",
            "metric": "mineral_rights_holder_hhi",
            "domain": DOMAIN,
            "title": f"Mineral-rights ownership concentration (HHI by claim area) — {country}",
            "source_url": "local:miningterminal/permits",
            "published_at": MINING_SNAPSHOT_DATE,
        })
    log(f"  + mining holder-concentration: {len(obs)} obs / "
        f"{len({o['series_id'] for o in obs})} series across {len(by_country)} countries")
    return obs


# ───────────────────────── orchestration ─────────────────────────

def collect(*, include_mining: bool = False, log=print) -> list[dict]:
    all_obs: list[dict] = []
    all_obs += collect_faostat_landuse(log=log)
    all_obs += collect_world_bank(log=log)
    all_obs += collect_owid_longrun(log=log)
    all_obs += collect_padus_federal(log=log)
    if include_mining:
        all_obs += collect_mining_concentration(log=log)

    # dedup (series_id, date) → keep first (sources are disjoint, but belt-and-braces)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for o in all_obs:
        key = (o["series_id"], o["date"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(o)
    deduped.sort(key=lambda o: (o["series_id"], o["date"]))

    existing = _existing_line_count()
    if not deduped:
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(deduped) < int(existing * MIN_REFRESH_FRACTION):
        log(f"\npartial refresh {len(deduped)} < {MIN_REFRESH_FRACTION:.0%} of existing {existing}; "
            f"preserved {OUT_PATH}")
        return []
    _write_jsonl_atomic(deduped)
    log(f"\nwrote {len(deduped)} observations → {OUT_PATH}")
    return deduped


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Keyless land ownership/tenure/concentration feed.")
    ap.add_argument("--mining", action="store_true",
                    help="also derive mineral-rights holder concentration from the local 30GB GeoJSON store")
    args = ap.parse_args()

    print("Land ownership / tenure / concentration (keyless):")
    rows = collect(include_mining=args.mining)
    if not rows:
        print("\nNO observations collected (sources unreachable; nothing written).")
    else:
        n_series = len({o["series_id"] for o in rows})
        dates = sorted(o["date"] for o in rows)
        metrics = sorted({o["metric"] for o in rows})
        print(f"\nrows: {len(rows)}   series: {n_series}   date span: {dates[0]} … {dates[-1]}")
        print(f"metrics ({len(metrics)}): {', '.join(metrics)}")
        print("\n3 sample observations:")
        step = max(1, len(rows) // 3)
        for o in rows[::step][:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
