"""EIA energy-supply collector — the US/global ENERGY pillar's structural feed (keyless).

The U.S. Energy Information Administration (EIA) is the authoritative open source for US and global
energy *supply*: electricity net-generation by source, primary-energy production & consumption by
fuel, crude-oil and natural-gas production, reserves, storage, and the benchmark spot prices (WTI,
Brent, Henry Hub). EIA's v2 JSON API needs a free key, but the SAME underlying series are published
KEYLESS as the v2 BULK download manifests — a zip per dataset at https://api.eia.gov/bulk/<DS>.zip
(also catalogued at https://www.eia.gov/opendata/bulk/). No key, no auth, just an HTTPS GET. Each
unzipped `.txt` is JSON-lines: one object per SERIES carrying {series_id, name, units, f (frequency),
last_updated, data:[[period, value], ...]}. We stream those manifests and keep the structural
national/global series that ground the energy-supply layer.

Datasets pulled (all keyless https://api.eia.gov/bulk/...):
  • ELEC  — Electricity. US net generation by source (coal, natural gas, nuclear, wind, all solar,
            hydro, geothermal, biomass, petroleum, all-fuels), all-sectors and electric-power totals,
            monthly + quarterly. This is the grid-mix transition as physical MWh.
  • TOTAL — Total Energy / Monthly Energy Review. US total primary-energy production & consumption,
            production/consumption by fuel (fossil / coal / natural gas / petroleum / nuclear /
            renewables), monthly + annual.
  • PET   — Petroleum. US field production of crude oil (national + Lower-48 + Alaska), and the
            WTI (Cushing) and Brent benchmark spot prices, monthly/weekly/daily.
  • NG    — Natural Gas. US dry-gas production / marketed production / consumption, working
            underground storage, and the Henry Hub benchmark spot price.
  • INTL  — International Energy Statistics. Per-COUNTRY (ISO3) annual crude-oil + dry-natural-gas
            production and total primary energy production/consumption, for the world's major
            producers — the global supply picture, not just the US.

Why this feeds the ENERGY-SUPPLY layer (and how it is leak-safe):
  • Net generation by source and primary-energy production by fuel are the *physical state* of the
    energy system: how fast gas/nuclear/solar/wind are displacing coal, how the AI-power build-out
    and electrification are showing up as actual produced energy. That structural shift surfaces here
    first as a measured quantity, not as price or news — exactly what the changepoint detector eats.
  • Every observation carries its REAL reporting period parsed straight from the source row's period
    code (YYYY / YYYYMM / YYYYQn / YYYYMMDD). Nothing is synthesized, backfilled, or smoothed: a
    period absent from the manifest is absent here. The value is exactly EIA's published number.

LEAK-CLASS — CONFIRMATION / LAG. Generation, production and consumption are a RECORD of energy
already produced/consumed, published with a reporting lag (the monthly manifests trail the calendar
by ~1-3 months; international annual data trails by ~a year). They CONFIRM that a structural shift
happened; they do not run ahead of the price of the binding input (that leading role belongs to the
transformer/switchgear PPI and interconnection-queue series in power.py, or the export-control feeds).
Benchmark spot prices (WTI/Brent/Henry Hub) are the one near-real-time channel here, but they are a
priced signal — corroboration, never a pre-consensus early warning.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit:str, metric:str, domain:'energy',
   title:str, source_url:str, published_at:'YYYY-MM-DD'}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/eia.py

This module is SELF-CONTAINED: it does NOT touch the sqlite DB, cli.py, or the schemas. It only
fetches, normalizes, and writes data/feeds/eia.jsonl.
"""

from __future__ import annotations

import io
import json
import time
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
BULK_BASE = "https://api.eia.gov/bulk"          # keyless v2 bulk manifests (no api key)
CATALOG_URL = "https://www.eia.gov/opendata/bulk/"   # human-readable catalogue of the same files
OUT_PATH = DATA_DIR / "feeds" / "eia.jsonl"
DOMAIN = "energy"
REQUEST_TIMEOUT_S = 600       # ELEC.zip is ~240 MB; allow a long keyless GET
FETCH_RETRIES = 2
MIN_REFRESH_FRACTION = 0.9

# ---------------------------------------------------------------------------
# Period parsing — the REAL reporting date per row, never today.
# EIA period codes by frequency f: A=YYYY, M=YYYYMM, Q=YYYYQn, W/D/4=YYYYMMDD.
# Annual -> calendar year-end (the date the full year is knowable); monthly -> first-of-month;
# quarter -> first-of-quarter; weekly/daily -> the exact date. None if uninterpretable (DROP).
# ---------------------------------------------------------------------------
_Q_MONTH = {"1": 1, "2": 4, "3": 7, "4": 10}


def _parse_period(period: str, freq: str | None) -> date | None:
    s = (period or "").strip()
    if not s:
        return None
    # YYYYMMDD (weekly / daily / 4-week-avg)
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    # YYYYQn (quarterly)
    if "Q" in s.upper():
        y, _, q = s.upper().partition("Q")
        if y.isdigit() and q in _Q_MONTH:
            return date(int(y), _Q_MONTH[q], 1)
        return None
    # YYYYMM (monthly)
    if len(s) == 6 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), 1)
        except ValueError:
            return None
    # YYYY (annual) -> year-end
    if len(s) == 4 and s.isdigit():
        return date(int(s), 12, 31)
    return None


def _parse_published(last_updated: str | None) -> str | None:
    """EIA `last_updated` is an ISO timestamp like '2026-05-26T12:38:41-04:00'. Take the date part."""
    if not last_updated:
        return None
    return str(last_updated)[:10] or None


# ---------------------------------------------------------------------------
# Per-dataset matchers. Each returns (metric, title) for a series we WANT, or None to skip.
# Curated to capture the structural national/global supply series and leave per-plant / per-state
# minutiae out (ELEC alone has ~670k per-plant series we do not want).
# ---------------------------------------------------------------------------

# ELEC generation: fuel code -> human fuel label. We keep the US-national series only, sectors
# 99 (all sectors) and 98 (electric-power total) — the clean structural grid-mix totals.
_ELEC_FUELS = {
    "ALL": "all fuels", "COW": "coal", "NG": "natural gas", "NUC": "nuclear",
    "WND": "wind", "SUN": "all utility-scale solar", "TSN": "all solar (incl. small-scale)",
    "DPV": "small-scale (distributed) solar", "HYC": "conventional hydroelectric",
    "GEO": "geothermal", "AOR": "other renewables (total)", "WWW": "wood & wood-derived",
    "WAS": "other biomass", "PEL": "petroleum liquids", "HPS": "hydro pumped storage",
}
_ELEC_SECTORS = {"99": "all sectors", "98": "electric power (total)"}


def match_elec(series_id: str, name: str) -> tuple[str, str] | None:
    # ELEC.GEN.<FUEL>-US-<SECTOR>.<FREQ>  — US net generation by source.
    parts = series_id.split(".")
    if len(parts) < 4 or parts[1] != "GEN":
        return None
    seg = parts[2].split("-")
    if len(seg) != 3 or seg[1] != "US":
        return None
    fuel, _, sector = seg
    if fuel not in _ELEC_FUELS or sector not in _ELEC_SECTORS:
        return None
    title = f"US net electricity generation — {_ELEC_FUELS[fuel]} ({_ELEC_SECTORS[sector]})"
    return "electricity_net_generation", title


def match_total(series_id: str, name: str) -> tuple[str, str] | None:
    # TOTAL (Monthly Energy Review): match the headline US primary-energy production/consumption-by-
    # -fuel series by their EXACT name (the description before the ', <frequency>' suffix). Exact
    # match keeps derived ratios (per-capita, per-$GDP, heat-content), sector breakouts and the
    # 'U.S. Government' sub-series out — we want only the clean national structural totals.
    label = name.split(",")[0].strip()        # drop the ', Monthly' / ', Annual' suffix
    suffix = name.split(",", 1)[1].strip().lower() if "," in name else ""
    if suffix not in ("monthly", "annual"):    # only the plain headline series, never a sub-breakout
        return None
    want = {
        "Total Primary Energy Production": "primary_energy_production",
        "Total Primary Energy Consumption": "primary_energy_consumption",
        "Total Fossil Fuels Production": "primary_energy_production",
        "Total Fossil Fuels Consumption": "primary_energy_consumption",
        "Total Renewable Energy Production": "primary_energy_production",
        "Total Renewable Energy Consumption": "primary_energy_consumption",
        "Coal Production": "fuel_production",
        "Coal Consumption": "fuel_consumption",
        "Natural Gas Production (Dry)": "fuel_production",
        "Crude Oil Production": "fuel_production",
        "Total Renewable Energy Production": "primary_energy_production",
        "Nuclear Electricity Net Generation": "fuel_production",
    }
    metric = want.get(label)
    if metric is None:
        return None
    return metric, f"US {label}"


def match_pet(series_id: str, name: str) -> tuple[str, str] | None:
    nl = name.lower()
    # Benchmark crude spot prices (WTI / Brent), monthly only (skip daily/weekly noise -> keep volume down)
    if series_id in ("PET.RWTC.M", "PET.RBRTE.M"):
        return "crude_oil_spot_price", name.split(",")[0].strip()
    # US national + Lower-48 + Alaska field production of crude oil, monthly/weekly
    if "field production of crude oil" in nl:
        if series_id.startswith(("PET.MCRFPUS", "PET.WCRFPUS")) or "_R48_" in series_id \
                or "_SAK_" in series_id:
            return "crude_oil_production", name.split(",")[0].strip()
    return None


def match_ng(series_id: str, name: str) -> tuple[str, str] | None:
    nl = name.lower()
    # Henry Hub benchmark spot price (weekly + daily live channel)
    if series_id in ("NG.RNGWHHD.W", "NG.RNGWHHD.D"):
        return "natural_gas_spot_price", "Henry Hub Natural Gas Spot Price"
    # US dry / marketed gas production (monthly + annual national headline series)
    if ("u.s." in nl and "natural gas" in nl
            and ("dry production" in nl or "marketed production" in nl
                 or "gross withdrawals" in nl)):
        if name.count(",") <= 1:
            return "natural_gas_production", name.split(",")[0].strip()
    return None


# INTL fuel-product code (the 2nd dash-field of the series id) -> what it is. We keep ANNUAL
# single-country (ISO3) series for the big structural fuels: crude, dry gas, primary production/consumption.
_INTL_PRODUCTS = {
    "57": ("crude_oil_production", "crude oil incl. lease condensate production"),
    "26": ("dry_natural_gas_production", "dry natural gas production"),
    "44": ("primary_energy_production", "total primary energy production"),
    "45": ("primary_energy_consumption", "total primary energy consumption"),
}


def match_intl(series_id: str, name: str, geography: str | None) -> tuple[str, str] | None:
    # INTL.<PRODUCT>-<ACTIVITY>-<GEO>-<UNIT>.<FREQ> ; keep single-country ISO3 geographies only.
    if not geography or "+" in str(geography) or len(str(geography)) != 3:
        return None
    parts = series_id.split(".")
    if len(parts) < 2:
        return None
    fields = parts[1].split("-")
    if len(fields) < 4:
        return None
    product = fields[0]
    spec = _INTL_PRODUCTS.get(product)
    if not spec:
        return None
    metric, label = spec
    # name already reads e.g. "Crude oil including lease condensate production, United States, Annual"
    country = ""
    bits = name.split(",")
    if len(bits) >= 2:
        country = bits[1].strip()
    title = f"{country}: {label}".strip().lstrip(":").strip()
    return metric, title


# ---------------------------------------------------------------------------
# Fetch / stream / normalize
# ---------------------------------------------------------------------------

def _fetch_bytes(url: str) -> bytes | None:
    for attempt in range(FETCH_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 keyless public endpoint
                return resp.read()
        except Exception:  # noqa: BLE001 — network/throttle: back off, retry, then None
            if attempt < FETCH_RETRIES:
                time.sleep(2.0 * (attempt + 1))
                continue
            return None


def _iter_series(zip_bytes: bytes):
    """Yield each JSON series object from a downloaded EIA bulk zip's inner .txt manifest."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        txt_names = [n for n in zf.namelist() if n.lower().endswith(".txt")]
        for tn in txt_names:
            with zf.open(tn) as fh:
                for raw in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        o = json.loads(raw)
                    except ValueError:
                        continue
                    if o.get("series_id") and o.get("data"):
                        yield o


def collect_dataset(ds: str, matcher, *, needs_geo: bool = False, log=print) -> list[dict]:
    """Download one keyless EIA bulk dataset and normalize the matched structural series.

    `matcher(series_id, name[, geography]) -> (metric, title) | None` decides what we keep. Values /
    periods are verbatim from the source — no fill, backfill, or interpolation. A non-numeric or
    null value is DROPPED, never coerced. A dataset that fails to download is logged and skipped
    (returns []), never sinking the run or faking data.
    """
    url = f"{BULK_BASE}/{ds}.zip"
    raw = _fetch_bytes(url)
    if raw is None:
        log(f"  - skip {ds} (bulk zip download failed: {url})")
        return []

    obs: list[dict] = []
    n_series = 0
    try:
        for o in _iter_series(raw):
            sid = o["series_id"]
            name = o.get("name") or ""
            if needs_geo:
                hit = matcher(sid, name, o.get("geography"))
            else:
                hit = matcher(sid, name)
            if not hit:
                continue
            metric, title = hit
            unit = (o.get("units") or "").strip() or "unit"
            freq = o.get("f")
            published = _parse_published(o.get("last_updated"))
            kept_any = False
            for point in o.get("data") or []:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                period, value = point[0], point[1]
                if value is None:        # genuine null -> DROP, never fabricate
                    continue
                try:
                    fval = float(value)
                except (TypeError, ValueError):
                    continue
                d = _parse_period(str(period), freq)
                if d is None:
                    continue
                obs.append({
                    "series_id": f"eia:{sid}",
                    "date": d.isoformat(),    # REAL reporting period, not today
                    "value": fval,
                    "unit": unit,
                    "metric": metric,
                    "domain": DOMAIN,
                    "title": title,
                    "source_url": url,
                    "published_at": published or d.isoformat(),
                })
                kept_any = True
            if kept_any:
                n_series += 1
    except zipfile.BadZipFile:
        log(f"  - skip {ds} (corrupt zip)")
        return []

    obs.sort(key=lambda o: (o["series_id"], o["date"]))
    if obs:
        years = sorted({o["date"][:4] for o in obs})
        log(f"  + {ds:<6} {years[0]}–{years[-1]}  {len(obs):6d} obs across {n_series} series")
    else:
        log(f"  - {ds}: no matching structural series")
    return obs


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


def collect(*, log=print) -> list[dict]:
    """Fetch every EIA bulk dataset keyless, normalize, write the jsonl. Returns observations written.

    $0. Never fabricates: a dataset that fails to download is logged and skipped. Refuses to clobber
    an existing file with a much smaller refresh (transient partial-download guard).
    """
    all_obs: list[dict] = []
    # smaller/faster manifests first; ELEC (~240 MB) and INTL last.
    all_obs += collect_dataset("TOTAL", match_total, log=log)
    all_obs += collect_dataset("NG", match_ng, log=log)
    all_obs += collect_dataset("PET", match_pet, log=log)
    all_obs += collect_dataset("INTL", match_intl, needs_geo=True, log=log)
    all_obs += collect_dataset("ELEC", match_elec, log=log)

    existing = _existing_line_count()
    if not all_obs:
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(all_obs) < int(existing * MIN_REFRESH_FRACTION):
        log(f"\npartial refresh {len(all_obs)} rows < {MIN_REFRESH_FRACTION:.0%} of existing "
            f"{existing}; preserved {OUT_PATH}")
        return []
    _write_jsonl_atomic(all_obs)
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    print("EIA energy-supply data (keyless, api.eia.gov/bulk v2 manifests):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — EIA bulk unreachable this run (no data written).")
    else:
        n_series = len({o["series_id"] for o in observations})
        metrics = sorted({o["metric"] for o in observations})
        dates = sorted({o["date"] for o in observations})
        print(f"\nrows: {len(observations)}   series: {n_series}   "
              f"date span: {dates[0]} → {dates[-1]}")
        print(f"metrics: {', '.join(metrics)}")
        print("\n3 sample observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
