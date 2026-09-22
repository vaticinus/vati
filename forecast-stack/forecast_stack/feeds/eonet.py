"""NASA EONET global natural-events collector.

EONET is NASA's keyless Earth Observatory Natural Event Tracker API. This collector turns the
event stream into date-stamped hazard-state series: daily event updates, daily new events, and
current open-event snapshots by category. Rows are capped at the local current date so future-dated
API records cannot leak into point-in-time forecasts.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
OUT_PATH = DATA_DIR / "feeds" / "eonet.jsonl"

REQUEST_TIMEOUT_S = 45
WINDOW_DAYS = 365
LIMIT = 10000
MIN_REFRESH_FRACTION = 0.5


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "unknown"


def _parse_date(raw: str | None) -> date | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        return datetime.fromisoformat(s[:19]).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def fetch_events(*, days: int = WINDOW_DAYS) -> list[dict[str, Any]]:
    qs = urllib.parse.urlencode({"days": str(days), "status": "all", "limit": str(LIMIT)})
    req = urllib.request.Request(
        f"{API_URL}?{qs}",
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
        data = json.loads(resp.read().decode("utf-8", "replace"))
    events = data.get("events") if isinstance(data, dict) else []
    return [e for e in events if isinstance(e, dict)]


def _categories(event: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for cat in event.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        title = str(cat.get("title") or cat.get("id") or "Unknown").strip()
        cid = _slug(str(cat.get("id") or title))
        out.append((cid, title))
    return out or [("unknown", "Unknown")]


def _event_dates(event: dict[str, Any]) -> list[date]:
    dates = []
    for geom in event.get("geometry") or []:
        if not isinstance(geom, dict):
            continue
        d = _parse_date(geom.get("date"))
        if d is not None:
            dates.append(d)
    return sorted(set(dates))


def _example(event: dict[str, Any], *, event_date: date | None = None) -> dict[str, Any]:
    coords = None
    magnitude_value = None
    magnitude_unit = None
    for geom in event.get("geometry") or []:
        if not isinstance(geom, dict):
            continue
        d = _parse_date(geom.get("date"))
        if event_date is None or d == event_date:
            coords = geom.get("coordinates")
            magnitude_value = geom.get("magnitudeValue")
            magnitude_unit = geom.get("magnitudeUnit")
            break
    return {
        "id": event.get("id"),
        "title": event.get("title"),
        "date": event_date.isoformat() if event_date else None,
        "closed": _parse_date(event.get("closed")).isoformat() if _parse_date(event.get("closed")) else None,
        "categories": [title for _cid, title in _categories(event)],
        "sources": [s.get("id") for s in event.get("sources") or [] if isinstance(s, dict) and s.get("id")],
        "coordinates": coords,
        "magnitude_value": magnitude_value,
        "magnitude_unit": magnitude_unit,
        "link": event.get("link"),
    }


def _append_example(bucket: dict[str, list[dict[str, Any]]], key: str, ex: dict[str, Any]) -> None:
    if len(bucket[key]) < 8:
        bucket[key].append(ex)


def normalize(
    events: list[dict[str, Any]],
    *,
    today: date | None = None,
    window_days: int = WINDOW_DAYS,
) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    since = today - timedelta(days=window_days)

    category_titles: dict[str, str] = {}
    updates: dict[tuple[str, str], set[str]] = defaultdict(set)
    new_events: dict[tuple[str, str], set[str]] = defaultdict(set)
    open_events: dict[str, set[str]] = defaultdict(set)
    update_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    new_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    open_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_update_by_date: dict[str, set[str]] = defaultdict(set)
    all_new_by_date: dict[str, set[str]] = defaultdict(set)
    all_open: set[str] = set()

    for event in events:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        cats = _categories(event)
        for cid, title in cats:
            category_titles[cid] = title
        all_dates = [d for d in _event_dates(event) if d <= today]
        if not all_dates:
            continue
        first_date = min(all_dates)
        closed_date = _parse_date(event.get("closed"))
        is_open = first_date <= today and (closed_date is None or closed_date > today)

        for d in all_dates:
            if d < since or d > today:
                continue
            ds = d.isoformat()
            all_update_by_date[ds].add(event_id)
            _append_example(update_examples, f"all|{ds}", _example(event, event_date=d))
            for cid, _title in cats:
                updates[(cid, ds)].add(event_id)
                _append_example(update_examples, f"{cid}|{ds}", _example(event, event_date=d))

        if since <= first_date <= today:
            ds = first_date.isoformat()
            all_new_by_date[ds].add(event_id)
            _append_example(new_examples, f"all|{ds}", _example(event, event_date=first_date))
            for cid, _title in cats:
                new_events[(cid, ds)].add(event_id)
                _append_example(new_examples, f"{cid}|{ds}", _example(event, event_date=first_date))

        if is_open:
            all_open.add(event_id)
            _append_example(open_examples, "all", _example(event))
            for cid, _title in cats:
                open_events[cid].add(event_id)
                _append_example(open_examples, cid, _example(event))

    rows: list[dict[str, Any]] = []

    def add_row(series_id: str, ds: str, value: int, unit: str, metric: str, title: str,
                examples: list[dict[str, Any]] | None = None) -> None:
        rows.append({
            "series_id": series_id,
            "date": ds,
            "value": float(value),
            "unit": unit,
            "metric": metric,
            "title": title,
            "examples": examples or [],
        })

    for ds, ids in sorted(all_update_by_date.items()):
        add_row(
            "eonet:all:event_updates", ds, len(ids), "events", "earth_event_updates",
            "NASA EONET — All categories event updates", update_examples.get(f"all|{ds}", []),
        )
    for ds, ids in sorted(all_new_by_date.items()):
        add_row(
            "eonet:all:new_events", ds, len(ids), "events", "earth_new_events",
            "NASA EONET — All categories new events", new_examples.get(f"all|{ds}", []),
        )
    add_row(
        "eonet:all:snapshot:open_events", today.isoformat(), len(all_open), "events", "earth_open_events",
        "NASA EONET — All categories current open events", open_examples.get("all", []),
    )

    for (cid, ds), ids in sorted(updates.items()):
        title = category_titles.get(cid, cid.replace("_", " ").title())
        add_row(
            f"eonet:{cid}:event_updates", ds, len(ids), "events", "earth_event_updates",
            f"NASA EONET — {title} event updates", update_examples.get(f"{cid}|{ds}", []),
        )
    for (cid, ds), ids in sorted(new_events.items()):
        title = category_titles.get(cid, cid.replace("_", " ").title())
        add_row(
            f"eonet:{cid}:new_events", ds, len(ids), "events", "earth_new_events",
            f"NASA EONET — {title} new events", new_examples.get(f"{cid}|{ds}", []),
        )
    for cid, ids in sorted(open_events.items()):
        title = category_titles.get(cid, cid.replace("_", " ").title())
        add_row(
            f"eonet:{cid}:snapshot:open_events", today.isoformat(), len(ids), "events", "earth_open_events",
            f"NASA EONET — {title} current open events", open_examples.get(cid, []),
        )

    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    try:
        events = fetch_events(days=WINDOW_DAYS)
    except Exception as exc:  # noqa: BLE001 — public endpoint; preserve last good file on failure
        log(f"EONET fetch failed: {exc}")
        return []
    rows = normalize(events)
    existing = _existing_line_count()
    if not rows:
        log(f"no observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"partial refresh fetched {len(rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(rows)
    dates = sorted({str(r["date"]) for r in rows})
    series = len({str(r["series_id"]) for r in rows})
    log(f"wrote {len(rows)} EONET observations across {series} series ({dates[0]}–{dates[-1]}) → {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("NASA EONET global natural events (keyless official API):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — EONET API unreachable/empty this run.")
    else:
        time.sleep(0.01)
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
