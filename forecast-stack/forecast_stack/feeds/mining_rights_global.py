"""Global mining-rights supply-pipeline time series — from the local MiningTerminal gov-data store.

The MiningTerminal project scraped government mineral-title registries worldwide and normalized them
to ONE schema (every permit carries country/jurisdiction/permit_type/phase/status/commodity/
area_hectares/grant_date/expiry_date/holder_name). That normalization is the gift: it lets us turn a
30GB pile of parcel GeoJSON into clean, dated, GLOBAL supply-pipeline series — the thing our substrate
was missing (it skewed US: EIA, LBNL queue, PAD-US, the USGS US salient).

WHY THIS IS THE SIGNAL. A mineral title is the earliest physical claim on future supply. The phases are
a lead/lag ladder that runs YEARS ahead of production and decades ahead of price:
    application → exploration permit → exploitation/production permit → mine.
A surge in exploration permits for cobalt in the DRC, or nickel IUPs in Indonesia, is a pre-consensus
supply signal that shows up here long before it shows up in USGS production or the spot price. We
aggregate by the permit's REAL grant year, so each (country × phase) and each (commodity × phase) is an
annual structural series the changepoint detector eats.

WHAT WE EMIT (per grant-year, leak-safe — grant_date is a historical record, published_at = snapshot):
  • mining_rights:<cc>:<phase>:count        — permits dated in year, by country × phase
  • mining_rights:<cc>:<phase>:area_ha      — hectares titled in year, by country × phase
  • mining_rights:global:<commodity>:<phase>:count — worldwide per-critical-mineral pipeline (a
    multi-commodity permit counts toward each of its commodities; commodity strings are canonicalized
    across French/Spanish/Swedish/Indonesian registry vocab).

SCOPE. All NON-US registries (the global story — Africa: DRC/Guinea/Kenya/Malawi/Nigeria/Senegal/
Uganda/Zambia; South America: Brazil/Guyana; SE Asia: Indonesia/Philippines; Europe: Finland/Poland/
Sweden; + Canada/Panama). The eight giant US-state claim dumps are skipped here — US federal claims are
already covered by forecast_stack/feeds/blm_mining_claims.py, and the point of this feed is the rest of the world.

LEAK-CLASS — LEADING. Titles are claimed before supply exists; this RUNS AHEAD of the priced outcome.
We drop grant years after the snapshot year (bad future dates) and before 1980 (registry noise), and we
never fabricate: a permit with no parseable grant year contributes only to the area/stock it can.

$0, keyless (reads a LOCAL store). Run:  uv run python forecast_stack/feeds/mining_rights_global.py
Requires `ijson` for streaming (added only if missing); falls back to a clear error otherwise.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from forecast_stack.config import DATA_DIR

# Optional: a local MiningTerminal GeoJSON permit dump (set MININGTERMINAL_PERMITS_DIR).
_PERMITS_ENV = os.environ.get("MININGTERMINAL_PERMITS_DIR", "")
PERMITS_DIR: Path | None = Path(_PERMITS_ENV) if _PERMITS_ENV else None
OUT_PATH = DATA_DIR / "feeds" / "mining_rights_global.jsonl"
SNAPSHOT_DATE = "2026-05-28"          # the MiningTerminal scrape date (every file is *_20260528)
SNAPSHOT_YEAR = 2026
MIN_YEAR = 1980

# US-state claim dumps to skip (US is covered by blm_mining_claims.py; this feed is the rest of world).
_US_PREFIXES = ("us_",)

# Phase canonicalization → the supply ladder. Registry 'phase' values are already English-ish.
_PHASE_MAP = {
    "exploration": "exploration", "prospecting": "exploration", "reservation": "exploration",
    "exploitation": "production", "production": "production", "mining": "production",
    "development": "development", "construction": "development",
}

# Commodity canonicalization across registry languages (FR/ES/SV/ID/EN) → canonical critical-mineral
# tokens. Only the minerals that matter for the constraint thesis are mapped; everything else is binned
# under its raw lowercase token for the per-country series but ignored for the global per-mineral series.
_COMMODITY_MAP = {
    # gold
    "gold": "gold", "or": "gold", "guld": "gold", "oro": "gold", "emas": "gold",
    # copper
    "copper": "copper", "cuivre": "copper", "koppar": "copper", "cobre": "copper", "tembaga": "copper",
    # cobalt
    "cobalt": "cobalt", "kobolt": "cobalt", "kobalt": "cobalt",
    # nickel
    "nickel": "nickel", "nikel": "nickel", "nickel ore": "nickel",
    # lithium
    "lithium": "lithium", "litium": "lithium", "litio": "lithium",
    # iron
    "iron": "iron", "fer": "iron", "järn": "iron", "hierro": "iron", "besi": "iron", "iron ore": "iron",
    # tin
    "tin": "tin", "étain": "tin", "etain": "tin", "tenn": "tin", "cassitérite": "tin", "cassiterite": "tin",
    "timah": "tin",
    # bauxite / aluminium
    "bauxite": "bauxite", "bauksit": "bauxite", "aluminium": "bauxite",
    # rare earths
    "rare earths": "rare_earths", "rare earth": "rare_earths", "terres rares": "rare_earths",
    "lantan och lantanider": "rare_earths", "lantanider": "rare_earths",
    # manganese
    "manganese": "manganese", "manganèse": "manganese", "mangan": "manganese",
    # zinc / lead / silver
    "zinc": "zinc", "zink": "zinc", "lead": "lead", "plomb": "lead", "bly": "lead",
    "silver": "silver", "argent": "silver", "plata": "silver", "perak": "silver",
    # coal, diamond, uranium, graphite, phosphate
    "coal": "coal", "charbon": "coal", "batubara": "coal",
    "diamond": "diamond", "diamant": "diamond", "intan": "diamond",
    "uranium": "uranium", "graphite": "graphite", "graphit": "graphite",
    "phosphate": "phosphate", "fosfat": "phosphate",
}
_GLOBAL_MINERALS = set(_COMMODITY_MAP.values())


def _ensure_ijson():
    try:
        import ijson  # noqa: F401
        return True
    except ImportError:
        return False


def _norm_str(v) -> str:
    if isinstance(v, list):
        return ",".join(str(x) for x in v if x)
    return str(v or "")


def _canon_phase(raw: str) -> str:
    r = raw.strip().lower()
    return _PHASE_MAP.get(r, "unspecified" if not r else "other")


def _grant_year(raw: str) -> int | None:
    s = _norm_str(raw)[:4]
    if s.isdigit():
        y = int(s)
        if MIN_YEAR <= y <= SNAPSHOT_YEAR:
            return y
    return None


def _commodities(raw) -> list[str]:
    """Canonical critical-mineral tokens for a permit's commodity field (may be multi-valued)."""
    out: set[str] = set()
    parts = _norm_str(raw).lower().replace(";", ",").split(",")
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p in _COMMODITY_MAP:
            out.add(_COMMODITY_MAP[p])
        else:                                   # substring fallback for compound registry phrases
            for k, v in _COMMODITY_MAP.items():
                if k in p:
                    out.add(v)
                    break
    return list(out)


def _country_code(props: dict, fname: str) -> str:
    cc = _norm_str(props.get("country")).strip().lower()
    if cc and len(cc) <= 3:
        return cc
    if cc:
        return cc[:24].replace(" ", "_")
    return fname.split("_")[0][:3]              # fallback: filename prefix


def collect(*, log=print) -> list[dict]:
    if PERMITS_DIR is None or not PERMITS_DIR.exists():
        log(f"permits dir not found: {PERMITS_DIR} — nothing to do")
        return []
    if not _ensure_ijson():
        log("ijson not installed; run with `uv run --with ijson python ...` or `uv add ijson`. Aborting.")
        return []
    import ijson

    files = sorted(
        p for p in PERMITS_DIR.glob("*.geojson")
        if not os.path.basename(p).startswith(_US_PREFIXES)
    )
    log(f"streaming {len(files)} non-US registry files from {PERMITS_DIR}")

    # aggregates: count and area keyed by (series_key) -> {year: value}
    cc_count: dict = defaultdict(lambda: defaultdict(int))      # (cc,phase) -> year -> count
    cc_area: dict = defaultdict(lambda: defaultdict(float))     # (cc,phase) -> year -> ha
    glob_count: dict = defaultdict(lambda: defaultdict(int))    # (mineral,phase) -> year -> count

    for path in files:
        fname = os.path.basename(path)
        n = 0
        try:
            with open(path, "rb") as f:
                for feat in ijson.items(f, "features.item"):
                    if not isinstance(feat, dict):
                        continue
                    p = feat.get("properties") or {}
                    n += 1
                    yr = _grant_year(p.get("grant_date"))
                    if yr is None:
                        continue
                    cc = _country_code(p, fname)
                    phase = _canon_phase(_norm_str(p.get("phase")))
                    cc_count[(cc, phase)][yr] += 1
                    try:
                        cc_area[(cc, phase)][yr] += float(p.get("area_hectares") or 0) or 0.0
                    except (TypeError, ValueError):
                        pass
                    for m in _commodities(p.get("commodity")):
                        glob_count[(m, phase)][yr] += 1
        except Exception as exc:  # noqa: BLE001 — one bad file must not sink the run
            log(f"  - {fname}: stream error after {n} feats ({exc}); kept partial")
            continue
        log(f"  + {fname:<46} {n:>8,} features")

    obs: list[dict] = []

    def emit(series_id, metric, unit, domain, title, year_map):
        for yr, val in sorted(year_map.items()):
            if val <= 0:
                continue
            obs.append({
                "series_id": series_id,
                # as_of = the grant year-end (the period the value pertains to AND when it became
                # public — a granted title is on the public registry that year). This is the
                # point-in-time dedup key, so it MUST vary per year or the series collapses to one row.
                "as_of": f"{yr}-12-31",
                "date": f"{yr}-12-31",
                "event_time": f"{yr}-12-31",
                "value": float(val),
                "unit": unit,
                "metric": metric,
                "domain": domain,
                "title": title,
                "published_at": f"{yr}-12-31",
                "observed_at": SNAPSHOT_DATE,
                "source_url": "https://miningterminal.com (gov mineral-title registries, normalized)",
            })

    for (cc, phase), ym in cc_count.items():
        emit(f"mining_rights:{cc}:{phase}:count", "mining_permits_dated", "permits", "minerals/land",
             f"Mineral-title permits dated per year — {cc.upper()} ({phase})", ym)
    for (cc, phase), ym in cc_area.items():
        emit(f"mining_rights:{cc}:{phase}:area_ha", "mining_permit_area", "hectares", "minerals/land",
             f"Mineral-title area titled per year — {cc.upper()} ({phase})", ym)
    for (m, phase), ym in glob_count.items():
        if m in _GLOBAL_MINERALS:
            emit(f"mining_rights:global:{m}:{phase}:count", "mining_permits_dated", "permits",
                 "minerals/land", f"Global {m} mineral-title permits dated per year ({phase})", ym)

    obs.sort(key=lambda o: (o["series_id"], o["date"]))
    if not obs:
        log("no observations produced (no parseable grant years?) — preserving any existing file")
        return []

    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for o in obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    tmp.replace(OUT_PATH)
    n_series = len({o["series_id"] for o in obs})
    log(f"\nwrote {len(obs)} observations / {n_series} series → {OUT_PATH}")
    return obs


if __name__ == "__main__":
    rows = collect()
    if rows:
        n_series = len({o["series_id"] for o in rows})
        years = sorted({o["date"][:4] for o in rows})
        print(f"\n{len(rows)} obs · {n_series} series · {years[0]}–{years[-1]}")
        print("sample:")
        for o in rows[:6]:
            print("  " + json.dumps(o, ensure_ascii=False))
