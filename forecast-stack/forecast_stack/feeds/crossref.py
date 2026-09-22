"""Crossref REST API — keyless scholarly-publication-volume time-series collector.

A self-contained KEYLESS collector for Vati's data layer. The Crossref REST API
(https://api.crossref.org/works) is open, no API key, and exposes the world's DOI-registered
scholarly record. We query the /works endpoint with `query.bibliographic=<term>` plus a
`filter=from-pub-date:...,until-pub-date:...` window and `rows=0`, then read
`message.total-results` — the exact count of works matching that term published in that year.
Sending a descriptive User-Agent with a mailto puts us in Crossref's faster "polite pool".

This module fetches yearly publication counts for a small basket of frontier research topics
across 2010–2025, so we can later compute sub-topic SHARE and ACCELERATION (the leading signal
is the second derivative of attention, not the raw level). Each (term, year) is one observation.

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every observation carries its REAL publication date — counts are bucketed by Crossref's
    issued/published date, so `date` = December 31 of the publication year (the point in time the
    year's count is knowable / complete). Nothing is synthesized or interpolated.
  • A year whose fetch FAILS (network/throttle/error) is DROPPED, never fabricated or filled.
  • As a forecasting signal this is LEADING: research publication volume rises while a field is
    still pre-commercial — scarcity/value migrates toward topics whose paper count is accelerating
    years before the capability ships and the constraint gets priced. (Caveat: Crossref's
    bibliographic match is loose/recall-biased, so absolute levels are noisy; the YoY trajectory
    and cross-topic share are the usable signal, not the raw count.)

normalized observation shape (one JSON object per jsonl line):
  {series_id:'crossref:<slug>', date:'YYYY-12-31', value:int, unit:'works/year', title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/crossref.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
CR_BASE = "https://api.crossref.org/works"
OUT_PATH = DATA_DIR / "feeds" / "crossref.jsonl"

# A small basket of frontier research topics. Each: (query term, url-safe slug). Slugs are stable
# series ids; terms are matched with query.bibliographic (title/abstract/container loose match).
TERMS: list[tuple[str, str]] = [
    ("diffusion model", "diffusion_model"),
    ("solid state battery", "solid_state_battery"),
    ("mRNA vaccine", "mrna_vaccine"),
    ("quantum error correction", "quantum_error_correction"),
    ("perovskite solar", "perovskite_solar"),
    ("CRISPR gene editing", "crispr_gene_editing"),
]

YEARS = list(range(2010, 2026))  # 2010..2025 inclusive


def _fetch_json(url: str, *, retries: int = 2):
    """GET a keyless Crossref API URL → parsed JSON. Returns None on persistent failure (never fakes)."""
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


def fetch_count(term: str, year: int, *, retries: int = 2) -> int | None:
    """Fetch the total works count for `term` published in `year`. Returns an int, or None if the
    fetch fails / the payload is malformed (caller DROPS None — never fabricates a count)."""
    params = {
        "query.bibliographic": term,
        "filter": f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31",
        "rows": 0,
    }
    url = f"{CR_BASE}?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url, retries=retries)
    if not isinstance(data, dict) or data.get("status") != "ok":
        return None
    msg = data.get("message")
    if not isinstance(msg, dict):
        return None
    total = msg.get("total-results")
    if not isinstance(total, int):
        return None
    return total


def collect(*, log=print) -> list[dict]:
    """Fetch yearly publication counts for every (term × year) keyless, normalize, write the jsonl.
    Returns the list of observations actually written. $0. Never fabricates: a (term, year) whose
    fetch fails is logged and skipped, not filled."""
    all_obs: list[dict] = []
    for term, slug in TERMS:
        series_id = f"crossref:{slug}"
        kept = 0
        first_yr = last_yr = None
        for year in YEARS:
            total = fetch_count(term, year)
            time.sleep(0.3)  # polite pacing
            if total is None:
                log(f"  - skip {series_id} {year} (fetch failed)")
                continue
            all_obs.append({
                "series_id": series_id,
                "date": f"{year}-12-31",  # year's count knowable at year-end
                "value": int(total),
                "unit": "works/year",
                "title": f"{term} — works published",
            })
            kept += 1
            first_yr = first_yr or year
            last_yr = year
        if kept:
            log(f"  + research {slug:<24} {first_yr}–{last_yr}  {kept} yrs")
        else:
            log(f"  - skip {series_id} (no years returned)")

    # chronological, then by series — latest last within each series
    all_obs.sort(key=lambda o: (o["series_id"], o["date"]))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in all_obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — Crossref API unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
