"""OECD SDMX composite leading indicators (CLI) — keyless macro-pillar collector.

The OECD exposes its full statistical warehouse through a public, KEYLESS SDMX REST API at
https://sdmx.oecd.org/public/rest/data/<dataflow>/<key>?format=jsondata . No API key, no auth — a
plain HTTPS GET returns SDMX-JSON. The finicky part is the <key>: it is a dot-separated, POSITIONAL
list of dimension values (REF_AREA.FREQ.MEASURE.UNIT_MEASURE.ACTIVITY.ADJUSTMENT.TRANSFORMATION.
TIME_HORIZ.METHODOLOGY for this dataflow), with empty slots meaning "all". Get one slot count wrong
and you get HTTP 422 ("Not enough key values"); a syntactically-valid but empty selection returns
404 "NoResultsFound". We pin one verified dataflow — the Composite Leading Indicators (DSD_STES@
DF_CLI) — and request the amplitude-adjusted leading indicator (MEASURE=LI, ADJUSTMENT=AA) for a few
major economies.

Why this feeds the MACRO pillar — and its leak-class:
  • The OECD CLI is explicitly DESIGNED to LEAD the business cycle: it is constructed from component
    series (order books, building permits, share prices, spreads, sentiment) that turn BEFORE GDP /
    industrial-production turning points, then amplitude-adjusted to an index around 100. A reading
    crossing/turning above or below 100 anticipates an expansion/slowdown by ~6–9 months. So as a
    forecasting signal this is a LEADING channel: it moves AHEAD of the priced macro outcome (the
    recession / recovery / IP turn it is engineered to forecast). That is the rare, valuable placement
    for an attention-grounding series — it grounds the macro pillar with an early-warning, not a
    confirmation.
  • Every observation carries its REAL reporting period, parsed straight from the SDMX TIME_PERIOD
    dimension (monthly → anchored to first-of-month, the period the value covers). Nothing is
    synthesized, backfilled, or smoothed: a period absent from the response is absent here; the value
    is exactly the OECD's published index point.

This module is SELF-CONTAINED (per the build brief): it does NOT touch the sqlite DB, cli.py, or the
schemas. It fetches, normalizes to plain observation dicts {series_id,date,value,unit,title}, and
(run as __main__) writes a real sample (>=30 observations) to data/feeds/oecd.jsonl. Cost: $0, keyless.

Run directly:  uv run python forecast_stack/feeds/oecd.py
"""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OUT_PATH = DATA_DIR / "feeds" / "oecd.jsonl"

# The verified keyless dataflow: OECD Short-Term Economic Statistics — Composite Leading Indicators.
# Agency.Dataflow,Version  (version is part of the path).
DATAFLOW = "OECD.SDD.STES,DSD_STES@DF_CLI,4.1"
BASE = "https://sdmx.oecd.org/public/rest/data"

# The CLI series-key dimension order (9 positions), confirmed from the returned data structure:
#   REF_AREA . FREQ . MEASURE . UNIT_MEASURE . ACTIVITY . ADJUSTMENT . TRANSFORMATION . TIME_HORIZ . METHODOLOGY
# We pin everything except REF_AREA (which we vary). MEASURE=LI (amplitude-adjusted leading indicator),
# FREQ=M (monthly), ADJUSTMENT=AA (amplitude adjusted), METHODOLOGY=H. Empty slots = "all", which here
# collapses to the single published variant for the LI/AA/H selection.
#   key template:  <REF_AREA>.M.LI...AA...H
REPORTERS: list[tuple[str, str]] = [
    ("USA", "United States"),
    ("OECD", "OECD total"),
    ("G7", "G7"),
    ("DEU", "Germany"),
    ("JPN", "Japan"),
]

START_PERIOD = "2018"  # plenty of monthly history per series; the API returns only real, dated points.


def _build_url(ref_area: str) -> str:
    key = f"{ref_area}.M.LI...AA...H"
    return f"{BASE}/{DATAFLOW}/{key}?startPeriod={START_PERIOD}&format=jsondata"


def _fetch_json(url: str, *, retries: int = 2):
    """GET a keyless OECD SDMX URL → parsed SDMX-JSON. Returns None on persistent failure (never fakes).

    404 (NoResultsFound) and 422 (bad key arity) raise inside urlopen and are caught → None, so a bad
    selection degrades honestly to "no observations" rather than emitting anything fabricated.
    """
    import time

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": "application/vnd.sdmx.data+json"}
            )
            with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 keyless public endpoint
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — network/HTTP/parse: back off, retry, then None
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None


def _period_to_date(period: str) -> date | None:
    """Parse an SDMX TIME_PERIOD → the REAL date it covers (never a synthesized one).

    Monthly periods come as 'YYYY-MM' → anchored to the first of that month (the period the index
    point covers). Plain 'YYYY' → year-end. Anything unparseable is dropped, not guessed.
    """
    p = period.strip()
    if len(p) == 7 and p[4] == "-":
        try:
            return date(int(p[:4]), int(p[5:7]), 1)
        except ValueError:
            return None
    if len(p) == 4 and p.isdigit():
        return date(int(p), 12, 31)
    return None


def normalize(ref_area: str, name: str, payload: dict) -> list[dict]:
    """SDMX-JSON payload → normalized Vati observations.

    SDMX-JSON encodes observations by POSITIONAL index, not by literal value: each series key is a
    colon-joined tuple of indices into the structure's series-dimension value lists, and each
    observation key is an index into the structure's observation-dimension (TIME_PERIOD) value list.
    We resolve those indices back to real period strings and unit names — taking everything verbatim
    from the source, with no fill or interpolation.
    """
    try:
        data = payload["data"]
        struct = data["structures"][0]
        ds = data["dataSets"][0]
    except (KeyError, IndexError, TypeError):
        return []

    # TIME_PERIOD is the (only) observation dimension; its values give the real ordered periods.
    obs_dims = struct.get("dimensions", {}).get("observation", [])
    time_dim = next((d for d in obs_dims if d.get("id") == "TIME_PERIOD"), None)
    if not time_dim:
        return []
    time_values = [v.get("id", "") for v in time_dim.get("values", [])]

    # UNIT_MEASURE (series dimension) gives the unit name (e.g. "Index").
    series_dims = struct.get("dimensions", {}).get("series", [])
    unit_dim = next((d for d in series_dims if d.get("id") == "UNIT_MEASURE"), None)
    unit_name = "Index"
    if unit_dim and unit_dim.get("values"):
        unit_name = unit_dim["values"][0].get("name") or "Index"

    series_id = f"oecd:DF_CLI:LI:{ref_area}"
    title = f"OECD composite leading indicator (CLI, amplitude-adjusted) — {name}"

    out: list[dict] = []
    for _series_key, series_obj in (ds.get("series") or {}).items():
        for obs_idx, obs_arr in (series_obj.get("observations") or {}).items():
            try:
                period = time_values[int(obs_idx)]
            except (ValueError, IndexError):
                continue
            d = _period_to_date(period)
            if d is None or not obs_arr:
                continue
            raw = obs_arr[0]
            if raw is None:  # genuine gap — drop, never backfill
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            out.append({
                "series_id": series_id,
                "date": d.isoformat(),
                "value": value,
                "unit": unit_name,
                "title": title,
            })
    out.sort(key=lambda o: o["date"])
    return out


def collect(*, log=print) -> list[dict]:
    """Fetch each reporter's CLI series keyless, normalize, write the jsonl. Returns the observations
    actually written. $0. A reporter that fails / returns nothing is logged and skipped, not filled."""
    import time

    all_obs: list[dict] = []
    for ref_area, name in REPORTERS:
        url = _build_url(ref_area)
        payload = _fetch_json(url)
        if payload is None:
            log(f"  - skip {ref_area} (no payload — 404/422/network)")
            continue
        obs = normalize(ref_area, name, payload)
        if not obs:
            log(f"  - skip {ref_area} (no dated observations in payload)")
            continue
        all_obs.extend(obs)
        log(f"  + CLI/LI  {ref_area:<5} {obs[0]['date'][:7]}–{obs[-1]['date'][:7]}  {len(obs)} obs")
        time.sleep(0.3)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in all_obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    print("OECD SDMX composite leading indicators (keyless, sdmx.oecd.org/public/rest):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — OECD SDMX unreachable / key empty this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
