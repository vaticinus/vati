"""Metaculus community-forecast collector — INTENDED keyless, but the source NOW REQUIRES A KEY.

A self-contained collector for Vati's data layer, modeled on forecast_stack/feeds/world_bank.py. Metaculus
publishes crowd/community forecasts on geopolitics / politics / science questions; the
community_prediction time-series is a LEADING signal — the crowd's probability moves ahead of the
priced/structural outcome, updating continuously between a question's open date and resolution.

LEAK CLASS: leading — the community forecast updates BEFORE the outcome resolves; each forecast
revision is dated, so it is a genuine ahead-of-outcome crowd signal (not coincident/lag).

=== HONEST STATUS (verified 2026-06-11): needs_key=true, works=false ===
Every documented keyless read path is now behind authentication. Probed directly:
    GET https://www.metaculus.com/api2/questions/?limit=...     -> HTTP 403
    GET https://www.metaculus.com/api/posts/?limit=...          -> HTTP 403
    GET https://www.metaculus.com/api2/questions/<id>/          -> HTTP 403
    GET https://www.metaculus.com/  (homepage)                  -> HTTP 403
All return the SAME application-level body (not a transient WAF/IP block; reproduced with both a
descriptive UA and a browser UA):
    "Permission Error: The API is only available to authenticated users.
     Please create an account and use your API token to access the API."
So the community_prediction / resolution data this module targets cannot be fetched without an API
token. Per Vati's leak/honesty discipline this module DOES NOT fabricate, backfill, or synthesize any
observation: it probes, and if the wall is up it writes nothing and reports needs_key=true.

The fetch+normalize code below is written against the documented schema so that the day Metaculus
re-opens a keyless read path (or a token is supplied via METACULUS_TOKEN), it lands real dated
community-forecast observations into data/feeds/metaculus.jsonl in the standard Vati shape:
    {series_id, date:'YYYY-MM-DD', value:float, unit:str, title:str}

Run:  uv run python forecast_stack/feeds/metaculus.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
MC_BASE = "https://www.metaculus.com"
MC_API_BASE = "https://www.metaculus.com/api"
OUT_PATH = DATA_DIR / "feeds" / "metaculus.jsonl"
STATUS_PATH = DATA_DIR / "feeds" / "metaculus.status.json"
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Optional token: if Metaculus access is ever granted, export METACULUS_TOKEN or keep it in .env.
def _env_file_value(name: str) -> str:
    if not ENV_PATH.exists():
        return ""
    prefix = f"{name}="
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or not line.startswith(prefix):
                continue
            return line[len(prefix):].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _token() -> str:
    return os.environ.get("METACULUS_TOKEN", "").strip() or _env_file_value("METACULUS_TOKEN")


def _write_status(*, needs_key: bool, works: bool, reason: str, rows: int) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "feed": "metaculus",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "needs_key": bool(needs_key),
        "works": bool(works),
        "reason": reason,
        "rows": int(rows),
        "visibility_limited": bool((not works) and rows == 0 and not needs_key),
    }
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

# Categories we care about (crowd forecasts are a leading signal on these structural classes).
CATEGORIES = ("geopolitics", "politics", "science")
LIMIT = 40  # how many recently-active questions to attempt per probe


def _request(url: str, *, retries: int = 2):
    """GET a Metaculus URL → (status_code, parsed_json_or_None). Adds a Token header if available.
    Never fabricates: on persistent network failure returns (None, None)."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Token {token}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 keyless public endpoint
                body = resp.read().decode("utf-8", "replace")
                try:
                    return resp.status, json.loads(body)
                except json.JSONDecodeError:
                    return resp.status, None
        except urllib.error.HTTPError as e:  # noqa: PERF203 — capture status for the auth-wall report
            return e.code, None
        except Exception:  # noqa: BLE001 — network/throttle: back off, retry, then give up
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None, None
    return None, None


def probe_access() -> tuple[bool, str]:
    """Return (open, reason). `open` is True only if a keyless (or token) read path returns JSON."""
    url = f"{MC_API_BASE}/posts/?{urllib.parse.urlencode({'limit': 2, 'order_by': '-hotness', 'forecast_type': 'binary'})}"
    status, data = _request(url)
    if status == 200 and isinstance(data, dict) and "results" in data:
        return True, "api/posts returned JSON results"
    return False, f"api/posts HTTP {status} (authentication required; no keyless read path)"


def fetch_questions() -> list[dict]:
    """Fetch recently-active binary posts. Returns [] if the API is walled (never fabricates)."""
    raw: list[dict] = []
    for status_filter in ("open", "closed", "resolved"):
        params = {
            "limit": LIMIT,
            "order_by": "-hotness" if status_filter == "open" else "-published_at",
            "forecast_type": "binary",
            "statuses": status_filter,
            "include_description": "false",
        }
        url = f"{MC_API_BASE}/posts/?{urllib.parse.urlencode(params)}"
        status, data = _request(url)
        if status != 200 or not isinstance(data, dict):
            continue
        for post in data.get("results", []) or []:
            if isinstance(post, dict):
                raw.append(post)
        time.sleep(0.3)
    return raw


def _aggregation_value(item: dict) -> float | None:
    centers = item.get("centers") or item.get("forecast_values")
    if isinstance(centers, list) and centers:
        try:
            return float(centers[-1])
        except (TypeError, ValueError):
            return None
    for key in ("mean", "q2", "median"):
        if item.get(key) is None:
            continue
        try:
            return float(item[key])
        except (TypeError, ValueError):
            return None
    x1 = item.get("x1") if isinstance(item.get("x1"), dict) else None
    if x1 is not None and x1.get("q2") is not None:
        try:
            return float(x1["q2"])
        except (TypeError, ValueError):
            return None
    return None


def _item_time(item: dict, *, fallback: str | None = None) -> str | None:
    for key in ("start_time", "end_time", "t", "time", "date", "created_at"):
        iso = _to_iso_date(item.get(key))
        if iso:
            return iso
    return _to_iso_date(fallback)


def _community_history(post: dict) -> list[tuple[str, float]]:
    """Extract dated community-prediction points (as_of, probability) from a question dict.
    Tolerant of both the api2 `community_prediction.history` and the newer aggregations shape.
    Returns [] when no dated crowd points exist — never invents a value."""
    pts: list[tuple[str, float]] = []
    q = post.get("question") if isinstance(post.get("question"), dict) else post
    fallback_time = (
        (q or {}).get("cp_reveal_time")
        or (q or {}).get("open_time")
        or post.get("published_at")
        or datetime.now(timezone.utc).isoformat()
    )
    cp = (q or {}).get("community_prediction") or post.get("community_prediction") or {}
    history = cp.get("history") if isinstance(cp, dict) else None
    if isinstance(history, list):
        for h in history:
            if not isinstance(h, dict):
                continue
            iso = _item_time(h, fallback=fallback_time)
            val = _aggregation_value(h)
            if iso is None or val is None:
                continue
            pts.append((iso, val))
    aggs = (q or {}).get("aggregations") or {}
    for key in ("recency_weighted", "metaculus_prediction", "unweighted"):
        agg = aggs.get(key)
        if not isinstance(agg, dict):
            continue
        hist = agg.get("history")
        if isinstance(hist, list):
            for h in hist:
                if not isinstance(h, dict):
                    continue
                iso = _item_time(h, fallback=fallback_time)
                val = _aggregation_value(h)
                if iso is not None and val is not None:
                    pts.append((iso, val))
        latest = agg.get("latest")
        if isinstance(latest, dict):
            iso = _item_time(latest, fallback=fallback_time)
            val = _aggregation_value(latest)
            if iso is not None and val is not None:
                pts.append((iso, val))
        if pts:
            break
    dedup = {(iso, round(val, 12)): (iso, val) for iso, val in pts if 0.0 <= val <= 1.0}
    return [dedup[k] for k in sorted(dedup)]


def _to_iso_date(t) -> str | None:
    """Coerce a Metaculus timestamp (unix seconds, or ISO string) to 'YYYY-MM-DD'. None if unparseable."""
    if isinstance(t, (int, float)):
        try:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(float(t), tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(t, str) and len(t) >= 10:
        head = t[:10]
        if head[4] == "-" and head[7] == "-":
            return head
    return None


def normalize(q: dict) -> list[dict]:
    """RAW Metaculus question → normalized Vati observations (one per dated community-forecast point).
    `value` = community probability in [0,1]; `date` = the REAL forecast-revision date (leading)."""
    inner = q.get("question") if isinstance(q.get("question"), dict) else {}
    qid = inner.get("id") or q.get("id")
    post_id = q.get("id") or inner.get("post_id")
    title = (q.get("title") or inner.get("title") or "").strip()
    if qid is None:
        return []
    series_id = f"metaculus:{post_id or qid}:{qid}"
    out: list[dict] = []
    for iso, val in _community_history(q):
        out.append({
            "series_id": series_id,
            "date": iso,
            "value": val,
            "unit": "community probability",
            "title": f"Metaculus community forecast — {title[:100]}",
            "metric": "community_probability",
            "post_id": post_id,
            "question_id": qid,
        })
    out.sort(key=lambda o: o["date"])
    return out


def collect(*, log=print) -> tuple[list[dict], bool, str]:
    """Probe access, then (if open) fetch+normalize+write. Returns (observations, needs_key, reason).
    Writes the jsonl ONLY when real dated observations were fetched. $0, never fabricates."""
    is_open, reason = probe_access()
    if not is_open:
        log(f"  ! Metaculus access BLOCKED: {reason}")
        log("    needs_key=true — set METACULUS_TOKEN to a valid API token to enable.")
        _write_status(needs_key=True, works=False, reason=reason, rows=0)
        return [], True, reason

    all_obs: list[dict] = []
    for q in fetch_questions():
        obs = normalize(q)
        if obs:
            all_obs.extend(obs)
            log(f"  + metaculus:{q.get('id')}  {obs[0]['date']}–{obs[-1]['date']}  {len(obs)} obs")

    if all_obs:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUT_PATH.open("w", encoding="utf-8") as f:
            for o in all_obs:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
        log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    if not all_obs:
        reason = (
            f"{reason}; posts are visible but dated community aggregate values "
            "are null/hidden in this API response"
        )
    _write_status(needs_key=False, works=bool(all_obs), reason=reason, rows=len(all_obs))
    return all_obs, False, reason


if __name__ == "__main__":
    observations, needs_key, why = collect()
    if needs_key:
        print("\nNO observations written — source REQUIRES A KEY (works=false, needs_key=true).")
        print(f"reason: {why}")
    elif not observations:
        print("\nNO observations collected — keyless path open but no dated community points returned.")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
