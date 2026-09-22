"""USGS deep mineral history + by-country concentration — keyless (the MINERALS DEPTH feed).

`usgs_minerals.py` already pulls the *recent* US Mineral Commodity Summaries salient series (the last
few years of US production / net-import-reliance). This feed adds the two things that file misses — the
DEPTH the minerals spine actually needs:

  1) DS-140 LONG HISTORY — USGS "Historical Statistics for Mineral and Material Commodities in the
     United States" (Data Series 140). One workbook per commodity, often 1900→present, columns for US
     production, imports, exports, apparent/estimated consumption, unit value (nominal $ and constant
     98$), and WORLD production. A century-long structural baseline: regime breaks (the shale-era
     copper price reset, the post-2010 rare-earth squeeze, the lithium demand inflection) all live here
     as level shifts the changepoint detector eats. Hosted keyless as .xlsx on USGS's prod S3
     (d9-wret.s3.us-west-2.amazonaws.com); we resolve each commodity's real URL off its usgs.gov media
     page and parse the workbook with the Python stdlib (zipfile + xml — NO new dependency).

  2) MCS WORLD PRODUCTION & RESERVES BY COUNTRY — the single most important geopolitical-constraint
     signal: "China 68% of rare-earth mine production", "DRC ~74% of cobalt", "China ~60% of gallium".
     One keyless CSV (the MCS 2025 "World Production, Capacity, and Reserves" ScienceBase data release)
     carries commodity × country × {production 2023, production est. 2024, capacity, reserves 2024}.
     We emit one series per (commodity × country × {production, capacity, reserves}) at its REAL
     reference year, so concentration (a country's share of the world total) is directly computable.

LEAK DISCIPLINE (matches usgs_minerals.py / ember.py):
  • Every observation carries its REAL reference date. DS-140 rows → Dec-31 of the reported Year. MCS
    world rows → Dec-31 of the column's reference year (PROD_2023→2023-12-31, PROD_EST_2024 &
    RESERVES_2024→2024-12-31). The MCS release also has a `published_at` (the 2025 vintage publish).
    Nothing is synthesized, backfilled, or interpolated.
  • USGS non-numeric flags ('W' withheld, 'NA', 'NA' estimate flags, blanks, 'XX', inequalities) are
    DROPPED, never coerced or filled. The jsonl carries only genuine reported numbers.

LEAK-CLASS — LAG / CONFIRMATION / BASELINE. DS-140 is a century-deep authoritative RECORD of physical
flows that already happened; the MCS world tables are an annual stocktake published the year after the
reference year. Both CONFIRM a structural state (a supply-concentration, an import dependency) and
ground a slow authoritative baseline / kill-metric — they do NOT run ahead of a priced outcome. The
LEADING channel for the same theme is the China MOFCOM export-control decree feed, not this census.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit, metric, domain, title, source_url, published_at}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/usgs_historical.py
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.request
import zipfile
from pathlib import Path
from forecast_stack.config import DATA_DIR
from xml.etree import ElementTree as ET

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OUT_PATH = DATA_DIR / "feeds" / "usgs_historical.jsonl"
REQUEST_TIMEOUT_S = 30
FETCH_RETRIES = 2
MIN_REFRESH_FRACTION = 0.9
DOMAIN = "minerals"

# ── DS-140 (long history) ───────────────────────────────────────────────────────────────────────
# The usgs.gov media landing page for each commodity. We scrape the embedded d9-wret S3 .xlsx URL
# off the page HTML (the year suffix, e.g. ds140-lithium-2021.xlsx, varies per commodity vintage, so
# we resolve it dynamically rather than guessing). The 2nd tuple field is the page-path slug; a few
# commodities use a non-standard page slug (the '-2017-update' pages) so the field may instead be a
# FULL media-page URL (detected by an 'http' prefix), used verbatim.
DS140_MEDIA = "https://www.usgs.gov/media/files/{slug}-historical-statistics-data-series-140"
DS140_S3_RE = re.compile(r"https://d9-wret\.s3\.us-west-2\.amazonaws\.com/[^\"'\s]*?ds140-[^\"'\s]*?\.xlsx")

# Commodity slugs that publish a DS-140 workbook (USGS NMIC historical-statistics index). Energy/
# strategic + bulk; one bad slug just skips. Names are the human title; slug is the URL slug.
DS140_COMMODITIES: list[tuple[str, str]] = [
    ("Aluminum", "aluminum"),
    ("Antimony", "antimony"),
    ("Arsenic", "arsenic"),
    ("Asbestos", "asbestos"),
    ("Barite", "barite"),
    ("Bauxite and Alumina", "bauxite-and-alumina"),
    ("Beryllium", "beryllium"),
    ("Bismuth", "bismuth"),
    ("Boron", "boron"),
    ("Bromine", "bromine"),
    ("Cadmium", "cadmium"),
    ("Cement", "cement"),
    ("Cesium", "https://www.usgs.gov/media/files/cesium-historical-statistics-data-series-140-2017-update"),
    ("Chromium", "chromium"),
    ("Clays", "clays"),
    ("Cobalt", "cobalt"),
    ("Copper", "copper"),
    ("Diamond (Industrial)", "industrial-diamond"),
    ("Diatomite", "diatomite"),
    ("Feldspar", "feldspar"),
    ("Fluorspar", "fluorspar"),
    ("Gallium", "gallium"),
    ("Garnet (Industrial)", "garnet"),
    ("Gemstones", "gemstones"),
    ("Germanium", "germanium"),
    ("Gold", "gold"),
    ("Graphite (Natural)", "graphite"),
    ("Gypsum", "gypsum"),
    ("Hafnium", "hafnium"),
    ("Helium", "helium"),
    ("Indium", "indium"),
    ("Iodine", "iodine"),
    ("Iron and Steel", "iron-and-steel"),
    ("Iron and Steel Scrap", "iron-and-steel-scrap"),
    ("Iron and Steel Slag", "iron-and-steel-slag"),
    ("Iron Ore", "iron-ore"),
    ("Iron Oxide Pigments", "iron-oxide-pigments"),
    ("Kyanite", "kyanite-and-related-minerals"),
    ("Lead", "lead"),
    ("Lime", "lime"),
    ("Lithium", "lithium"),
    ("Magnesium Compounds", "magnesium-compounds"),
    ("Magnesium Metal", "magnesium-metal"),
    ("Manganese", "manganese"),
    ("Mercury", "mercury"),
    ("Mica", "mica"),
    ("Molybdenum", "molybdenum"),
    ("Nickel", "nickel"),
    ("Niobium (Columbium)", "niobium"),
    ("Nitrogen", "nitrogen"),
    ("Peat", "peat"),
    ("Perlite", "perlite"),
    ("Phosphate Rock", "phosphate-rock"),
    ("Platinum-Group Metals", "platinum-group-metals"),
    ("Potash", "potash"),
    ("Pumice and Pumicite", "pumice-and-pumicite"),
    ("Rare Earths", "rare-earths"),
    ("Rhenium", "rhenium"),
    ("Salt", "salt"),
    ("Sand and Gravel (Construction)", "construction-sand-and-gravel"),
    ("Sand and Gravel (Industrial)", "industrial-sand-and-gravel"),
    ("Selenium", "selenium"),
    ("Silicon", "silicon"),
    ("Silver", "silver"),
    ("Soda Ash", "soda-ash"),
    ("Stone (Crushed)", "crushed-stone"),
    ("Stone (Dimension)", "dimension-stone"),
    ("Strontium", "strontium"),
    ("Sulfur", "sulfur"),
    ("Talc and Pyrophyllite", "talc"),
    ("Tantalum", "tantalum"),
    ("Tellurium", "tellurium"),
    ("Thallium", "https://www.usgs.gov/media/files/thallium-historical-statistics-data-series-140-2017-update"),
    ("Thorium", "thorium"),
    ("Tin", "tin"),
    ("Titanium Dioxide Pigment", "titanium-dioxide-pigments"),
    ("Titanium Metal", "titanium-metal"),
    ("Titanium Mineral Concentrates", "titanium-mineral-concentrates"),
    ("Tungsten", "tungsten"),
    ("Vanadium", "vanadium"),
    ("Vermiculite", "vermiculite"),
    ("Wollastonite", "wollastonite"),
    ("Zinc", "zinc"),
    ("Zirconium Mineral Concentrates", "zirconium"),
]

# ── MCS world production / reserves by country (the concentration signal) ─────────────────────────
# ScienceBase item: "Mineral Commodity Summaries 2025 - World Production, Capacity, and Reserves".
# A single keyless CSV: commodity × country × {PROD_2023, PROD_EST_2024, CAP_2023, CAP_EST_2024,
# RESERVES_2024}. The item id is stable; the on-disk file URL is resolved from the item JSON.
MCS_WORLD_ITEM = "6798fd34d34ea8c18376e8ee"
SB_ITEM = "https://www.sciencebase.gov/catalog/item/{item_id}?format=json"
# The MCS 2025 release (covers the 2024 reference year) was published Jan/2025 — the publish vintage.
MCS_WORLD_PUBLISHED = "2025-01-31"
MCS_WORLD_LANDING = "https://data.usgs.gov/datacatalog/data/USGS:6798fd34d34ea8c18376e8ee"

# MCS world CSV column → (metric, reference-year). PROD_2023 is the firm 2023 figure; the *_EST_2024
# columns and RESERVES_2024 are the latest (2024) reference year.
_MCS_COLS: list[tuple[str, str, str]] = [
    ("PROD_2023", "world_mine_production", "2023"),
    ("PROD_EST_ 2024", "world_mine_production", "2024"),  # note: source header has a stray space
    ("CAP_2023", "world_capacity", "2023"),
    ("CAP_EST_ 2024", "world_capacity", "2024"),
    ("RESERVES_2024", "world_reserves", "2024"),
]

# USGS non-numeric flags → DROP (leak discipline). Anything that isn't a genuine reported number.
_NULL_FLAGS = {"", "w", "na", "nd", "e", "--", "-", "(s)", "xx", "nan", "n/a", "small", "—"}

# ── shared IO ─────────────────────────────────────────────────────────────────────────────────────
XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


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
                time.sleep(2.5 * (attempt + 1))
                continue
            return None


def _to_float(raw: str) -> float | None:
    """A cell → float, or None if it is a USGS non-numeric flag (DROP, never coerce/interpolate)."""
    s = (raw or "").strip().replace(",", "")
    if s.lower() in _NULL_FLAGS:
        return None
    if s.startswith((">", "<", "~")):  # inequality / approximation flag, not a reported number
        return None
    # trailing reference-superscript digits ('5,400e' style estimate letters) → strip a single flag char
    if s and s[-1].isalpha():
        s = s[:-1].strip()
    try:
        return float(s)
    except ValueError:
        return None


# ── DS-140 long history ───────────────────────────────────────────────────────────────────────────
def _load_xlsx_rows(raw: bytes) -> list[dict[str, str]]:
    """Parse a single-sheet .xlsx (stdlib only) → list of {col_letter: value} dicts, row order kept."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    strings: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall(f"{XL_NS}si"):
            strings.append("".join(t.text or "" for t in si.iter(f"{XL_NS}t")))
    # first worksheet
    sheet_name = next((n for n in z.namelist() if re.match(r"xl/worksheets/sheet1\.xml$", n)), None)
    if sheet_name is None:
        sheet_name = next((n for n in z.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")), None)
    if sheet_name is None:
        return []
    root = ET.fromstring(z.read(sheet_name))
    rows: list[dict[str, str]] = []
    for row in root.iter(f"{XL_NS}row"):
        cells: dict[str, str] = {}
        for c in row.findall(f"{XL_NS}c"):
            ref = c.get("r") or ""
            m = re.match(r"[A-Z]+", ref)
            if not m:
                continue
            col = m.group()
            t = c.get("t")
            v = c.find(f"{XL_NS}v")
            val = ""
            if v is not None and v.text is not None:
                val = strings[int(v.text)] if t == "s" and v.text.isdigit() else v.text
            else:
                isn = c.find(f"{XL_NS}is")
                if isn is not None:
                    val = "".join(tt.text or "" for tt in isn.iter(f"{XL_NS}t"))
            cells[col] = val
        if cells:
            rows.append(cells)
    return rows


# DS-140 header-text → (metric, unit-hint). The header labels are stable across commodities.
def _classify_ds140_column(header: str, default_unit: str) -> tuple[str, str, str] | None:
    """Map a DS-140 column header to (metric, unit, clean-label), or None to skip (e.g. the Year col).

    Units: most quantity columns inherit the workbook's bracketed unit note (default_unit). Unit-value
    columns are $/unit (nominal or constant 98$); we read the $ note from the header itself.
    """
    h = header.strip()
    hl = h.lower()
    if not h or hl == "year":
        return None
    # money columns carry their own unit in the header, e.g. "Unit value ($/t)" / "(98$/t)"
    if "unit value" in hl or hl.startswith("price") or "$/" in hl:
        unit = "98$ per unit" if "98$" in hl else "$ per unit"
        m = re.search(r"\(([^)]*\$[^)]*)\)", h)
        if m:
            unit = m.group(1)
        return ("unit_value", unit, h)
    metric_map = [
        ("world production", "world_production"),
        ("world mine production", "world_production"),
        ("production", "us_production"),
        ("mine production", "us_production"),
        ("imports", "us_imports"),
        ("exports", "us_exports"),
        ("apparent consumption", "us_apparent_consumption"),
        ("estimated consumption", "us_consumption"),
        ("reported consumption", "us_reported_consumption"),
        ("consumption", "us_consumption"),
        ("recycl", "us_recycling"),
        ("stocks", "us_stocks"),
        ("price", "unit_value"),
    ]
    for needle, metric in metric_map:
        if needle in hl:
            return (metric, default_unit, h)
    # an unrecognized quantity column → keep it as a generic measured quantity (still a real number)
    return ("quantity", default_unit, h)


def collect_ds140(spec: tuple[str, str], *, log=print) -> list[dict]:
    """Resolve, download and normalize one commodity's DS-140 workbook → observations.

    Dynamically resolves the keyless S3 .xlsx URL off the commodity's usgs.gov media page, parses the
    workbook with the stdlib, finds the 'Year' header row, infers each column's metric+unit from its
    header text, and emits one observation per (Year × column) carrying a genuine number. `date` =
    Dec-31 of the reported Year; `published_at` = the workbook's 'Last modification' date if present.
    """
    name, slug = spec
    try:
        page_url = slug if slug.startswith("http") else DS140_MEDIA.format(slug=slug)
        page = _fetch_bytes(page_url, timeout=REQUEST_TIMEOUT_S)
        if page is None:
            log(f"  - skip {name} (media page unreachable)")
            return []
        m = DS140_S3_RE.search(page.decode("utf-8", "replace"))
        if not m:
            log(f"  - skip {name} (no DS-140 .xlsx link on media page)")
            return []
        xlsx_url = m.group(0)
        raw = _fetch_bytes(xlsx_url)
        if raw is None or not raw.startswith(b"PK"):
            log(f"  - skip {name} (xlsx download failed / not a workbook)")
            return []
        rows = _load_xlsx_rows(raw)
        if not rows:
            log(f"  - skip {name} (empty workbook)")
            return []

        # workbook-level metadata: unit note (bracketed), last-modification date
        default_unit = "unit"
        published_at = None
        header_idx = None
        for i, r in enumerate(rows[:12]):
            a = (r.get("A") or "").strip()
            if a.startswith("[") and "]" in a:               # "[All values are in metric tons (t) cobalt content unless otherwise noted]"
                note = a.strip("[]").strip()
                # drop the boilerplate lead-in/tail, leaving the canonical unit, e.g. "metric tons (t) cobalt content"
                note = re.sub(r"^all (?:values|quantities)(?: are)? in\s+", "", note, flags=re.I)
                note = re.sub(r"\s+unless otherwise noted\.?$", "", note, flags=re.I).strip(" .")
                default_unit = note or "unit"
            if a.lower().startswith("last modification"):
                dm = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", a)
                if dm:
                    try:
                        from datetime import datetime
                        published_at = datetime.strptime(dm.group(1), "%B %d, %Y").date().isoformat()
                    except ValueError:
                        published_at = None
            if a.strip().lower() == "year":
                header_idx = i
        if header_idx is None:
            log(f"  - skip {name} (no 'Year' header row found)")
            return []

        header_row = rows[header_idx]
        # classify every non-Year column once
        cols: dict[str, tuple[str, str, str]] = {}
        for col_letter, htext in header_row.items():
            cls = _classify_ds140_column(htext, default_unit)
            if cls is not None:
                cols[col_letter] = cls

        slug_id = _slugify(name)
        obs: list[dict] = []
        for r in rows[header_idx + 1:]:
            year = (r.get("A") or "").strip()
            if len(year) != 4 or not year.isdigit():       # footnote / blank rows → skip
                continue
            for col_letter, (metric, unit, label) in cols.items():
                val = _to_float(r.get(col_letter, ""))
                if val is None:                            # withheld / NA / inequality → DROP
                    continue
                obs.append({
                    "series_id": f"usgs_ds140:{slug_id}:{metric}:{col_letter}",
                    "date": f"{year}-12-31",
                    "value": val,
                    "unit": unit,
                    "metric": metric,
                    "domain": DOMAIN,
                    "title": f"USGS DS-140 {name} — {label}",
                    "source_url": xlsx_url,
                    "published_at": published_at or f"{year}-12-31",
                })
    except Exception as exc:  # noqa: BLE001 — one bad commodity must never sink the run
        log(f"  - skip {name} (error: {exc})")
        return []

    obs.sort(key=lambda o: (o["series_id"], o["date"]))
    if obs:
        years = sorted({o["date"][:4] for o in obs})
        log(f"  + {name:<34} {years[0]}–{years[-1]}  {len(obs):5d} obs "
            f"/ {len({o['series_id'] for o in obs})} series")
    else:
        log(f"  - {name}: no numeric observations (all withheld/NA?)")
    return obs


# ── MCS world production / reserves by country ───────────────────────────────────────────────────
def _resolve_mcs_world_csv() -> str | None:
    """Resolve the keyless MCS world-data CSV URL from the ScienceBase item JSON (stable item id)."""
    raw = _fetch_bytes(SB_ITEM.format(item_id=MCS_WORLD_ITEM))
    if raw is None:
        return None
    try:
        item = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    for f in item.get("files", []) or []:
        if (f.get("name") or "").lower().endswith(".csv") and f.get("url"):
            return f["url"]
    return None


def _slugify(s: str) -> str:
    s = s.lower().replace("&", "and")
    s = re.sub(r"[(),/]", " ", s)
    return re.sub(r"\s+", "_", s.strip())


def collect_mcs_world(*, log=print) -> list[dict]:
    """Download + normalize the MCS world-production/capacity/reserves-by-country CSV → observations.

    Emits one observation per (commodity × country × column) that carries a genuine number, at the
    column's REAL reference year (PROD_2023→2023, *_EST_2024/RESERVES_2024→2024). This is the
    concentration signal: each country's share of the world commodity total is directly computable.
    """
    url = _resolve_mcs_world_csv()
    if url is None:
        log("  - MCS world: could not resolve CSV URL (ScienceBase item unreachable)")
        return []
    raw = _fetch_bytes(url)
    if raw is None:
        log("  - MCS world: CSV download failed")
        return []
    text = raw.decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(text))
    # normalize headers (strip stray spaces) → map back to canonical names
    field_lookup = {(h or "").strip(): h for h in (reader.fieldnames or [])}

    def cell(row: dict, key: str) -> str:
        # tolerate the stray-space variants in the source header (e.g. 'PROD_EST_ 2024')
        for cand in (key, key.replace("_ ", "_"), key.replace("_", "_ ")):
            real = field_lookup.get(cand)
            if real is not None:
                return row.get(real, "")
        # last resort: case/space-insensitive match
        norm = key.replace(" ", "").lower()
        for h, real in field_lookup.items():
            if h.replace(" ", "").lower() == norm:
                return row.get(real, "")
        return ""

    obs: list[dict] = []
    for row in reader:
        commodity = (row.get("COMMODITY") or row.get(field_lookup.get("COMMODITY", "COMMODITY"), "") or "").strip()
        country = (cell(row, "COUNTRY") or "").strip()
        ctype = (cell(row, "TYPE") or "").strip()
        unit = (cell(row, "UNIT_MEAS") or "unit").strip() or "unit"
        if not commodity or not country:
            continue
        c_slug = _slugify(commodity)
        co_slug = _slugify(country)
        type_tag = _slugify(ctype)[:48] if ctype else ""
        for col, metric, year in _MCS_COLS:
            val = _to_float(cell(row, col))
            if val is None:                                # withheld / NA / blank → DROP
                continue
            sid = f"usgs_mcs_world:{c_slug}:{co_slug}:{metric}:{year}"
            if type_tag:
                sid = f"usgs_mcs_world:{c_slug}:{co_slug}:{type_tag}:{metric}:{year}"
            title = f"USGS MCS2025 {commodity} — {metric.replace('_', ' ')} ({country})"
            if ctype:
                title += f" [{ctype}]"
            obs.append({
                "series_id": sid,
                "date": f"{year}-12-31",
                "value": val,
                "unit": unit,
                "metric": metric,
                "domain": DOMAIN,
                "title": title,
                "source_url": MCS_WORLD_LANDING,
                "published_at": MCS_WORLD_PUBLISHED,
            })
    obs.sort(key=lambda o: (o["series_id"], o["date"]))
    n_comm = len({o["title"].split(" — ")[0] for o in obs})
    n_country = len({o["series_id"].split(":")[2] for o in obs})
    log(f"  + MCS world by-country: {len(obs)} obs / "
        f"{len({o['series_id'] for o in obs})} series across ~{n_comm} commodity-blocks, {n_country} countries")
    return obs


def collect(*, log=print) -> list[dict]:
    """Fetch DS-140 long history (all commodities) + MCS world-by-country, normalize, write jsonl.

    $0, keyless. Never fabricates: any commodity/source that fails to resolve is logged and skipped.
    Atomic write with a partial-refresh guard (preserves a good prior file if this run came up short).
    """
    all_obs: list[dict] = []

    log("MCS world production / capacity / reserves by country (the concentration signal):")
    all_obs.extend(collect_mcs_world(log=log))

    log("\nDS-140 historical statistics (long per-commodity history, 1900→present):")
    for spec in DS140_COMMODITIES:
        all_obs.extend(collect_ds140(spec, log=log))
        time.sleep(1.2)  # usgs.gov media pages throttle (403) under fast access; pace politely

    existing = _existing_line_count()
    if not all_obs:
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(all_obs) < int(existing * MIN_REFRESH_FRACTION):
        log(f"\npartial refresh fetched {len(all_obs)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}")
        return []
    _write_jsonl_atomic(all_obs)
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    print("USGS deep mineral history + by-country concentration (keyless):\n")
    observations = collect()
    if not observations:
        print("\nNO observations collected — USGS sources unreachable this run (no data written).")
    else:
        n_series = len({o["series_id"] for o in observations})
        dates = sorted({o["date"] for o in observations})
        print(f"\nrows: {len(observations)}   series: {n_series}   span: {dates[0]} → {dates[-1]}")
        print("\n3 sample rows:")
        # show one MCS-world row and two DS-140 rows for flavor
        mcs = next((o for o in observations if o["series_id"].startswith("usgs_mcs_world")), None)
        ds = [o for o in observations if o["series_id"].startswith("usgs_ds140")]
        sample = [x for x in (mcs, ds[0] if ds else None, ds[len(ds) // 2] if ds else None) if x]
        for o in sample[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
