"""OpenAlex — the LEADING research-signal collector (keyless, global, all-field).

WHY THIS EXISTS (2026-06-15). Our diagnosed blind spot (see goal): the detector read only
ONE coarse channel (annual counts) and fit one global slope, so deep learning was invisible at
2010 and AI-compute looked *decelerating* at 2017. The fix the goal names explicitly is
multi-channel LEADING detectors: sub-topic SHARE, cross-field DIFFUSION, slope-of-slope. This
module mints exactly those channels from OpenAlex — the open global scholarly graph (~250M works,
every field, every language, no US/English skew, keyless).

THREE CHANNELS PER WATCHED CONCEPT (each a separate series + metric):
  • works   — works published per year for the concept (the volume base for changepoint/slope-of-slope).
  • share   — concept works / ALL works that year, in ppm of the world literature. This is the bar's
              "a sub-topic gaining share" signal: it normalizes out total-corpus growth, so a rising
              share is real reorientation of attention, not just more papers everywhere. LEADING.
  • fields  — number of distinct top-level fields the concept's works appear in that year (breadth).
              This is the bar's "a technique crossing from field A into field B" — cross-field
              diffusion. A widening field-count is a technique going general-purpose. LEADING.

All three are computed keyless via OpenAlex `group_by` (one call per concept-channel, +1 shared
denominator call). Concept IDs are pinned (verified live 2026-06-15) — the /concepts search endpoint
is currently erroring, so we do NOT depend on runtime resolution; a bad/renamed ID is logged and
skipped, never faked.

LEAK DISCIPLINE: each observation's `as_of` = Dec-31 of the publication year (the point in time the
year's count is knowable). The CURRENT (incomplete) year is included but is partial-by-construction;
downstream slope detectors must treat the trailing year as provisional. Nothing is interpolated; a
year with no group bucket is simply absent.

normalized observation shape (one JSON object per jsonl line) — note the per-row `metric` so a single
feed file can carry three metrics (ingest.py honors row-level metric):
  {series_id, metric, date:'YYYY-12-31', value:float, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/openalex.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"  # OpenAlex "polite pool"
BASE = "https://api.openalex.org/works"
OUT_PATH = DATA_DIR / "feeds" / "openalex.jsonl"

START_YEAR = 2010
END_YEAR = 2025  # the trailing year is partial-by-construction (provisional)

# Watched frontier concepts. (slug, OpenAlex concept id, display name) — IDs verified live 2026-06-15
# via the autocomplete entity endpoint. Generate WIDE here (the aperture is open at the detector); the
# gate downstream converges. Add concepts freely — a renamed/dead id is logged and skipped, not faked.
CONCEPTS: list[tuple[str, str, str]] = [
    ("deep_learning",      "C108583219",  "Deep learning"),
    ("crispr",             "C98108389",   "CRISPR"),
    ("perovskite_solar",   "C2780089039", "Perovskite solar cell"),
    ("lithium_battery",    "C2779004117", "Lithium battery"),
    ("quantum_computing",  "C119382340",  "Superconducting quantum computing"),
    ("reinforcement_learning", "C97541855", "Reinforcement learning"),
    ("graph_neural_network",   "C153180895", "Artificial neural network"),
    ("solid_oxide_fuel_cell",  "C148764684", "Solid oxide fuel cell"),
]


def _fetch_json(url: str, *, retries: int = 3):
    """GET a keyless OpenAlex URL → parsed JSON, or None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 keyless public endpoint
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — network/parse/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            return None


def _group_by(*, group: str, concept_id: str | None, year: int | None) -> dict | None:
    """One keyless group_by call. Returns {key: count} or None on failure.
    per_page=200 is REQUIRED — a small per_page silently truncates the group_by buckets."""
    filt = []
    if concept_id:
        filt.append(f"concepts.id:{concept_id}")
    if year is not None:
        filt.append(f"from_publication_date:{year}-01-01")
        filt.append(f"to_publication_date:{year}-12-31")
    else:
        filt.append(f"from_publication_date:{START_YEAR}-01-01")
        filt.append(f"to_publication_date:{END_YEAR}-12-31")
    params = {"filter": ",".join(filt), "group_by": group, "per_page": 200}
    data = _fetch_json(f"{BASE}?{urllib.parse.urlencode(params)}")
    if not isinstance(data, dict) or "group_by" not in data:
        return None
    out = {}
    for g in data["group_by"]:
        out[str(g.get("key"))] = int(g.get("count") or 0)
    return out


def collect(*, log=print) -> list[dict]:
    """Mint the three leading channels for every watched concept, keyless. Returns observations
    actually written. $0. A concept that fails to fetch is logged and skipped, not filled."""
    obs: list[dict] = []

    # --- shared denominator: ALL works per year (one call) — the share normalizer ---
    log("denominator: all works per year ...")
    totals = _group_by(group="publication_year", concept_id=None, year=None)
    if not totals:
        log("  ! denominator fetch failed — share channel will be skipped this run")
    time.sleep(0.3)

    for slug, cid, name in CONCEPTS:
        # volume: works per year (one call)
        vol = _group_by(group="publication_year", concept_id=cid, year=None)
        if not vol:
            log(f"  - skip {slug} ({cid}) — no volume returned (renamed/dead id?)")
            continue
        years = sorted(int(y) for y in vol if y.isdigit() and START_YEAR <= int(y) <= END_YEAR)
        for y in years:
            v = vol[str(y)]
            obs.append({"series_id": f"openalex:works:{slug}", "metric": "research_works",
                        "date": f"{y}-12-31", "value": float(v), "unit": "works/year",
                        "title": f"{name} — works/year"})
            # share (ppm of world literature) when the denominator is available
            if totals and totals.get(str(y)):
                ppm = 1_000_000.0 * v / totals[str(y)]
                obs.append({"series_id": f"openalex:share:{slug}", "metric": "research_share_ppm",
                            "date": f"{y}-12-31", "value": round(ppm, 2), "unit": "ppm of works",
                            "title": f"{name} — share of world literature (ppm)"})
        log(f"  + {slug:22s} works {years[0]}–{years[-1]}  "
            f"{vol[str(years[0])]}→{vol[str(years[-1])]}")
        time.sleep(0.3)

        # cross-field diffusion: # distinct fields per year (one call per year)
        for y in range(max(START_YEAR, 2012), END_YEAR + 1):
            fields = _group_by(group="primary_topic.field.id", concept_id=cid, year=y)
            if not fields:
                continue
            n = sum(1 for k, c in fields.items() if c > 0 and k not in ("unknown", "None"))
            obs.append({"series_id": f"openalex:fields:{slug}", "metric": "research_field_breadth",
                        "date": f"{y}-12-31", "value": float(n), "unit": "fields",
                        "title": f"{name} — # fields present (diffusion breadth)"})
            time.sleep(0.2)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(obs)} observations → {OUT_PATH}")
    return obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — OpenAlex unreachable this run (no data written).")
    else:
        print(f"\nfirst 3 observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
