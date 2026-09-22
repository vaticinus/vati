"""PatentsView (USPTO) — patent-grant counts by CPC class as an INNOVATION LEADING indicator.

INTENDED SIGNAL (leak-class = LEADING): granted-patent counts in a technology CPC class, aggregated
to monthly/yearly buckets, are an *upstream* capability signal — a tech field's patenting accelerates
years before the priced economic outcome (products, revenue, sector repricing). Each observation
carries the patent's REAL grant date; nothing is synthesised or backfilled. The collector matches the
repo's keyless-collector style (engine/pillars/forces.py, engine/pillars/power.py): fetch via urllib,
bucket to point-in-time dated counts, emit normalized observations.

HONEST STATUS — 2026-06-11: THIS SOURCE NOW REQUIRES AN API KEY. Probed live and confirmed:
  • The keyless PatentsView Search API at https://search.patentsview.org/api/v1/patent/ is RETIRED:
    `search.patentsview.org` no longer resolves (NXDOMAIN), and the legacy host
    https://api.patentsview.org/patents/query 301-redirects to
    https://data.uspto.gov/support/transition-guide/patentsview (the PatentsView→ODP migration).
  • Its successor is the USPTO Open Data Portal (ODP) at https://api.uspto.gov/api/v1/...
    A no-key request returns HTTP 401 {"message":"Unauthorized"}; a request with an invalid key
    returns HTTP 403 {"message":"Forbidden"} (i.e. the endpoint is live but gate-checks the key).
    An ODP API key (free signup, but a key nonetheless) is REQUIRED — there is no keyless path.

Per the leak/honesty discipline (never fake values), this collector does NOT fabricate counts. When
run it performs the live keyless probe, reports the key requirement, writes only a tiny diagnostic
sidecar, and exits without writing observation rows.
If/when an ODP key is provisioned, set USPTO_ODP_KEY in the environment and flip _build_query() to the
ODP schema (POST https://api.uspto.gov/api/v1/patent/applications/search with header `X-API-KEY`);
the bucketing/normalisation below is reusable as-is.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"

# Legacy keyless PatentsView Search API (RETIRED — kept for the live probe / documentation).
PATENTSVIEW_SEARCH = "https://search.patentsview.org/api/v1/patent/"
# Successor: USPTO Open Data Portal (requires X-API-KEY).
USPTO_ODP_SEARCH = "https://api.uspto.gov/api/v1/patent/applications/search"

OUT_PATH = DATA_DIR / "feeds" / "patentsview.jsonl"
STATUS_PATH = OUT_PATH.with_suffix(".status.json")

# Tech CPC classes that would be the LEADING innovation signal if the source were keyless.
#   G06N — machine learning / AI computing systems
#   H01M — batteries / electrochemical energy storage
#   H01L — semiconductors / solid-state devices
CPC_CLASSES: list[tuple[str, str]] = [
    ("G06N", "AI / machine-learning computing"),
    ("H01M", "Batteries & electrochemical storage"),
    ("H01L", "Semiconductors & solid-state devices"),
]
WINDOW_START = "2015-01-01"


def _build_query(cpc_group: str, gte: str, lt: str) -> dict:
    """PatentsView Search-API query body (the keyless schema, retained for when a key is wired)."""
    return {
        "q": {
            "_and": [
                {"_gte": {"patent_date": gte}},
                {"_lt": {"patent_date": lt}},
                {"cpc_current.cpc_group_id": cpc_group},
            ]
        },
        "f": ["patent_id", "patent_date"],
        "o": {"size": 1000},
    }


def _probe_keyless() -> tuple[bool, str]:
    """Live-probe the (former) keyless PatentsView Search API. Returns (reachable, detail).

    Confirms the source is retired / key-gated WITHOUT fabricating any observation."""
    body = _build_query("G06N", "2023-01-01", "2023-02-01")
    url = (
        f"{PATENTSVIEW_SEARCH}?q={urllib.parse.quote(json.dumps(body['q']))}"
        f"&f={urllib.parse.quote(json.dumps(body['f']))}"
        f"&o={urllib.parse.quote(json.dumps(body['o']))}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 public endpoint
            return True, f"HTTP {resp.status} (unexpected — source may have been restored keyless)"
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "") if e.headers else ""
        return False, f"HTTP {e.code}" + (f" -> {loc}" if loc else "")
    except urllib.error.URLError as e:
        return False, f"URLError {e.reason} (host retired / NXDOMAIN)"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _bucket_yearly(patent_dates: list[str]) -> list[tuple[date, float]]:
    """[grant_date 'YYYY-MM-DD'] -> [(year_end, count)]. Point-in-time: a year's grant count is
    knowable at year-end. Used once a keyed fetch returns real dated grants."""
    by_year: dict[int, int] = defaultdict(int)
    for s in patent_dates:
        try:
            by_year[date.fromisoformat(s[:10]).year] += 1
        except ValueError:
            continue
    return [(date(y, 12, 31), float(c)) for y, c in sorted(by_year.items())]


def _write_status(payload: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps(
            {
                "feed": "patentsview",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                **payload,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def collect(*, log=print) -> dict:
    """Fetch patent-grant counts by CPC class if keyless access exists.

    HONEST: the keyless source is retired and the successor requires a key, so the blocked path writes
    only `patentsview.status.json` and never emits fabricated observations.
    """
    reachable, detail = _probe_keyless()
    if not reachable:
        log("PatentsView keyless Search API is RETIRED / unreachable.")
        log(f"  probe: GET {PATENTSVIEW_SEARCH} -> {detail}")
        log("  successor = USPTO Open Data Portal (https://api.uspto.gov/...) which REQUIRES an API key")
        log("  (no-key -> HTTP 401 Unauthorized; invalid-key -> HTTP 403 Forbidden).")
        log("  Refusing to fabricate counts. Set USPTO_ODP_KEY + wire the ODP schema to populate.")
        _write_status({
            "works": False,
            "needs_key": True,
            "rows": 0,
            "reason": "USPTO ODP requires an API key; keyless PatentsView endpoint is retired.",
            "detail": detail,
        })
        return {"works": False, "needs_key": True, "obs": 0, "detail": detail}

    # Reached only if the keyless API is ever restored: real keyless fetch + write.
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    n_obs = 0
    rows: list[dict] = []
    for cpc, title in CPC_CLASSES:
        body = _build_query(cpc, WINDOW_START, "2026-01-01")
        url = (
            f"{PATENTSVIEW_SEARCH}?q={urllib.parse.quote(json.dumps(body['q']))}"
            f"&f={urllib.parse.quote(json.dumps(body['f']))}"
            f"&o={urllib.parse.quote(json.dumps(body['o']))}"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = json.loads(urllib.request.urlopen(req, timeout=40).read())  # noqa: S310
        except Exception as e:  # noqa: BLE001
            log(f"  - skip {cpc} ({type(e).__name__})")
            continue
        dates = [p.get("patent_date", "") for p in (data.get("patents") or [])]
        for d, c in _bucket_yearly(dates):
            rows.append({
                "series_id": f"patentsview_cpc_{cpc}",
                "date": d.isoformat(),
                "value": c,
                "unit": "patents_granted_per_year",
                "title": f"USPTO patent grants — CPC {cpc} ({title})",
            })
            n_obs += 1
    with OUT_PATH.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    for r in rows[:5]:
        log(json.dumps(r))
    return {"works": n_obs > 0, "needs_key": False, "obs": n_obs}


if __name__ == "__main__":
    result = collect()
    print(json.dumps(result, indent=2))
