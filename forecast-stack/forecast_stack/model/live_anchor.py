"""Live market re-anchoring for the ForecastBench market half.

The question set freezes crowd values ~10 days before the forecast due date
(e.g. 2026-07-19 round froze 2026-07-09). Anchoring on the freeze value throws
away everything the markets learned since. This module refreshes the anchor
from the source platforms' public keyless APIs at build time.

Leak discipline:
- Activates only when the round's due date is today (UTC). Backtests replay
  past rounds and therefore never fetch live values. Env override:
  FORECASTBENCH_LIVE_ANCHOR=1 forces on, =0 forces off.
- The refreshed value replaces `freeze_datetime_value` IN MEMORY only; question
  set files on disk are never rewritten. Primary and calibration-hedge builds
  explicitly reuse the same cached live snapshot.

Safety:
- Any fetch failure, unparseable payload, or out-of-range value -> keep freeze.
- |logit(live) - logit(freeze)| clamped to +/-3.0 (a 0.50 anchor can move to
  ~0.95, never to 0.999 on one bad tick).
- Results cached per due date on disk so the mechanical build, the research-leg
  worklist, and any retry share one snapshot. A late-day refresh pass
  (FORECASTBENCH_LIVE_ANCHOR_REFRESH=1) refetches everything and keeps the old
  snapshot value wherever a refetch fails.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR

from ..eval.score import MARKET_SOURCES

CACHE = DATA_DIR / "forecastbench" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (forecastbench-bot; research)"}
MAX_LOGIT_SHIFT = 3.0
TIMEOUT = 12


def _logit(p: float) -> float:
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def _get_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _fetch_manifold(qid: str) -> float | None:
    j = _get_json(f"https://api.manifold.markets/v0/market/{urllib.parse.quote(str(qid))}")
    p = j.get("probability")
    return float(p) if isinstance(p, (int, float)) else None


def _metaculus_token() -> str | None:
    tok = os.getenv("METACULUS_TOKEN")
    if tok:
        return tok
    envp = Path(__file__).resolve().parents[2] / ".env"
    try:
        for line in envp.read_text().splitlines():
            if line.startswith("METACULUS_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return None


def _fetch_metaculus(qid) -> float | None:
    """Authenticated /api/posts/<id>/ — keyless reads are 403-walled since 2026-06
    and unauthenticated aggregations come back empty (see engine/metaculus/api.py)."""
    headers = dict(UA)
    tok = _metaculus_token()
    if tok:
        headers["Authorization"] = f"Token {tok}"
    req = urllib.request.Request(f"https://www.metaculus.com/api/posts/{qid}/", headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        j = json.loads(r.read())
    q = j.get("question") or j
    aggs = (q or {}).get("aggregations") or {}
    for key in ("recency_weighted", "metaculus_prediction", "unweighted"):
        latest = (aggs.get(key) or {}).get("latest") or {}
        centers = latest.get("centers") or latest.get("forecast_values")
        if centers:
            try:
                return float(centers[-1])
            except (TypeError, ValueError):
                pass
    return None


def _fetch_polymarket(qid: str) -> float | None:
    j = _get_json("https://gamma-api.polymarket.com/markets?"
                  + urllib.parse.urlencode({"condition_ids": str(qid)}))
    if not isinstance(j, list) or not j:
        return None
    m = j[0]
    outcomes = m.get("outcomes")
    prices = m.get("outcomePrices")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(prices, str):
        prices = json.loads(prices)
    if not outcomes or not prices or len(outcomes) != len(prices):
        return None
    for o, p in zip(outcomes, prices):
        if str(o).strip().lower() == "yes":
            return float(p)
    return None


def _fetch_infer(qid) -> float | None:
    """Public RFI question page (keyless; the JSON API needs a key). The page
    embeds the question object in data-react-props; binary questions carry
    Yes/No answers with the live crowd probability."""
    url = ("https://www.randforecastinginitiative.org/questions/"
           + urllib.parse.quote(str(qid)))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        page = r.read().decode("utf-8", "ignore")
    for m in re.finditer(r'data-react-props="([^"]+)"', page):
        try:
            props = json.loads(html.unescape(m.group(1)))
        except Exception:
            continue
        q = props.get("question") if isinstance(props, dict) else None
        answers = (q or {}).get("answers")
        if not isinstance(answers, list):
            continue
        for a in answers:
            if isinstance(a, dict) and str(a.get("name", "")).strip().lower() == "yes":
                p = a.get("probability")
                return float(p) if isinstance(p, (int, float)) else None
    return None


_FETCHERS = {
    "manifold": _fetch_manifold,
    "metaculus": _fetch_metaculus,
    "polymarket": _fetch_polymarket,
    "infer": _fetch_infer,
}


def _fetch_one(src: str, qid) -> float | None:
    fn = _FETCHERS.get(src)
    if fn is None:
        return None
    try:
        p = fn(qid)
    except Exception:
        return None
    if p is None or not (0.0 <= p <= 1.0):
        return None
    return p


def enabled(due_str: str) -> bool:
    env = os.getenv("FORECASTBENCH_LIVE_ANCHOR")
    if env == "1":
        return True
    if env == "0":
        return False
    return str(due_str) == datetime.now(timezone.utc).date().isoformat()


def fetch_round(questions, due_str: str, workers: int = 8, refresh: bool = False) -> dict:
    """{(source, id): live_p} for market singles, disk-cached per due date.

    refresh=True (or FORECASTBENCH_LIVE_ANCHOR_REFRESH=1) refetches every
    fetchable key; wherever the refetch fails the previous snapshot value is
    kept — a late re-anchor can only get fresher, never fall back to freeze."""
    refresh = refresh or os.getenv("FORECASTBENCH_LIVE_ANCHOR_REFRESH") == "1"
    cf = CACHE / f"live_anchor_{due_str}.json"
    cached = {}
    if cf.exists():
        try:
            cached = {tuple(k.split("\u241f", 1)): v for k, v in json.loads(cf.read_text()).items()}
        except Exception:
            cached = {}
    # Metaculus' authenticated API can intentionally hide community aggregates
    # that its public server-rendered question page exposes. A due-date-specific
    # browser recovery sidecar restores those values without weakening the
    # ordinary API/freeze fallback for other rounds.
    recovered = CACHE / f"metaculus_rsc_{due_str}.json"
    if recovered.exists():
        try:
            for raw_key, raw_p in json.loads(recovered.read_text()).items():
                key = tuple(raw_key.split("\u241f", 1))
                p = float(raw_p)
                if len(key) == 2 and key[0] == "metaculus" and 0.0 <= p <= 1.0:
                    cached[key] = p
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    todo = []
    for q in questions:
        if isinstance(q["id"], list) or q["source"] not in MARKET_SOURCES:
            continue
        key = (q["source"], str(q["id"]))
        if q["source"] in _FETCHERS and (refresh or key not in cached):
            todo.append((key, q))
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for key, p in zip((k for k, _ in todo),
                              ex.map(lambda t: _fetch_one(t[0][0], t[1]["id"]), todo)):
                cached[key] = p if p is not None else cached.get(key)
        try:
            cf.write_text(json.dumps({"\u241f".join(k): v for k, v in cached.items()}))
        except Exception:
            pass
    return cached


def apply(questions, due_str: str) -> int:
    """Swap freeze_datetime_value -> live value in memory. Returns #refreshed."""
    if not enabled(due_str):
        return 0
    live = fetch_round(questions, due_str)
    n = 0
    for q in questions:
        if isinstance(q["id"], list) or q["source"] not in MARKET_SOURCES:
            continue
        p = live.get((q["source"], str(q["id"])))
        if p is None:
            continue
        try:
            fz = float(q.get("freeze_datetime_value"))
        except (TypeError, ValueError):
            fz = None
        if fz is not None and 0.0 <= fz <= 1.0:
            z = _logit(fz) + max(-MAX_LOGIT_SHIFT, min(MAX_LOGIT_SHIFT, _logit(p) - _logit(fz)))
            p = _sigmoid(z)
        q["freeze_datetime_value"] = p
        n += 1
    if n:
        print(f"  live anchor: refreshed {n} market crowd values (due {due_str})")
    return n
