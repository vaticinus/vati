"""GDELT 2.0 DOC API — keyless global news/event attention + tone time-series collector.

A self-contained KEYLESS collector for Vati's data layer. GDELT's DOC 2.0 API
(https://api.gdeltproject.org/api/v2/doc/doc) is open, no API key, and returns a DAILY
timeline of the worldwide news coverage for a query:
  • mode=timelinevol  → "Volume Intensity": the % of all monitored global articles per day that
    match the query (an ATTENTION signal — volume spikes WITH events).
  • mode=timelinetone → "Average Tone": the mean GDELT tone (roughly -100..+100, usually -10..+10)
    of matching articles per day (a SENTIMENT signal — tone can move ahead of escalation).

This module fetches a small basket of geopolitical THEMES (a conflict, an election, a sanctions
topic) across BOTH volume and tone, normalizes each daily point to a Vati observation, and writes
>=30 REAL dated observations to data/feeds/gdelt.jsonl.

Leak discipline (matches forecast_stack/feeds/world_bank.py):
  • Every observation carries its REAL article date (GDELT timestamps each daily point; we keep the
    UTC calendar day as `date`). Nothing is synthesized, backfilled, or interpolated: a point with a
    null/non-finite value or an unparseable date is DROPPED, never filled. The jsonl is only
    ground-truth points GDELT actually returned for the requested window.
  • Leak class is MIXED and signal-dependent:
      - VOLUME (attention) is COINCIDENT: coverage volume spikes WITH the event as it breaks/escalates;
        it confirms attention in real time, it does not lead the underlying outcome.
      - TONE (sentiment) is mildly LEADING: aggregate news tone often deteriorates ahead of visible
        escalation (the framing shifts before the priced outcome moves). We classify the feed overall
        as COINCIDENT (the dominant volume channel) and document tone as the leading sub-channel.

normalized observation shape (one JSON object per jsonl line):
  {series_id, date:'YYYY-MM-DD', value:float, unit:str, title:str}

Rate limit: GDELT asks for <=1 request every 5 seconds (it returns HTTP 429 otherwise). We sleep
between requests and retry 429s with backoff. $0, keyless. Run directly:
    uv run python forecast_stack/feeds/gdelt.py
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO, TextIOWrapper
from pathlib import Path
from forecast_stack.config import DATA_DIR
import csv

# A descriptive but browser-like UA; GDELT 429s aggressive/empty UAs.
UA = "forecast-stack (+https://github.com/vaticinus/vati)"
GDELT_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_LASTUPDATE = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
OUT_PATH = DATA_DIR / "feeds" / "gdelt.jsonl"

# GDELT politely requests <= 1 request / 5s. The IP can also fall into a longer penalty window when
# it has been hit recently, so stay well above the floor to let that window clear between requests.
REQUEST_SPACING_S = 6.0
REQUEST_TIMEOUT_S = 10
FETCH_RETRIES = 0
MIN_REFRESH_FRACTION = 0.8
EXPORT_FALLBACK_STEPS = 16

GDELT_ACTION_COUNTRIES: dict[str, tuple[str, str]] = {
    "US": ("United States", "united_states"),
    "CH": ("China", "china"),
    "IN": ("India", "india"),
    "JA": ("Japan", "japan"),
    "KS": ("South Korea", "south_korea"),
    "TW": ("Taiwan", "taiwan"),
    "GM": ("Germany", "germany"),
    "UK": ("United Kingdom", "united_kingdom"),
    "FR": ("France", "france"),
    "CA": ("Canada", "canada"),
    "AS": ("Australia", "australia"),
    "BR": ("Brazil", "brazil"),
    "MX": ("Mexico", "mexico"),
    "VM": ("Vietnam", "vietnam"),
    "ID": ("Indonesia", "indonesia"),
    "SA": ("Saudi Arabia", "saudi_arabia"),
    "AE": ("United Arab Emirates", "united_arab_emirates"),
    "RS": ("Russia", "russia"),
    "UP": ("Ukraine", "ukraine"),
    "SF": ("South Africa", "south_africa"),
    "CG": ("Democratic Republic of the Congo", "democratic_republic_of_the_congo"),
    "CI": ("Chile", "chile"),
    "AR": ("Argentina", "argentina"),
}


class GdeltRateLimited(RuntimeError):
    """Raised when GDELT returns its explicit request-rate notice."""

# Three geopolitical THEMES: a conflict, an election, a sanctions topic. `query` is GDELT DOC
# query syntax; `slug` is a short stable id for the series key.
THEMES: list[dict] = [
    {
        "slug": "ukraine_conflict",
        "query": '(Ukraine AND (war OR conflict OR offensive OR strike))',
        "title": "Ukraine conflict",
        "domain": "conflict",
    },
    {
        "slug": "us_election",
        "query": '("US election" OR "midterm election" OR "presidential election")',
        "title": "US election",
        "domain": "politics",
    },
    {
        "slug": "sanctions",
        "query": '(sanctions AND (Russia OR Iran OR China OR export))',
        "title": "Sanctions (geopolitical)",
        "domain": "geopolitics",
    },
]

# Two signal channels off the same query. Each: (mode, unit, signal-note for the title).
MODES: list[dict] = [
    {"mode": "timelinevol", "unit": "% of global articles", "signal": "volume"},
    {"mode": "timelinetone", "unit": "avg tone", "signal": "tone"},
]

# How far back to pull the daily timeline. 12 months of daily points = ~365 obs per (theme,mode).
TIMESPAN = "12m"


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def _read_existing_rows() -> list[dict]:
    if not OUT_PATH.exists():
        return []
    rows: list[dict] = []
    for line in OUT_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("series_id") and row.get("date"):
            rows.append(row)
    return rows


def _merge_rows(existing: list[dict], fresh: list[dict]) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for row in [*existing, *fresh]:
        key = (str(row.get("series_id")), str(row.get("date")))
        by_key[key] = row
    return sorted(by_key.values(), key=lambda r: (str(r.get("series_id")), str(r.get("date"))))


def _write_jsonl_atomic(rows: list[dict]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_bytes(url: str, *, timeout: int = REQUEST_TIMEOUT_S) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 keyless public endpoint
            return resp.read()
    except Exception:  # noqa: BLE001 — keyless public file can be late/missing
        return None


def _fetch_json(url: str, *, retries: int = FETCH_RETRIES, timeout: int = REQUEST_TIMEOUT_S):
    """GET a keyless GDELT DOC URL → parsed JSON. Retries 429/transient errors with backoff.
    Returns None on persistent failure (never fakes)."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 keyless public endpoint
                raw = resp.read().decode("utf-8", "replace")
            # GDELT sometimes returns an empty body or a plain-text rate-limit notice (not JSON).
            raw = raw.strip()
            if not raw or not raw.startswith("{"):
                if "limit requests to one every 5 seconds" in raw.lower():
                    if attempt < retries:
                        time.sleep(20.0 * (attempt + 1))
                        continue
                    raise GdeltRateLimited("GDELT explicit rate-limit notice")
                raise ValueError("non-JSON body (likely a rate-limit/notice page)")
            return json.loads(raw)
        except urllib.error.HTTPError as e:  # noqa: PERF203
            # 429 = rate limited: back off hard (the IP penalty window is several seconds) and retry.
            wait = 15.0 * (attempt + 1) if e.code == 429 else 2.0 * (attempt + 1)
            if attempt < retries:
                time.sleep(wait)
                continue
            return None
        except Exception:  # noqa: BLE001 — network/parse/throttle: back off, retry, then None
            if attempt < retries:
                time.sleep(3.0 * (attempt + 1))
                continue
            return None


def fetch_timeline(query: str, mode: str, *, retries: int = FETCH_RETRIES) -> list[dict]:
    """Fetch one (query, mode) daily timeline → list of RAW GDELT points [{date, value}, ...].
    GDELT returns {"timeline": [{"series": label, "data": [...]}, ...]}; we take the first series."""
    params = {"query": query, "mode": mode, "format": "json", "timespan": TIMESPAN}
    url = f"{GDELT_BASE}?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url, retries=retries)
    if not isinstance(data, dict):
        return []
    timeline = data.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        return []
    first = timeline[0] or {}
    points = first.get("data")
    return points if isinstance(points, list) else []


def _parse_gdelt_date(raw: str) -> str | None:
    """GDELT date is 'YYYYMMDDT HHMMSSZ' (e.g. '20260314T000000Z'). Return 'YYYY-MM-DD' or None."""
    s = (raw or "").strip()
    digits = s[:8]
    if len(digits) != 8 or not digits.isdigit():
        return None
    y, m, d = digits[0:4], digits[4:6], digits[6:8]
    if not ("0001" <= y) or not ("01" <= m <= "12") or not ("01" <= d <= "31"):
        return None
    return f"{y}-{m}-{d}"


def normalize(theme: dict, mode_spec: dict, raw_points: list[dict]) -> list[dict]:
    """RAW GDELT daily points → normalized Vati observations. `date` = the UTC calendar day GDELT
    timestamps the point. value cast to float; non-finite values DROPPED (never backfilled)."""
    series_id = f"gdelt:{theme['slug']}:{mode_spec['signal']}"
    out: list[dict] = []
    seen: set[str] = set()
    for p in raw_points:
        if not isinstance(p, dict):
            continue
        date = _parse_gdelt_date(str(p.get("date", "")))
        if date is None or date in seen:  # de-dup any repeated day; keep first
            continue
        try:
            value = float(p["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(value):  # DROP NaN/inf — never fabricate
            continue
        seen.add(date)
        out.append({
            "series_id": series_id,
            "date": date,
            "value": value,
            "unit": mode_spec["unit"],
            "title": f"{theme['title']} — GDELT {mode_spec['signal']}",
        })
    out.sort(key=lambda o: o["date"])  # chronological, latest last
    return out


def _latest_export_url() -> str | None:
    raw = _fetch_bytes(GDELT_LASTUPDATE, timeout=8)
    if not raw:
        return None
    for line in raw.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if parts and parts[-1].endswith(".export.CSV.zip"):
            return parts[-1]
    return None


def _recent_export_urls(latest_url: str, *, steps: int = EXPORT_FALLBACK_STEPS) -> list[str]:
    stamp = latest_url.rsplit("/", 1)[-1].split(".", 1)[0]
    try:
        latest = datetime.strptime(stamp, "%Y%m%d%H%M%S")
    except ValueError:
        return [latest_url]
    prefix = latest_url.rsplit("/", 1)[0]
    return [
        f"{prefix}/{(latest - timedelta(minutes=15 * i)).strftime('%Y%m%d%H%M%S')}.export.CSV.zip"
        for i in range(steps)
    ]


def _iter_export_rows(raw_zip: bytes):
    try:
        with zipfile.ZipFile(BytesIO(raw_zip)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".CSV")]
            if not names:
                return
            with zf.open(names[0]) as fh:
                reader = csv.reader(TextIOWrapper(fh, encoding="utf-8", errors="replace"), delimiter="\t")
                yield from reader
    except (OSError, zipfile.BadZipFile):
        return


def _date_from_dateadded(raw: str) -> str | None:
    digits = (raw or "").strip()[:8]
    if len(digits) != 8 or not digits.isdigit():
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _to_number(raw: str, default: float = 0.0) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _normalize_export_rows(rows) -> list[dict]:
    """Aggregate GDELT 2.0 event export rows into daily global/top-country observations.

    Uses DATEADDED (column 59) as the timestamped observation date. Event Day can be historical
    even when a current article mentions an old event, so DATEADDED is the leak-safe "news seen"
    time for this rolling feed.
    """

    by_date: dict[str, dict] = defaultdict(lambda: {
        "events": 0,
        "articles": 0.0,
        "hostile": 0,
        "tone_weighted": 0.0,
        "tone_weight": 0.0,
        "countries": defaultdict(int),
    })
    for row in rows:
        if len(row) < 60:
            continue
        iso = _date_from_dateadded(row[59])
        if iso is None:
            continue
        rec = by_date[iso]
        articles = max(1.0, _to_number(row[33], 1.0))
        tone = _to_number(row[34], 0.0)
        rec["events"] += 1
        rec["articles"] += articles
        rec["tone_weighted"] += tone * articles
        rec["tone_weight"] += articles
        if str(row[29]).strip() == "4":
            rec["hostile"] += 1
        country = str(row[53]).strip().upper()
        if country in GDELT_ACTION_COUNTRIES:
            rec["countries"][country] += 1

    out: list[dict] = []
    for iso, rec in sorted(by_date.items()):
        tone_weight = rec["tone_weight"] or 1.0
        out.extend([
            {
                "series_id": "gdelt_export:global:event_count",
                "date": iso,
                "value": float(rec["events"]),
                "unit": "events",
                "metric": "event_count",
                "title": "GDELT latest global event count",
            },
            {
                "series_id": "gdelt_export:global:article_mentions",
                "date": iso,
                "value": float(rec["articles"]),
                "unit": "articles",
                "metric": "news_articles",
                "title": "GDELT latest global article mentions",
            },
            {
                "series_id": "gdelt_export:global:hostile_event_count",
                "date": iso,
                "value": float(rec["hostile"]),
                "unit": "events",
                "metric": "hostile_event_count",
                "title": "GDELT latest global hostile event count",
            },
            {
                "series_id": "gdelt_export:global:avg_tone",
                "date": iso,
                "value": float(rec["tone_weighted"]) / float(tone_weight),
                "unit": "avg tone",
                "metric": "avg_tone",
                "title": "GDELT latest global event tone",
            },
        ])
        for country_code, count in sorted(rec["countries"].items()):
            name, slug = GDELT_ACTION_COUNTRIES[country_code]
            out.append({
                "series_id": f"gdelt_export:country:{slug}:event_count",
                "date": iso,
                "value": float(count),
                "unit": "events",
                "metric": "event_count",
                "title": f"GDELT latest events — {name}",
            })
    return out


def collect_recent_exports(*, log=print) -> list[dict]:
    latest = _latest_export_url()
    if latest is None:
        log("  - export fallback unavailable (lastupdate.txt unreachable)")
        return []
    rows = []
    files_seen = 0
    for url in _recent_export_urls(latest):
        raw = _fetch_bytes(url, timeout=12)
        if not raw:
            continue
        files_seen += 1
        rows.extend(_iter_export_rows(raw) or [])
    obs = _normalize_export_rows(rows)
    if obs:
        dates = sorted({o["date"] for o in obs})
        log(f"  + export fallback {files_seen} files  {dates[0]}–{dates[-1]}  {len(obs)} obs")
    else:
        log(f"  - export fallback fetched {files_seen} files but produced no observations")
    return obs


def collect(*, log=print) -> list[dict]:
    """Fetch all (theme × mode) timelines keyless, normalize, write the jsonl. Returns the list of
    observations actually written. $0. Never fabricates: a series that fails to fetch is logged and
    skipped, not filled. Honors GDELT's ~1-req/5s limit via REQUEST_SPACING_S between calls."""
    all_obs: list[dict] = []
    first_call = True
    for theme in THEMES:
        for mode_spec in MODES:
            if not first_call:
                time.sleep(REQUEST_SPACING_S)  # respect GDELT rate limit
            first_call = False
            try:
                raw = fetch_timeline(theme["query"], mode_spec["mode"])
            except GdeltRateLimited:
                existing = _existing_line_count()
                log(f"  ! GDELT rate-limited; preserved existing {existing} rows at {OUT_PATH}")
                return []
            obs = normalize(theme, mode_spec, raw)
            if not obs:
                log(f"  - skip {theme['slug']:18s} / {mode_spec['signal']:6s} (no dated points returned)")
                continue
            all_obs.extend(obs)
            log(f"  + {theme['domain']:<11} {theme['slug']:18s} {mode_spec['signal']:6s}  "
                f"{obs[0]['date']}–{obs[-1]['date']}  {len(obs)} obs")

    existing = _existing_line_count()
    if not all_obs:
        fallback_obs = collect_recent_exports(log=log)
        if fallback_obs:
            merged = _merge_rows(_read_existing_rows(), fallback_obs)
            _write_jsonl_atomic(merged)
            log(f"\nmerged {len(fallback_obs)} export observations; file now has {len(merged)} rows → {OUT_PATH}")
            return fallback_obs
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(all_obs) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial refresh fetched {len(all_obs)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(all_obs)
    log(f"\nwrote {len(all_obs)} observations → {OUT_PATH}")
    return all_obs


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("\nNO observations collected — GDELT DOC API unreachable/empty this run (no data written).")
    else:
        print(f"\nfirst {min(3, len(observations))} observations:")
        for o in observations[:3]:
            print("  " + json.dumps(o, ensure_ascii=False))
        # report the true count straight from the written file
        n_lines = sum(1 for _ in OUT_PATH.open(encoding="utf-8"))
        print(f"\njsonl line count: {n_lines}")
