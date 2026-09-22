"""World Bank Indicators API — keyless macro/energy/trade time-series collector.

A self-contained KEYLESS collector for Vati's data layer. The World Bank's Indicators API
(https://api.worldbank.org/v2/country/<ISO>/indicator/<CODE>?format=json) is open, no API key,
returns dated annual observations as `[meta, rows]` JSON and is paginated. This module fetches a
WIDE basket of major reporters (World aggregate + ~24 major economies) across ~50 indicators spanning
macro / industry / energy / emissions / R&D-IP / trade / digital / agriculture / demographics /
health / labor, normalizes each row to a Vati observation, and writes every REAL dated observation to
data/feeds/world_bank.jsonl. Each (indicator × country) is its OWN series (`series_id = wb_<CODE>_<ISO>`).
Annual structural series (unlike daily prices or smooth curves) reliably fire the changepoint detector,
which is why this collector builds the data layer's "outcome" spine.

Leak discipline (matches engine/pillars/forces.py + power.py):
  • Every observation carries its REAL reporting date — the World Bank reports annual figures, so
    `as_of` = December 31 of the indicator's reference year (the point in time the year's value is
    knowable). Nothing is synthesized, backfilled, or interpolated: a null `value` row is DROPPED,
    never filled, so the jsonl is only ground-truth reported points.
  • The series is point-in-time honest but PUBLISHED WITH A LAG and REVISED. National accounts and
    energy/trade aggregates land ~1 year (or more) after the reference year and are revised for
    several vintages afterward. So as a forecasting signal this is a LAG / CONFIRMATION channel: it
    confirms a structural shift (a GDP regime, an energy-mix transition, a trade-intensity move)
    AFTER it has already happened and been priced — it does not lead it. It grounds the macro pillar
    as a slow, authoritative baseline / kill-metric, not an early-warning.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', as_of:'YYYY-MM-DD', value:float, metric:str, domain:str, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/world_bank.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
WB_BASE = "https://api.worldbank.org/v2"
OUT_PATH = DATA_DIR / "feeds" / "world_bank.jsonl"
MIN_REFRESH_FRACTION = 0.8

# WIDE basket of REAL, currently-live WDI indicator codes spanning real-economy OUTCOMES and
# STRUCTURE. Annual structural series (unlike daily prices or smooth curves) reliably fire the
# changepoint detector and build the data layer's "outcome" spine. Each row carries a descriptive
# `metric` (indicator family, auditable), a `domain` (pillar/layer), `unit`, and `title`.
#
# Every code below was verified live against the API on 2026-06-20 (curl spot-checks). The collector
# already drops any indicator that 404s gracefully, but we ship only known-live codes. Archived/404
# codes intentionally EXCLUDED: EG.ELC.PROD.KH, EN.ATM.CO2E.KT/.PC (retired CDIAC series; replaced by
# the AR5 GHG accounts), IP.TMK.TOTL/.RESD (deleted; trademarks now via IP.TMK.RSCT direct-resident).
INDICATORS: list[dict] = [
    # ── Macro: output, income, structure ────────────────────────────────────────────────────────
    {"code": "NY.GDP.MKTP.CD", "metric": "macro_indicator", "domain": "macro",
     "unit": "current US$", "title": "GDP (current US$)"},
    {"code": "NY.GDP.PCAP.CD", "metric": "macro_indicator", "domain": "macro",
     "unit": "current US$", "title": "GDP per capita (current US$)"},
    {"code": "NY.GDP.PCAP.KD.ZG", "metric": "macro_indicator", "domain": "macro",
     "unit": "% annual growth", "title": "GDP per capita growth (annual %)"},
    {"code": "NY.GNP.MKTP.CD", "metric": "macro_indicator", "domain": "macro",
     "unit": "current US$", "title": "GNI, Atlas method (current US$)"},
    {"code": "NY.GNP.PCAP.CD", "metric": "macro_indicator", "domain": "macro",
     "unit": "current US$", "title": "GNI per capita, Atlas method (current US$)"},
    {"code": "NE.GDI.TOTL.ZS", "metric": "macro_indicator", "domain": "macro",
     "unit": "% of GDP", "title": "Gross capital formation (% of GDP)"},
    {"code": "NE.CON.GOVT.ZS", "metric": "macro_indicator", "domain": "macro",
     "unit": "% of GDP", "title": "General government final consumption (% of GDP)"},
    {"code": "BX.KLT.DINV.CD.WD", "metric": "fdi_inflow", "domain": "capital",
     "unit": "current US$", "title": "Foreign direct investment, net inflows (BoP, current US$)"},
    # ── Industry / structure of the economy ─────────────────────────────────────────────────────
    {"code": "NV.IND.MANF.CD", "metric": "manufacturing_value_added", "domain": "industry",
     "unit": "current US$", "title": "Manufacturing, value added (current US$)"},
    {"code": "NV.IND.MANF.ZS", "metric": "manufacturing_value_added", "domain": "industry",
     "unit": "% of GDP", "title": "Manufacturing, value added (% of GDP)"},
    {"code": "NV.AGR.TOTL.ZS", "metric": "sector_value_added", "domain": "industry",
     "unit": "% of GDP", "title": "Agriculture, forestry & fishing, value added (% of GDP)"},
    {"code": "NV.SRV.TOTL.ZS", "metric": "sector_value_added", "domain": "industry",
     "unit": "% of GDP", "title": "Services, value added (% of GDP)"},
    {"code": "NV.MNF.TECH.ZS.UN", "metric": "hightech_manufacturing", "domain": "industry",
     "unit": "% of manufacturing value added", "title": "Medium- & high-tech manufacturing VA (%)"},
    # ── Energy: output, mix, access, intensity ──────────────────────────────────────────────────
    {"code": "EG.ELC.RNEW.ZS", "metric": "energy_output", "domain": "energy",
     "unit": "% of total electricity output", "title": "Renewable electricity output (% of total)"},
    {"code": "EG.ELC.FOSL.ZS", "metric": "energy_output", "domain": "energy",
     "unit": "% of total electricity output", "title": "Electricity from fossil fuels (% of total)"},
    {"code": "EG.ELC.NUCL.ZS", "metric": "energy_output", "domain": "energy",
     "unit": "% of total electricity output", "title": "Electricity from nuclear (% of total)"},
    {"code": "EG.ELC.HYRO.ZS", "metric": "energy_output", "domain": "energy",
     "unit": "% of total electricity output", "title": "Electricity from hydro (% of total)"},
    {"code": "EG.FEC.RNEW.ZS", "metric": "energy_output", "domain": "energy",
     "unit": "% of final energy consumption", "title": "Renewable energy consumption (% of total final)"},
    {"code": "EG.ELC.ACCS.ZS", "metric": "energy_access", "domain": "energy",
     "unit": "% of population", "title": "Access to electricity (% of population)"},
    {"code": "EG.ELC.ACCS.RU.ZS", "metric": "energy_access", "domain": "energy",
     "unit": "% of rural population", "title": "Access to electricity, rural (% of rural pop.)"},
    {"code": "EG.USE.PCAP.KG.OE", "metric": "energy_use", "domain": "energy",
     "unit": "kg of oil equivalent per capita", "title": "Energy use per capita (kg oil eq.)"},
    {"code": "EG.IMP.CONS.ZS", "metric": "energy_dependency", "domain": "energy",
     "unit": "% of energy use", "title": "Energy imports, net (% of energy use)"},
    # ── Emissions / environment ─────────────────────────────────────────────────────────────────
    {"code": "EN.GHG.CO2.MT.CE.AR5", "metric": "co2_emissions", "domain": "environment",
     "unit": "Mt CO2 equivalent", "title": "CO2 emissions, total excl. LULUCF (Mt CO2e, AR5)"},
    {"code": "AG.LND.FRST.ZS", "metric": "land_use", "domain": "environment",
     "unit": "% of land area", "title": "Forest area (% of land area)"},
    {"code": "ER.H2O.FWTL.ZS", "metric": "water_stress", "domain": "environment",
     "unit": "% of internal resources", "title": "Annual freshwater withdrawals (% of internal)"},
    # ── R&D, innovation, IP ─────────────────────────────────────────────────────────────────────
    {"code": "GB.XPD.RSDV.GD.ZS", "metric": "rnd_expenditure", "domain": "innovation",
     "unit": "% of GDP", "title": "Research & development expenditure (% of GDP)"},
    {"code": "SP.POP.SCIE.RD.P6", "metric": "rnd_personnel", "domain": "innovation",
     "unit": "per million people", "title": "Researchers in R&D (per million people)"},
    {"code": "IP.PAT.RESD", "metric": "patent_applications", "domain": "innovation",
     "unit": "applications", "title": "Patent applications, residents"},
    {"code": "IP.PAT.NRES", "metric": "patent_applications", "domain": "innovation",
     "unit": "applications", "title": "Patent applications, nonresidents"},
    {"code": "IP.TMK.RSCT", "metric": "trademark_applications", "domain": "innovation",
     "unit": "applications", "title": "Trademark applications, direct resident"},
    {"code": "IP.JRN.ARTC.SC", "metric": "scientific_output", "domain": "innovation",
     "unit": "articles", "title": "Scientific & technical journal articles"},
    # ── Trade: high-tech, intensity, flows ──────────────────────────────────────────────────────
    {"code": "TX.VAL.TECH.MF.ZS", "metric": "hightech_exports", "domain": "trade",
     "unit": "% of manufactured exports", "title": "High-technology exports (% of manuf. exports)"},
    {"code": "TX.VAL.MANF.ZS.UN", "metric": "manufactured_exports", "domain": "trade",
     "unit": "% of merchandise exports", "title": "Manufactures exports (% of merchandise exports)"},
    {"code": "BX.GSR.CCIS.ZS", "metric": "ict_exports", "domain": "trade",
     "unit": "% of service exports", "title": "ICT service exports (% of service exports, BoP)"},
    {"code": "NE.EXP.GNFS.CD", "metric": "trade_flow", "domain": "trade",
     "unit": "current US$", "title": "Exports of goods and services (current US$)"},
    {"code": "NE.IMP.GNFS.CD", "metric": "trade_flow", "domain": "trade",
     "unit": "current US$", "title": "Imports of goods and services (current US$)"},
    {"code": "NE.TRD.GNFS.ZS", "metric": "trade_intensity", "domain": "trade",
     "unit": "% of GDP", "title": "Trade (% of GDP)"},
    {"code": "TG.VAL.TOTL.GD.ZS", "metric": "trade_intensity", "domain": "trade",
     "unit": "% of GDP", "title": "Merchandise trade (% of GDP)"},
    # ── Connectivity / digital ──────────────────────────────────────────────────────────────────
    {"code": "IT.NET.USER.ZS", "metric": "internet_penetration", "domain": "digital",
     "unit": "% of population", "title": "Individuals using the Internet (% of population)"},
    {"code": "IT.CEL.SETS.P2", "metric": "mobile_penetration", "domain": "digital",
     "unit": "per 100 people", "title": "Mobile cellular subscriptions (per 100 people)"},
    # ── Agriculture / food ──────────────────────────────────────────────────────────────────────
    {"code": "AG.PRD.CROP.XD", "metric": "agricultural_output", "domain": "agriculture",
     "unit": "index (2014-2016=100)", "title": "Crop production index"},
    {"code": "AG.YLD.CREL.KG", "metric": "agricultural_yield", "domain": "agriculture",
     "unit": "kg per hectare", "title": "Cereal yield (kg per hectare)"},
    {"code": "AG.LND.AGRI.ZS", "metric": "land_use", "domain": "agriculture",
     "unit": "% of land area", "title": "Agricultural land (% of land area)"},
    # ── Demographics / urbanization / labor / health ────────────────────────────────────────────
    {"code": "SP.POP.TOTL", "metric": "demographics", "domain": "demographics",
     "unit": "people", "title": "Population, total"},
    {"code": "SP.POP.GROW", "metric": "demographics", "domain": "demographics",
     "unit": "% annual growth", "title": "Population growth (annual %)"},
    {"code": "SP.URB.TOTL.IN.ZS", "metric": "urbanization", "domain": "demographics",
     "unit": "% of total population", "title": "Urban population (% of total)"},
    {"code": "SP.RUR.TOTL.ZS", "metric": "urbanization", "domain": "demographics",
     "unit": "% of total population", "title": "Rural population (% of total)"},
    {"code": "EN.POP.DNST", "metric": "demographics", "domain": "demographics",
     "unit": "people per sq. km of land", "title": "Population density"},
    {"code": "SP.DYN.LE00.IN", "metric": "health_outcome", "domain": "health",
     "unit": "years", "title": "Life expectancy at birth, total (years)"},
    {"code": "SH.XPD.CHEX.GD.ZS", "metric": "health_expenditure", "domain": "health",
     "unit": "% of GDP", "title": "Current health expenditure (% of GDP)"},
    {"code": "SE.TER.ENRR", "metric": "education", "domain": "human_capital",
     "unit": "% gross", "title": "School enrollment, tertiary (% gross)"},
    {"code": "SL.TLF.TOTL.IN", "metric": "labor_force", "domain": "labor",
     "unit": "people", "title": "Labor force, total"},
    {"code": "SL.UEM.TOTL.ZS", "metric": "unemployment", "domain": "labor",
     "unit": "% of total labor force", "title": "Unemployment, total (% of labor force, modeled ILO)"},
]

# Global basket: WLD aggregate (structural baseline) + the G20 single economies + key emerging
# producers (de-US-skews the macro pillar — "other countries too"). ISO-3 as the API expects.
REPORTERS: list[tuple[str, str]] = [
    ("WLD", "World"),
    ("USA", "United States"), ("CHN", "China"), ("JPN", "Japan"), ("DEU", "Germany"),
    ("IND", "India"), ("GBR", "United Kingdom"), ("FRA", "France"), ("BRA", "Brazil"),
    ("ITA", "Italy"), ("CAN", "Canada"), ("KOR", "South Korea"), ("RUS", "Russia"),
    ("AUS", "Australia"), ("MEX", "Mexico"), ("IDN", "Indonesia"), ("SAU", "Saudi Arabia"),
    ("TUR", "Turkey"), ("ZAF", "South Africa"), ("NGA", "Nigeria"), ("VNM", "Vietnam"),
    ("ARE", "United Arab Emirates"), ("TWN", "Taiwan"), ("NLD", "Netherlands"),
]

PER_PAGE = 1000  # full annual history fits in one page (≤ ~65 years per indicator)


def _row_key(row: dict) -> tuple[str, str]:
    return str(row.get("series_id") or ""), str(row.get("date") or "")


def _existing_rows() -> list[dict]:
    if not OUT_PATH.exists():
        return []
    rows: list[dict] = []
    with OUT_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _row_key(row) != ("", ""):
                rows.append(row)
    return rows


def _merge_rows(old: list[dict], new: list[dict]) -> list[dict]:
    merged = {_row_key(row): row for row in old if _row_key(row) != ("", "")}
    for row in new:
        key = _row_key(row)
        if key != ("", ""):
            merged[key] = row
    return sorted(merged.values(), key=lambda r: (_row_key(r)[0], _row_key(r)[1]))


def _write_rows(rows: list[dict]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_json(url: str, *, retries: int = 2):
    """GET a keyless World Bank API URL → parsed JSON. Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 keyless public endpoint
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — network/parse/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None


def fetch_indicator(code: str, iso3: str, *, retries: int = 2) -> list[dict]:
    """Fetch one (indicator, reporter) series, paginated, → list of RAW World Bank rows with a
    non-null value. The API returns `[meta, rows]`; an invalid/archived code returns `[{"message":...}]`."""
    rows: list[dict] = []
    page = 1
    while True:
        params = {"format": "json", "per_page": PER_PAGE, "page": page}
        url = f"{WB_BASE}/country/{urllib.parse.quote(iso3)}/indicator/{urllib.parse.quote(code)}?{urllib.parse.urlencode(params)}"
        data = _fetch_json(url, retries=retries)
        # Valid payload is a 2-element list [meta, data]; anything else (error envelope / None) → stop.
        if not isinstance(data, list) or len(data) != 2 or not isinstance(data[1], list):
            break
        meta, page_rows = data
        for r in page_rows:
            if r.get("value") is not None:  # DROP nulls — never backfill/interpolate (leak discipline)
                rows.append(r)
        total_pages = (meta or {}).get("pages", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.3)
    return rows


def normalize(code: str, iso3: str, spec: dict, raw_rows: list[dict]) -> list[dict]:
    """RAW World Bank rows → normalized Vati observations. Each (indicator × country) becomes its own
    series (`series_id = wb_<CODE>_<ISO>`). `as_of` (date) = Dec-31 of the reference year (the point in
    time the annual value is knowable). value cast to float; metric/domain/unit/title from the spec."""
    series_id = f"wb_{code}_{iso3}"
    out: list[dict] = []
    for r in raw_rows:
        year = (r.get("date") or "").strip()
        if len(year) != 4 or not year.isdigit():
            continue
        try:
            value = float(r["value"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "series_id": series_id,
            "date": f"{year}-12-31",          # REAL reference year, reported point-in-time (annual)
            "as_of": f"{year}-12-31",         # leak discipline: knowable-at, never fetched_at
            "value": value,
            "metric": spec["metric"],
            "domain": spec["domain"],
            "unit": spec["unit"],
            "title": f"{spec['title']} — {iso3}",
        })
    # chronological order, latest last
    out.sort(key=lambda o: o["date"])
    return out


def collect(*, log=print) -> list[dict]:
    """Fetch all (indicator × reporter) series keyless, normalize, write the jsonl. Returns the
    list of observations actually written. $0. Never fabricates: a series that fails to fetch is
    logged and skipped, not filled."""
    all_obs: list[dict] = []
    for spec in INDICATORS:
        code = spec["code"]
        for iso3, name in REPORTERS:
            raw = fetch_indicator(code, iso3)
            obs = normalize(code, iso3, spec, raw)
            if not obs:
                log(f"  - skip {code} / {iso3} (no dated observations returned)")
                continue
            all_obs.extend(obs)
            log(f"  + {spec['domain']:<13} {code:<20} {iso3}  "
                f"{obs[0]['date'][:4]}–{obs[-1]['date'][:4]}  {len(obs)} obs")
            time.sleep(0.3)

    existing = _existing_rows()
    if not all_obs:
        if existing:
            log(f"\nno observations fetched; preserved existing {len(existing)} rows at {OUT_PATH}")
        return []
    if existing and len(all_obs) < MIN_REFRESH_FRACTION * len(existing):
        log(
            f"\npartial World Bank refresh fetched {len(all_obs)} rows, below "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {len(existing)}; preserved {OUT_PATH}"
        )
        return []

    merged = _merge_rows(existing, all_obs)
    _write_rows(merged)
    retained = len(merged) - len(all_obs)
    suffix = f" ({retained} prior rows retained)" if retained else ""
    log(f"\nwrote {len(merged)} observations → {OUT_PATH}{suffix}")
    return merged


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — World Bank API unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        # report the true count straight from the written file
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
