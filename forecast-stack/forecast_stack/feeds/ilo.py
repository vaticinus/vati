"""ILOSTAT (ILO Department of Statistics) — keyless labour-market OUTCOMES collector.

A self-contained KEYLESS collector for Vati's data layer. The ILO's public data API
(https://rplumber.ilo.org/data/indicator/) is open, no API key, and returns dated annual
observations as CSV. It is the live machine endpoint behind ilostat.ilo.org (the static
bulk_download index URLs the ILO advertises are frequently moved/404; this API is the stable,
documented programmatic path and is what works keyless today).

This module builds out the **terminal "outcomes" spine layer** — realized labour-market /
social outcomes — WIDE: many labour indicators (unemployment, employment-to-population, labour
force participation, youth NEET, informal employment, sectoral employment, working poverty,
wages, hours, labour-income share, productivity growth) across ~25 countries. Each
(indicator × country) becomes its own series_id with a row-level `metric`, `domain`, `unit`
and `title`. Annual, dated to reference year-end. These are the slow, authoritative structural
series that reliably fire the changepoint detector and ground the calibration moat.

Per-indicator dimension handling. ILO indicators carry breakdown dimensions (sex, age band,
economic sector, etc.). Rather than pin every dimension server-side (brittle — codes differ per
indicator), each spec declares the SLICE it wants as `classif1` category PREFIXES, and normalize
filters to that slice client-side. `sex=SEX_T` (both sexes) is pinned server-side where the
indicator has a sex dimension. An indicator that returns nothing for a country (e.g. the US does
not report informal employment) is simply sparse — logged and skipped, never filled.

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every observation carries its REAL reference date — ILO reports annual figures, so `date` =
    December 31 of the indicator's reference year (the point in time the year's value is knowable).
    Nothing is synthesized, backfilled, or interpolated: a row with a non-numeric value is DROPPED,
    never filled, so the jsonl is only ground-truth reported points.
  • Values are PUBLISHED WITH A LAG and revised across vintages (many of the latest years are ILO
    modelled estimates). As a forecasting signal this is a LAG / CONFIRMATION channel: it confirms
    a structural labour-market shift AFTER it has happened — it does not lead it. It grounds the
    social/labour outcome layer as a slow kill-metric baseline, not an early-warning.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, metric:str, domain:str, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/ilo.py
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
ILO_BASE = "https://rplumber.ilo.org/data/indicator/"
OUT_PATH = DATA_DIR / "feeds" / "ilo.jsonl"
MIN_REFRESH_FRACTION = 0.8

CUTOFF_YEAR = 2026  # cap at the data cutoff: drop any year strictly after this (modelled projections)
TIME_FROM = 2005    # ~two decades of annual history per (indicator, country, slice)

# Labour / social OUTCOME indicators. Each spec:
#   id      — the ILOSTAT indicator code
#   metric  — row-level metric tag (specific) written onto every observation
#   domain  — "labour" or "social"
#   unit    — human-readable unit
#   title   — series title stem
#   has_sex — whether the indicator carries a sex dimension (pin SEX_T server-side)
#   slices  — list of (classif_prefix, slice_label). For each matching classif1 category we mint a
#             distinct series suffixed with slice_label. None/[] → indicator has no classif1 (one
#             series per country). classif_prefix is matched by exact code OR by exact prefix.
# All codes + dimension categories verified against the live API before inclusion.
INDICATORS: list[dict] = [
    # ── headline labour-market rates (national reported) ──────────────────────────────────────
    {
        "id": "UNE_DEAP_SEX_AGE_RT_A", "metric": "unemployment_rate", "domain": "labour",
        "unit": "% of labour force", "title": "Unemployment rate", "has_sex": True,
        "slices": [("AGE_YTHADULT_YGE15", "15+"), ("AGE_YTHADULT_Y15-24", "youth_15-24")],
    },
    {
        "id": "EMP_DWAP_SEX_AGE_RT_A", "metric": "employment_to_population_ratio", "domain": "labour",
        "unit": "% of working-age population", "title": "Employment-to-population ratio", "has_sex": True,
        "slices": [("AGE_YTHADULT_YGE15", "15+")],
    },
    {
        "id": "EAP_DWAP_SEX_AGE_RT_A", "metric": "labour_force_participation_rate", "domain": "labour",
        "unit": "% of working-age population", "title": "Labour force participation rate", "has_sex": True,
        "slices": [("AGE_YTHADULT_YGE15", "15+")],
    },
    {
        "id": "EIP_NEET_SEX_AGE_RT_A", "metric": "youth_neet_rate", "domain": "social",
        "unit": "% of youth", "title": "Youth not in employment, education or training (NEET)",
        "has_sex": True, "slices": [("AGE_5YRBANDS_YGE15", "15+")],
    },
    # ── ILO modelled estimates (broad country coverage; latest years modelled) ────────────────
    {
        "id": "UNE_2EAP_SEX_AGE_RT_A", "metric": "unemployment_rate_modelled", "domain": "labour",
        "unit": "% of labour force", "title": "Unemployment rate (ILO modelled)", "has_sex": True,
        "slices": [("AGE_YTHADULT_YGE15", "15+"), ("AGE_YTHADULT_Y15-24", "youth_15-24")],
    },
    {
        "id": "EMP_2EMP_SEX_AGE_NB_A", "metric": "employment_total_modelled", "domain": "labour",
        "unit": "thousands of persons", "title": "Total employment (ILO modelled)", "has_sex": True,
        "slices": [("AGE_YTHADULT_YGE15", "15+")],
    },
    # ── structural transition signals (informality, sectoral shift, working poverty) ──────────
    {
        "id": "EMP_NIFL_SEX_RT_A", "metric": "informal_employment_rate", "domain": "social",
        "unit": "% of total employment", "title": "Informal employment rate", "has_sex": True,
        "slices": None,
    },
    {
        "id": "EMP_TEMP_SEX_ECO_NB_A", "metric": "employment_by_sector", "domain": "labour",
        "unit": "thousands of persons", "title": "Employment by economic sector", "has_sex": True,
        "slices": [("ECO_SECTOR_AGR", "agriculture"), ("ECO_SECTOR_IND", "industry"),
                   ("ECO_SECTOR_SER", "services")],
    },
    {
        "id": "SDG_0111_SEX_AGE_RT_A", "metric": "working_poverty_rate", "domain": "social",
        "unit": "% of employed below US$3 PPP", "title": "Working poverty rate", "has_sex": True,
        "slices": [("AGE_YTHADULT_YGE15", "15+")],
    },
    # ── pay, hours, productivity, distribution ────────────────────────────────────────────────
    {
        "id": "HOW_TEMP_SEX_NB_A", "metric": "weekly_hours_worked", "domain": "labour",
        "unit": "hours per week", "title": "Average weekly hours actually worked", "has_sex": True,
        "slices": None,
    },
    {
        "id": "EAR_EHRA_SEX_NB_A", "metric": "hourly_earnings_lcu", "domain": "labour",
        "unit": "local currency per hour", "title": "Average hourly earnings of employees",
        "has_sex": True, "slices": None,
    },
    {
        "id": "SDG_1041_NOC_RT_A", "metric": "labour_income_share", "domain": "social",
        "unit": "% of GDP", "title": "Labour income share of GDP", "has_sex": False, "slices": None,
    },
    {
        "id": "SDG_0821_NOC_RT_A", "metric": "labour_productivity_growth", "domain": "labour",
        "unit": "% annual growth (output per worker)", "title": "Labour productivity growth",
        "has_sex": False, "slices": None,
    },
]

SEX = "SEX_T"  # both sexes — pinned server-side where the indicator has a sex dimension

# ~25 reporters spanning advanced + emerging economies (ISO-3 as the ILO expects them).
REPORTERS: list[tuple[str, str]] = [
    ("USA", "United States"), ("CHN", "China"), ("JPN", "Japan"), ("DEU", "Germany"),
    ("IND", "India"), ("GBR", "United Kingdom"), ("FRA", "France"), ("ITA", "Italy"),
    ("BRA", "Brazil"), ("CAN", "Canada"), ("RUS", "Russian Federation"), ("KOR", "Korea, Rep."),
    ("ESP", "Spain"), ("AUS", "Australia"), ("MEX", "Mexico"), ("IDN", "Indonesia"),
    ("TUR", "Türkiye"), ("NLD", "Netherlands"), ("SAU", "Saudi Arabia"), ("ZAF", "South Africa"),
    ("POL", "Poland"), ("NGA", "Nigeria"), ("ARG", "Argentina"), ("EGY", "Egypt"),
    ("VNM", "Viet Nam"), ("BGD", "Bangladesh"),
]


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
    # Drop legacy-format rows (pre-`metric` schema, with bare un-sliced series_ids). This collector
    # now fully supersedes those indicators with sliced, metric-tagged series, so keeping the old
    # shape would leave orphaned duplicate series and a mixed row schema.
    merged = {_row_key(row): row for row in old
              if _row_key(row) != ("", "") and "metric" in row}
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


def _fetch_csv(url: str, *, retries: int = 2) -> str | None:
    """GET a keyless ILO CSV URL → decoded text. Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 keyless public endpoint
                return resp.read().decode("utf-8-sig", "replace")
        except Exception:  # noqa: BLE001 — network/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None


def fetch_indicator(spec: dict, iso3: str, *, retries: int = 2) -> list[dict]:
    """Fetch one (indicator, reporter) series as CSV → list of RAW dict rows. Pins SEX_T server-side
    when the indicator has a sex dimension; the classif1 slice is filtered client-side in normalize.
    Empty/garbled payload → []."""
    params: dict[str, object] = {"id": spec["id"], "ref_area": iso3,
                                 "timefrom": TIME_FROM, "format": ".csv"}
    if spec.get("has_sex"):
        params["sex"] = SEX
    url = ILO_BASE + "?" + urllib.parse.urlencode(params)
    text = _fetch_csv(url, retries=retries)
    if not text:
        return []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "obs_value" not in reader.fieldnames:
        return []  # not the expected CSV envelope (e.g. {"error":...}) → stop, never fake
    return list(reader)


def _slice_match(classif: str, prefix: str) -> bool:
    """A classif1 category matches a requested slice if it equals the prefix exactly, or starts with
    it followed by a separator. (ECO_SECTOR_AGR must NOT match ECO_SECTOR_AGR_* sub-codes, but the
    sector totals we want are leaf codes, so exact-or-boundary is the safe rule.)"""
    return classif == prefix


def normalize(spec: dict, iso3: str, raw_rows: list[dict]) -> list[dict]:
    """RAW ILO CSV rows → normalized Vati observations, one series per requested slice. `date` =
    Dec-31 of the reference year. value cast to float; non-numeric / post-cutoff rows DROPPED."""
    base = f"ilo:{spec['id']}:{iso3}"
    slices = spec.get("slices")
    out: list[dict] = []
    # (series_id) -> set of years already taken, to guard duplicate vintages within one slice
    seen: dict[str, set[str]] = {}
    for r in raw_rows:
        year = (r.get("time") or "").strip()
        if len(year) != 4 or not year.isdigit():
            continue
        if int(year) > CUTOFF_YEAR:
            continue  # leak/projection guard: never carry a value dated past the cutoff
        raw_val = (r.get("obs_value") or "").strip()
        if not raw_val:
            continue
        try:
            value = float(raw_val)
        except (TypeError, ValueError):
            continue

        classif = (r.get("classif1") or "").strip()
        if slices:
            label = None
            for prefix, slice_label in slices:
                if _slice_match(classif, prefix):
                    label = slice_label
                    break
            if label is None:
                continue  # this classif1 category is not a requested slice → skip
            series_id = f"{base}:{label}"
            title = f"{spec['title']} ({label.replace('_', ' ')}) — {iso3}"
        else:
            series_id = base
            title = f"{spec['title']} — {iso3}"

        ytaken = seen.setdefault(series_id, set())
        if year in ytaken:
            continue
        ytaken.add(year)
        out.append({
            "series_id": series_id,
            "date": f"{year}-12-31",          # REAL reference year, reported point-in-time (annual)
            "value": value,
            "metric": spec["metric"],
            "domain": spec["domain"],
            "unit": spec["unit"],
            "title": title,
        })
    out.sort(key=lambda o: (o["series_id"], o["date"]))
    return out


def collect(*, log=print) -> list[dict]:
    """Fetch all (indicator × reporter) series keyless, normalize, write the jsonl. Returns the
    list of observations actually written. $0. Never fabricates: a series that fails to fetch or
    returns no data is logged and skipped, not filled."""
    all_obs: list[dict] = []
    n_series = 0
    n_skipped = 0
    for spec in INDICATORS:
        ind_obs = 0
        for iso3, _name in REPORTERS:
            raw = fetch_indicator(spec, iso3)
            obs = normalize(spec, iso3, raw)
            if not obs:
                n_skipped += 1
                continue
            all_obs.extend(obs)
            ind_obs += len(obs)
            n_series += len({o["series_id"] for o in obs})
            time.sleep(0.25)
        log(f"  {spec['domain']:<7} {spec['metric']:<33} {spec['id']:<24} {ind_obs:>5} obs")
    log(f"\n  {n_series} series · {len(all_obs)} obs · {n_skipped} (indicator,country) slices empty/skipped")

    existing = _existing_rows()
    if not all_obs:
        if existing:
            log(f"\nno observations fetched; preserved existing {len(existing)} rows at {OUT_PATH}")
        return []
    if existing and len(all_obs) < MIN_REFRESH_FRACTION * len(existing):
        log(
            f"\npartial ILO refresh fetched {len(all_obs)} rows, below "
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
        print("\nNO observations collected — ILO API unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
