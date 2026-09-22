"""Polymarket prediction-market — keyless crowd-implied-probability collector.

A self-contained KEYLESS collector for Vati's data layer. Polymarket exposes two open,
no-API-key endpoints used here:

  • Gamma API   https://gamma-api.polymarket.com/markets?closed=false&tag_id=<id>&...
      lists ACTIVE markets with metadata (question, volume, the CLOB token ids, and the
      current `outcomePrices`). We filter to the geopolitics/politics question class by tag
      (Politics=2, Geopolitics=100265, Elections=144, World=101970) and rank by traded volume.
  • CLOB API    https://clob.polymarket.com/prices-history?market=<clobTokenId>&interval=max&fidelity=1440
      returns the REAL daily mid-price history (one `{t,p}` point per `fidelity` minutes) for a
      single outcome token. This is where the time-depth comes from: each point is a genuine,
      dated, market-clearing price = the crowd's implied probability of the YES outcome on that day.

So this feed is NOT a single snapshot — for each market we land a multi-month DAILY series of the
crowd-implied probability of the YES outcome (typically 100s of dated points spanning the market's
life). We also emit the current 24h-traded-volume as a coincident liquidity/attention reading.

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every probability point carries its REAL trade date — `date` = the UTC day of the CLOB price
    timestamp `t`. Nothing is synthesized, backfilled, or interpolated: the daily history is exactly
    what the order book cleared at. A market we cannot fetch history for is SKIPPED, not filled.
  • Leak class = LEADING. A prediction market's implied probability is a forward-looking crowd
    aggregate that re-prices on new information BEFORE the modeled outcome resolves — it moves ahead
    of the priced event, the canonical leading crowd signal. (The volume reading is coincident:
    attention as it happens.) Caveat held honestly: only ACTIVE markets are read, so this is a live
    leading channel, not a settled-outcome archive.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit:str, title:str}

$0, keyless. Run directly:  uv run python forecast_stack/feeds/polymarket.py
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
OUT_PATH = DATA_DIR / "feeds" / "polymarket.jsonl"

# Geopolitics / politics question class, by Gamma tag id (verified live, resolved via /tags/slug/<slug>).
TAGS: list[tuple[int, str]] = [
    (100265, "Geopolitics"),
    (2, "Politics"),
    (144, "Elections"),
    (101970, "World"),
]

MARKETS_PER_TAG = 4       # top markets by traded volume per tag
FIDELITY_MIN = 1440       # CLOB history resolution: 1440 min = 1 point/day (REAL daily prices)
MIN_VOLUME = 5000.0       # ignore thinly-traded markets (noisy / illiquid implied prob)


def _fetch_json(url: str, *, retries: int = 2):
    """GET a keyless Polymarket URL → parsed JSON. Returns None on persistent failure (never fakes)."""
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


def fetch_markets(tag_id: int, *, limit: int) -> list[dict]:
    """List ACTIVE markets for a tag, ranked by traded volume desc. `related_tags=true` widens the
    tag to its sibling topics so the geopolitics/politics class is well-populated."""
    params = {
        "closed": "false",
        "active": "true",
        "archived": "false",
        "tag_id": tag_id,
        "related_tags": "true",
        "order": "volumeNum",
        "ascending": "false",
        "limit": limit * 4,  # over-fetch; we filter by liquidity + history availability below
    }
    url = f"{GAMMA_BASE}/markets?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url)
    return data if isinstance(data, list) else []


def fetch_price_history(clob_token_id: str) -> list[dict]:
    """Fetch the REAL daily mid-price history for one outcome token → list of {t,p} points.
    interval=max spans the market's full life; fidelity gives ~1 point/day. Empty on failure."""
    params = {"market": clob_token_id, "interval": "max", "fidelity": FIDELITY_MIN}
    url = f"{CLOB_BASE}/prices-history?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url)
    if isinstance(data, dict):
        hist = data.get("history")
        if isinstance(hist, list):
            return hist
    return []


def _yes_token_id(market: dict) -> str | None:
    """The CLOB token id of the YES outcome. `clobTokenIds` is a JSON-encoded string list parallel to
    `outcomes` (["Yes","No"]). We take the YES leg so probability rises = event more likely."""
    raw_ids = market.get("clobTokenIds")
    raw_outcomes = market.get("outcomes")
    try:
        ids = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(ids, list) or not ids:
        return None
    if isinstance(outcomes, list) and len(outcomes) == len(ids):
        for tok, name in zip(ids, outcomes):
            if str(name).strip().lower() in ("yes", "true"):
                return str(tok)
    return str(ids[0])  # binary fallback: first leg


def normalize_history(market: dict, hist: list[dict]) -> list[dict]:
    """RAW CLOB {t,p} points → normalized Vati probability observations. `date` = UTC day of the trade
    timestamp; value = the crowd-implied probability (0–1) of the YES outcome on that day."""
    cond = str(market.get("conditionId") or market.get("id"))
    question = str(market.get("question") or "Polymarket market")[:120]
    series_id = f"polymarket:prob:{cond}"
    out: list[dict] = []
    seen_days: set[str] = set()
    for pt in hist:
        try:
            t = int(pt["t"])
            p = float(pt["p"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0.0 <= p <= 1.0):
            continue
        day = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        if day in seen_days:  # one point per day (dedupe sub-daily ticks); keep first of the day
            continue
        seen_days.add(day)
        out.append({
            "series_id": series_id,
            "date": day,                              # REAL trade date (point-in-time)
            "value": round(p, 6),
            "unit": "implied probability (0-1)",
            "title": f"Polymarket implied P(Yes) — {question}",
        })
    out.sort(key=lambda o: o["date"])
    return out


def volume_observation(market: dict) -> dict | None:
    """Coincident liquidity reading: the market's current cumulative traded volume, dated TODAY (UTC),
    the point-in-time it is observed. Skipped if volume is missing."""
    try:
        vol = float(market.get("volumeNum") or market.get("volume"))
    except (TypeError, ValueError):
        return None
    cond = str(market.get("conditionId") or market.get("id"))
    question = str(market.get("question") or "Polymarket market")[:120]
    return {
        "series_id": f"polymarket:volume:{cond}",
        "date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        "value": round(vol, 2),
        "unit": "USD cumulative traded volume",
        "title": f"Polymarket traded volume — {question}",
    }


def collect(*, log=print) -> list[dict]:
    """Fetch top geopolitics/politics markets per tag keyless, pull each one's REAL daily implied-prob
    history from CLOB, normalize, write the jsonl. Returns observations written. $0. Never fabricates:
    a market with no fetchable history is logged and skipped, not filled."""
    all_obs: list[dict] = []
    seen_conditions: set[str] = set()
    for tag_id, tag_name in TAGS:
        markets = fetch_markets(tag_id, limit=MARKETS_PER_TAG)
        kept = 0
        for m in markets:
            if kept >= MARKETS_PER_TAG:
                break
            cond = str(m.get("conditionId") or m.get("id"))
            if cond in seen_conditions:
                continue
            try:
                vol = float(m.get("volumeNum") or 0)
            except (TypeError, ValueError):
                vol = 0.0
            if vol < MIN_VOLUME:
                continue
            tok = _yes_token_id(m)
            if not tok:
                continue
            hist = fetch_price_history(tok)
            obs = normalize_history(m, hist)
            if not obs:
                log(f"  - skip [{tag_name}] {str(m.get('question'))[:60]!r} (no price history)")
                time.sleep(0.2)
                continue
            seen_conditions.add(cond)
            all_obs.extend(obs)
            vob = volume_observation(m)
            if vob:
                all_obs.append(vob)
            kept += 1
            log(f"  + [{tag_name:11s}] {obs[0]['date']}–{obs[-1]['date']}  {len(obs):3d} prob-pts  "
                f"vol=${vol:,.0f}  {str(m.get('question'))[:55]!r}")
            time.sleep(0.25)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for o in all_obs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — Polymarket API unreachable this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
