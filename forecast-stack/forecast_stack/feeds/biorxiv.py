"""bioRxiv / medRxiv preprint-count API — keyless life-science research-effort collector.

A self-contained KEYLESS collector for Vati's data layer. The bioRxiv/medRxiv public API
(https://api.biorxiv.org) is open, no API key, and the date-range details endpoint
(https://api.biorxiv.org/details/<server>/<YYYY-MM-DD>/<YYYY-MM-DD>/<cursor>) returns, in
`messages[0]`, the count of NEW preprints posted in that window (`count_new_papers`) plus a
`total` that also counts later revisions. This module queries one window per year per server
(biorxiv + medrxiv), reads the NEW-preprint count, and writes annual posting counts to
data/feeds/biorxiv.jsonl. value = NEW preprints posted that year (revisions excluded).

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every observation carries its REAL posting date — preprints are dated by when they were
    POSTED, so `date` = December 31 of the posting year (the point in time the year's full
    count is knowable). Nothing is synthesized, backfilled, or interpolated: a year/server that
    returns no count (e.g. medRxiv before it launched in 2019, or a fetch failure) is DROPPED,
    never filled, so the jsonl is only ground-truth posted-preprint counts.
  • Unlike journal publication, a preprint is posted BEFORE peer review and journal acceptance
    (months to >1 year earlier). So as a forecasting signal this is a LEADING channel: a rise in
    posting volume in a life-science area is an early indicator of where effort/attention is
    migrating, observable well before the journal-publication record confirms it. We count NEW
    preprints (not `total`, which inflates with revisions) to keep it an honest effort proxy.

normalized observation shape (one JSON object per jsonl line):
  {series_id:'biorxiv:<server>', date:'YYYY-12-31', value:int, unit:'preprints/year', title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/biorxiv.py
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
BIORXIV_BASE = "https://api.biorxiv.org"
OUT_PATH = DATA_DIR / "feeds" / "biorxiv.jsonl"

# The two servers the API serves. bioRxiv (biology) launched 2013; medRxiv (medicine) launched
# 2019 — so its pre-2019 windows correctly return no count and are dropped (never backfilled).
SERVERS: list[tuple[str, str]] = [
    ("biorxiv", "bioRxiv"),
    ("medrxiv", "medRxiv"),
]

YEARS = list(range(2015, 2026))  # ~2015–2025 inclusive


def _fetch_json(url: str, *, retries: int = 2):
    """GET a keyless bioRxiv API URL → parsed JSON. Returns None on persistent failure (never fakes).
    Parses with strict=False: the `collection` array carries raw control chars inside abstracts that
    would otherwise break a strict JSON load — we only need the `messages` block regardless."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 keyless public endpoint
                return json.loads(resp.read().decode("utf-8", "replace"), strict=False)
        except Exception:  # noqa: BLE001 — network/parse/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None


def fetch_year_count(server: str, year: int, *, retries: int = 2) -> int | None:
    """Fetch the NEW-preprint count for one (server, year) window from the details endpoint.
    Returns int count, or None if the API has no count for that window (DROP it — never backfill)."""
    url = f"{BIORXIV_BASE}/details/{server}/{year}-01-01/{year}-12-31/0"
    data = _fetch_json(url, retries=retries)
    if not isinstance(data, dict):
        return None
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    msg = messages[0]
    if not isinstance(msg, dict) or msg.get("status") != "ok":
        return None
    raw = msg.get("count_new_papers")  # NEW preprints posted in-window (revisions excluded)
    if raw is None:
        return None
    try:
        count = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return count if count > 0 else None


def collect(*, log=print) -> list[dict]:
    """Fetch annual NEW-preprint counts for each (server × year) keyless, write the jsonl. Returns
    the list of observations actually written. $0. Never fabricates: a window with no count is
    logged and skipped, not filled."""
    all_obs: list[dict] = []
    for server, name in SERVERS:
        server_obs: list[dict] = []
        for year in YEARS:
            count = fetch_year_count(server, year)
            time.sleep(0.3)
            if count is None:
                log(f"  - skip {server} {year} (no count returned — likely pre-launch or fetch fail)")
                continue
            server_obs.append({
                "series_id": f"biorxiv:{server}",
                "date": f"{year}-12-31",       # REAL posting year, point-in-time (full-year count)
                "value": count,
                "unit": "preprints/year",
                "title": f"{name} preprints — all",
            })
        if server_obs:
            all_obs.extend(server_obs)
            log(f"  + {name:<8} {server_obs[0]['date'][:4]}–{server_obs[-1]['date'][:4]}  "
                f"{len(server_obs)} yrs")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in all_obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — bioRxiv API unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
