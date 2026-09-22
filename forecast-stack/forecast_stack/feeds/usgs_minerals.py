"""USGS Mineral Commodity Summaries — keyless critical-minerals collector (the MINERALS pillar).

The USGS National Minerals Information Center publishes the Mineral Commodity Summaries (MCS) as
open, KEYLESS ScienceBase data releases. Each commodity is a catalog item carrying a
`*_salient.csv` file: one row per Year with US mine/refinery PRODUCTION, IMPORTS, EXPORTS,
CONSUMPTION, PRICE and the Net-Import-Reliance (NIR) percentage. We resolve each commodity's CSV
dynamically from the ScienceBase item JSON (https://www.sciencebase.gov/catalog/item/<id>?format=json
— no API key, no auth, just an HTTPS GET), then download the salient CSV and normalize the supply
columns to Vati observations.

This grounds the SUPPLY-ELASTICITY / minerals spine: physical US production and net-import-reliance
across the full MCS commodity set (lithium, cobalt, nickel, copper, rare earths, graphite, gallium,
germanium, manganese, vanadium, tungsten, antimony, tin, tantalum, platinum-group, aluminum, zinc,
and the rest of the ~85 commodities the MCS 2025 release exposes). Annual structural series like
these are exactly what the changepoint detector eats — a supply-concentration or import-reliance
regime shift shows up as a level break.

We go WIDE by AUTO-DISCOVERING the supply columns rather than hand-coding each metal. Per commodity
salient CSV:
  • mineral_production — every `USprod_*` column (one production series per stream, e.g.
    primary/secondary/mine/refinery), unit decoded from the column suffix (kt/t/kg/mmt).
  • net_import_reliance — every `NIR_*` column (NIR is definitionally a percentage).
Each (commodity × column) is its own series_id with a row-level `metric`, `domain="minerals"`,
`unit`, and `title`. Per-commodity failures (item JSON unreachable, CSV missing, no numeric rows)
are logged and skipped; one bad metal never sinks the run.

Leak discipline (matches forecast_stack/feeds/world_bank.py + ember.py):
  • Every observation carries its REAL reporting year. MCS reports annual figures, so `date` =
    December 31 of the reference Year (the point in time the year's value is knowable). Nothing is
    synthesized, backfilled, or interpolated.
  • USGS uses non-numeric flags for unavailable figures: 'W' (withheld to avoid disclosing company
    data), 'NA' (not available), 'E' (estimate-only with no number), '>50' / '<25' (inequalities),
    blank. EVERY such non-numeric cell is DROPPED, never coerced or filled — the jsonl carries only
    genuine reported numbers.

LEAK-CLASS — LAG / CONFIRMATION. The MCS is an authoritative annual stocktake published the YEAR
AFTER the reference year (MCS2025 covers data through 2024) and is revised across vintages. US mine
production, imports and consumption are a RECORD of physical flows that already happened; the NIR%
confirms a dependency that is already structurally in place. So this CONFIRMS a critical-minerals
shift (a supply-concentration, an import-reliance regime) after the fact and grounds a slow,
authoritative baseline / kill-metric — it does NOT run ahead of the priced outcome. The leading
channel for the same theme is the China MOFCOM export-control decree feed, not this annual census.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit:str, metric:str, domain:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/usgs_minerals.py
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
SB_ITEM = "https://www.sciencebase.gov/catalog/item/{item_id}?format=json"
OUT_PATH = DATA_DIR / "feeds" / "usgs_minerals.jsonl"
REQUEST_TIMEOUT_S = 12
FETCH_RETRIES = 1
MIN_REFRESH_FRACTION = 0.9
DOMAIN = "minerals"

# USGS non-numeric flags that mean "no genuine number here" → DROP, never coerce (leak discipline).
# 'W'=withheld, 'NA'=not available, 'E'=estimate flag w/o value, blanks, and any inequality (>,<).
_NULL_FLAGS = {"", "w", "na", "nd", "e", "--", "-", "(s)", "xx"}

# Column-suffix → unit. USGS encodes the unit as the last underscore-token of the salient column
# (USprod_Primary_kt → 'kt'). NIR columns are always a percentage regardless of their suffix.
_UNIT_BY_SUFFIX = {
    "kt": "thousand metric tons",
    "mmt": "million metric tons",
    "t": "metric tons",
    "kg": "kg",
    "g": "grams",
    "mct": "million carats",
    "ct": "carats",
    # price / unit-value suffixes (USGS encodes the price denomination as the trailing token)
    "dt": "USD per metric ton",
    "dto": "USD per metric ton",
    "dkt": "USD per thousand metric tons",
    "dkg": "USD per kg",
    "dlb": "USD per pound",
    "dca": "USD per carat",
    "dct": "USD per carat",
    "ctslb": "US cents per pound",
    "ctlb": "US cents per pound",
    "cm": "USD per cubic meter",
    "mcm": "USD per thousand cubic meters",
    # counts
    "num": "count",
    "pct": "%",
}

# Measure-column prefix → (metric, human label). Auto-discovered from the salient header so we
# capture the FULL row — not just production/NIR but imports, exports, consumption, price, stocks
# and employment, exactly the supply/dependency signals the changepoint detector needs. Tried in
# order; the first prefix a column starts with wins.
_MEASURE_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("USprod", "mineral_production", "US production"),
    ("Production", "production", "production"),
    ("Imports", "imports", "imports"),
    ("Exports", "exports", "exports"),
    ("Consump", "consumption", "consumption"),
    ("Price", "price", "price"),
    ("Stocks", "stocks", "yearend stocks"),
    ("Reserves", "reserves", "reserves"),
    ("Recycling", "recycling", "recycling"),
    ("Employment", "employment", "employment"),
    ("NIR", "net_import_reliance", "net import reliance"),
)

# The full MCS 2025 "Salient Commodity Data Release" commodity set (ScienceBase item ids). Resolved
# once from the parent collection (parentId=6793e234d34e72688d6b71e7); pinned here so the run does not
# depend on a live catalog crawl. Each commodity's salient-CSV URL is still resolved dynamically from
# its item JSON (the on-disk URL hash changes between vintages; the item id is stable).
COMMODITIES: list[dict] = [
    {"name": "Aluminum", "item_id": "6797f997d34ea8c18376e11b"},
    {"name": "Antimony", "item_id": "6797f9b7d34ea8c18376e11e"},
    {"name": "Arsenic", "item_id": "6797f9cbd34ea8c18376e120"},
    {"name": "Asbestos", "item_id": "6797f9eed34ea8c18376e124"},
    {"name": "Barite", "item_id": "6797fa05d34ea8c18376e127"},
    {"name": "Bauxite and Alumina", "item_id": "6797fa1ad34ea8c18376e129"},
    {"name": "Beryllium", "item_id": "6797fa40d34ea8c18376e12d"},
    {"name": "Bismuth", "item_id": "6797fa59d34ea8c18376e131"},
    {"name": "Boron", "item_id": "6797fa70d34ea8c18376e134"},
    {"name": "Bromine", "item_id": "6797fa8ed34ea8c18376e141"},
    {"name": "Cadmium", "item_id": "6797faa3d34ea8c18376e145"},
    {"name": "Cement", "item_id": "6797fab8d34ea8c18376e148"},
    {"name": "Chromium", "item_id": "6797fad1d34ea8c18376e14e"},
    {"name": "Clays", "item_id": "6797faebd34ea8c18376e153"},
    {"name": "Cobalt", "item_id": "6797fb00d34ea8c18376e159"},
    {"name": "Copper", "item_id": "6797fba5d34ea8c18376e15d"},
    {"name": "Diamond (Industrial)", "item_id": "6797fbbcd34ea8c18376e160"},
    {"name": "Diatomite", "item_id": "6797fbdcd34ea8c18376e169"},
    {"name": "Feldspar and Nepheline Syenite", "item_id": "6797fbf1d34ea8c18376e16e"},
    {"name": "Fluorspar", "item_id": "6797fd4ad34ea8c18376e188"},
    {"name": "Gallium", "item_id": "6797fd60d34ea8c18376e18b"},
    {"name": "Garnet", "item_id": "6797fd78d34ea8c18376e190"},
    {"name": "Gemstones", "item_id": "6797fd93d34ea8c18376e195"},
    {"name": "Germanium", "item_id": "6797fdaed34ea8c18376e19d"},
    {"name": "Gold", "item_id": "6797fdc7d34ea8c18376e1a0"},
    {"name": "Graphite (Natural)", "item_id": "6797fe3fd34ea8c18376e1a3"},
    {"name": "Gypsum", "item_id": "6797fe57d34ea8c18376e1ab"},
    {"name": "Helium", "item_id": "6797fe71d34ea8c18376e1af"},
    {"name": "Indium", "item_id": "6797fea8d34ea8c18376e1b4"},
    {"name": "Iodine", "item_id": "6797fec1d34ea8c18376e1b7"},
    {"name": "Iron and Steel", "item_id": "6797fd33d34ea8c18376e185"},
    {"name": "Iron and Steel Scrap", "item_id": "6797fcfed34ea8c18376e17f"},
    {"name": "Iron and Steel Slag", "item_id": "6797fd1bd34ea8c18376e182"},
    {"name": "Iron Ore", "item_id": "6797fc06d34ea8c18376e173"},
    {"name": "Iron Oxide Pigments", "item_id": "6797fcdfd34ea8c18376e17a"},
    {"name": "Kyanite", "item_id": "6797ff13d34ea8c18376e1bd"},
    {"name": "Lead", "item_id": "6797ff2fd34ea8c18376e1c4"},
    {"name": "Lime", "item_id": "6797ff43d34ea8c18376e1c7"},
    {"name": "Lithium", "item_id": "6797ff62d34ea8c18376e1cb"},
    {"name": "Magnesium Compounds", "item_id": "6797ffe2d34ea8c18376e1da"},
    {"name": "Magnesium Metal", "item_id": "6797ffffd34ea8c18376e1e5"},
    {"name": "Manganese", "item_id": "6797ff7fd34ea8c18376e1ce"},
    {"name": "Mercury", "item_id": "6797ffc8d34ea8c18376e1d6"},
    {"name": "Mica (Natural)", "item_id": "6798001ad34ea8c18376e1e8"},
    {"name": "Molybdenum", "item_id": "67980035d34ea8c18376e1ec"},
    {"name": "Nickel", "item_id": "6798004ed34ea8c18376e1ef"},
    {"name": "Niobium (Columbium)", "item_id": "6798006ad34ea8c18376e1f2"},
    {"name": "Nitrogen (Fixed)-Ammonia", "item_id": "67980088d34ea8c18376e1f6"},
    {"name": "Peat", "item_id": "67980182d34ea8c18376e205"},
    {"name": "Perlite", "item_id": "6798019dd34ea8c18376e209"},
    {"name": "Phosphate Rock", "item_id": "679801b8d34ea8c18376e20c"},
    {"name": "Platinum-Group Metals", "item_id": "679801d3d34ea8c18376e213"},
    {"name": "Potash", "item_id": "679801ead34ea8c18376e21b"},
    {"name": "Pumice and Pumicite", "item_id": "67980208d34ea8c18376e224"},
    {"name": "Quartz Crystal", "item_id": "6798f072d34ea8c18376e7ef"},
    {"name": "Rare Earths", "item_id": "6798f088d34ea8c18376e7f9"},
    {"name": "Rhenium", "item_id": "6798f0a3d34ea8c18376e7ff"},
    {"name": "Salt", "item_id": "6798f0bad34ea8c18376e803"},
    {"name": "Sand and Gravel (Construction)", "item_id": "6798f0e1d34ea8c18376e80d"},
    {"name": "Sand and Gravel (Industrial)", "item_id": "6798f101d34ea8c18376e813"},
    {"name": "Scandium", "item_id": "6798f11cd34ea8c18376e81e"},
    {"name": "Selenium", "item_id": "6798f162d34ea8c18376e829"},
    {"name": "Silicon", "item_id": "6798f1e0d34ea8c18376e839"},
    {"name": "Silver", "item_id": "6798f17ad34ea8c18376e82f"},
    {"name": "Soda Ash", "item_id": "6798f1f9d34ea8c18376e83f"},
    {"name": "Stone (Crushed)", "item_id": "6798f22dd34ea8c18376e842"},
    {"name": "Stone (Dimension)", "item_id": "6798f249d34ea8c18376e847"},
    {"name": "Strontium", "item_id": "6798f26fd34ea8c18376e84b"},
    {"name": "Sulfur", "item_id": "6798f3e7d34ea8c18376e861"},
    {"name": "Talc and Pyrophyllite", "item_id": "6798f4a6d34ea8c18376e86a"},
    {"name": "Tantalum", "item_id": "6798f4ddd34ea8c18376e86d"},
    {"name": "Tellurium", "item_id": "6798f4f9d34ea8c18376e870"},
    {"name": "Thallium", "item_id": "6798f50dd34ea8c18376e878"},
    {"name": "Thorium", "item_id": "6798f531d34ea8c18376e87d"},
    {"name": "Tin", "item_id": "6798f55fd34ea8c18376e886"},
    {"name": "Titanium and Titanium Dioxide", "item_id": "6798f5bad34ea8c18376e889"},
    {"name": "Titanium Mineral Concentrates", "item_id": "6798f548d34ea8c18376e880"},
    {"name": "Tungsten", "item_id": "6798f5d9d34ea8c18376e88d"},
    {"name": "Vanadium", "item_id": "6798f5f9d34ea8c18376e891"},
    {"name": "Vermiculite", "item_id": "6798f60fd34ea8c18376e896"},
    {"name": "Yttrium", "item_id": "6798f626d34ea8c18376e899"},
    {"name": "Zeolites (Natural)", "item_id": "6798f63fd34ea8c18376e89d"},
    {"name": "Zinc", "item_id": "6798f654d34ea8c18376e8a5"},
    {"name": "Zirconium and Hafnium", "item_id": "6798f669d34ea8c18376e8a7"},
]


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


def resolve_salient_csv_url(item_id: str) -> str | None:
    """Resolve a commodity's salient-CSV download URL from its ScienceBase item JSON.

    Done dynamically (not hard-coded) because the file's on-disk URL hash changes between MCS
    vintages, but the item id and the `*_salient.csv` naming convention are stable.
    """
    raw = _fetch_bytes(SB_ITEM.format(item_id=item_id))
    if raw is None:
        return None
    try:
        item = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    for f in item.get("files", []) or []:
        name = (f.get("name") or "").lower()
        if name.endswith("_salient.csv") and f.get("url"):
            return f["url"]
    # fallback: any csv attached to the item
    for f in item.get("files", []) or []:
        if (f.get("name") or "").lower().endswith(".csv") and f.get("url"):
            return f["url"]
    return None


def _to_float(raw: str) -> float | None:
    """A salient-CSV cell → float, or None if it is a USGS non-numeric flag (DROP, never coerce).

    Drops 'W'/'NA'/'E'/blank and any inequality ('>50','<25') — those are not genuine numbers.
    """
    s = (raw or "").strip().replace(",", "")
    if s.lower() in _NULL_FLAGS:
        return None
    if s.startswith((">", "<", "~")):  # inequality / approximation flag, not a reported number
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _unit_for_column(column: str) -> str:
    """Decode the physical unit from a salient column's trailing underscore-token suffix."""
    suffix = column.rsplit("_", 1)[-1].lower()
    return _UNIT_BY_SUFFIX.get(suffix, "unit")


# Trailing tokens that are units/percentage flags, never a stream descriptor → strip from labels.
_UNIT_TOKENS = set(_UNIT_BY_SUFFIX) | {"pct", "ct", "num", "dt", "dlb", "dkg", "dto", "dkt",
                                       "ctslb", "cm", "mcm", "mct", "to", "cts"}


def _stream_label(column: str, prefix: str) -> str:
    """Human label for a production/NIR stream, e.g. 'USprod_Secondary-old_kt' → 'secondary old'.

    Drops the trailing unit/percentage token (kt/t/kg/pct/...) wherever it sits, including when it
    is the column's only token (so 'NIR_pct' → '' rather than 'pct').
    """
    body = column[len(prefix):].strip("_")     # strip the 'USprod' / 'NIR' prefix
    parts = [p for p in body.split("_") if p]
    if parts and parts[-1].lower() in _UNIT_TOKENS:
        parts = parts[:-1]
    return " ".join(parts).replace("-", " ").strip().lower()


def _discover_measures(header: list[str]) -> list[tuple[str, str, str, str]]:
    """From a salient CSV header, auto-discover EVERY measure column we surface.

    Returns (column, metric, unit, label) for every production / imports / exports / consumption /
    price / stocks / reserves / recycling / employment / NIR column (per _MEASURE_PREFIXES). NIR is
    always a percentage; units otherwise decode from the trailing suffix token. Non-measure columns
    (DataSource, Commodity, Year) are skipped.
    """
    measures: list[tuple[str, str, str, str]] = []
    for col in header:
        c = col.strip()
        if c in ("DataSource", "Commodity", "Year") or not c:
            continue
        for prefix, metric, metric_label in _MEASURE_PREFIXES:
            if c.startswith(prefix):
                stream = _stream_label(c, prefix)
                unit = "% net import reliance" if metric == "net_import_reliance" else _unit_for_column(c)
                label = f"{metric_label}{f' ({stream})' if stream else ''}"
                measures.append((c, metric, unit, label))
                break
    return measures


def collect_commodity(spec: dict, *, log=print) -> list[dict]:
    """Resolve, download and normalize one commodity's salient CSV → Vati observations.

    Auto-discovers the `USprod_*` (production) and `NIR_*` (net-import-reliance) columns, then emits
    one observation per (Year × column) that carries a genuine number. `date` = Dec-31 of the
    reported Year. Values/years are verbatim — no fill, backfill, or interpolation. A commodity that
    fails to resolve / download / parse is logged and skipped (returns []), never sinking the run.
    """
    try:
        url = resolve_salient_csv_url(spec["item_id"])
        if url is None:
            url = spec.get("fallback_url")
            if url:
                log(f"  ~ {spec['name']}: using pinned ScienceBase CSV URL fallback")
            else:
                log(f"  - skip {spec['name']} (could not resolve salient CSV — item JSON unreachable)")
                return []
        raw = _fetch_bytes(url)
        if raw is None:
            log(f"  - skip {spec['name']} (CSV download failed)")
            return []

        text = raw.decode("utf-8-sig", "replace")  # utf-8-sig strips the BOM seen on the header
        reader = csv.DictReader(io.StringIO(text))
        header = [h for h in (reader.fieldnames or []) if h]
        measures = _discover_measures(header)
        if not measures:
            log(f"  - {spec['name']}: no measure columns in salient CSV (skipped)")
            return []
        slug = (
            spec["name"].lower()
            .replace("(", "").replace(")", "")
            .replace(",", "").replace("&", "and")
            .replace("/", "_").replace(" ", "_")
        )
        obs: list[dict] = []
        for row in reader:
            year = (row.get("Year") or "").strip()
            if len(year) != 4 or not year.isdigit():
                continue
            # DataSource carries the MCS vintage (e.g. 'MCS2025'); the report is released early in
            # that calendar year, so the figure for `year` became publicly knowable no earlier than
            # the vintage year — leak-safe publication stamp.
            vintage = (row.get("DataSource") or "").strip().upper()
            published_at = f"{vintage[3:]}-01-15" if vintage.startswith("MCS") and vintage[3:].isdigit() else f"{int(year) + 1}-01-15"
            for column, metric, unit, label in measures:
                value = _to_float(row.get(column, ""))
                if value is None:  # withheld / NA / inequality → DROP, never fabricate
                    continue
                obs.append({
                    "series_id": f"usgs_mcs:{slug}:{column}",
                    "date": f"{year}-12-31",   # REAL reference year, reported point-in-time (annual)
                    "value": value,
                    "unit": unit,
                    "metric": metric,
                    "domain": DOMAIN,
                    "title": f"USGS MCS {spec['name']} — {label}",
                    "published_at": published_at,
                    "source_url": url,
                })
    except Exception as exc:  # noqa: BLE001 — one bad commodity must never sink the run
        log(f"  - skip {spec['name']} (error: {exc})")
        return []

    obs.sort(key=lambda o: (o["series_id"], o["date"]))
    if obs:
        years = sorted({o["date"][:4] for o in obs})
        log(f"  + {spec['name']:<32} {years[0]}–{years[-1]}  {len(obs):4d} obs "
            f"across {len({o['series_id'] for o in obs})} series")
    else:
        log(f"  - {spec['name']}: no numeric observations (all cells withheld/NA?)")
    return obs


def collect(*, log=print) -> list[dict]:
    """Fetch all commodities keyless, normalize, write the jsonl. Returns observations written.

    $0. Never fabricates: a commodity that fails to resolve/download is logged and skipped.
    """
    all_obs: list[dict] = []
    for spec in COMMODITIES:
        all_obs.extend(collect_commodity(spec, log=log))
        time.sleep(0.2)

    existing = _existing_line_count()
    if not all_obs:
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(all_obs) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial refresh fetched {len(all_obs)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(all_obs)
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    print("USGS Mineral Commodity Summaries (keyless, ScienceBase data releases):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — ScienceBase unreachable this run (no data written).")
    else:
        n_series = len({o["series_id"] for o in observations})
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}  ({n_series} series)")
