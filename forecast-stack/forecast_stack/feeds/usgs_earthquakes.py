"""USGS global earthquake event collector.

Official USGS Earthquake Hazards Program GeoJSON API. This is a bounded, keyless global hazard
feed: one rolling year of M4.5+ earthquakes, normalized into date-stamped daily counts by severity
band and operational flags. Event timestamps are capped at the local current date so future-dated
API rows cannot leak into point-in-time forecasts.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
OUT_PATH = DATA_DIR / "feeds" / "usgs_earthquakes.jsonl"

REQUEST_TIMEOUT_S = 60
WINDOW_DAYS = 365
MIN_MAGNITUDE = 4.5
LIMIT = 20000
MIN_REFRESH_FRACTION = 0.5


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


def _date_from_ms(raw: Any) -> date | None:
    try:
        return datetime.fromtimestamp(float(raw) / 1000.0, timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def fetch_events(*, today: date | None = None, window_days: int = WINDOW_DAYS) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=window_days)
    end = today + timedelta(days=1)
    params = {
        "format": "geojson",
        "starttime": start.isoformat(),
        "endtime": end.isoformat(),
        "minmagnitude": f"{MIN_MAGNITUDE:g}",
        "orderby": "time",
        "limit": str(LIMIT),
    }
    req = urllib.request.Request(
        f"{API_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
        data = json.loads(resp.read().decode("utf-8", "replace"))
    features = data.get("features") if isinstance(data, dict) else []
    return [f for f in features if isinstance(f, dict)]


def _example(feature: dict[str, Any], event_date: date) -> dict[str, Any]:
    props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    geom = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
    coords = geom.get("coordinates") if isinstance(geom.get("coordinates"), list) else None
    return {
        "id": feature.get("id"),
        "date": event_date.isoformat(),
        "title": props.get("title"),
        "place": props.get("place"),
        "magnitude": props.get("mag"),
        "magnitude_type": props.get("magType"),
        "depth_km": coords[2] if coords and len(coords) > 2 else None,
        "longitude": coords[0] if coords and len(coords) > 0 else None,
        "latitude": coords[1] if coords and len(coords) > 1 else None,
        "alert": props.get("alert"),
        "tsunami": props.get("tsunami"),
        "felt": props.get("felt"),
        "significance": props.get("sig"),
        "status": props.get("status"),
        "url": props.get("url"),
    }


def _append_example(bucket: dict[str, list[dict[str, Any]]], key: str, ex: dict[str, Any]) -> None:
    if len(bucket[key]) < 8:
        bucket[key].append(ex)


def normalize(
    features: list[dict[str, Any]],
    *,
    today: date | None = None,
    window_days: int = WINDOW_DAYS,
) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    since = today - timedelta(days=window_days)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def bump(series: str, d: date, feature: dict[str, Any]) -> None:
        ds = d.isoformat()
        counts[(series, ds)] += 1
        _append_example(examples, f"{series}|{ds}", _example(feature, d))

    for feature in features:
        props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        event_date = _date_from_ms(props.get("time"))
        if event_date is None or event_date < since or event_date > today:
            continue
        try:
            mag = float(props.get("mag"))
        except (TypeError, ValueError):
            continue
        bump("all_m45_plus", event_date, feature)
        if mag >= 5.0:
            bump("m50_plus", event_date, feature)
        if mag >= 6.0:
            bump("m60_plus", event_date, feature)
        if mag >= 7.0:
            bump("m70_plus", event_date, feature)
        if int(props.get("tsunami") or 0) == 1:
            bump("tsunami_flagged", event_date, feature)
        try:
            if float(props.get("sig") or 0) >= 600:
                bump("significant", event_date, feature)
        except (TypeError, ValueError):
            pass
        if props.get("felt") is not None:
            bump("felt_reports", event_date, feature)

    specs = {
        "all_m45_plus": ("earthquakes", "earthquakes_m45_plus", "USGS Earthquake Hazards — All M4.5+ earthquakes"),
        "m50_plus": ("earthquakes", "earthquakes_m50_plus", "USGS Earthquake Hazards — M5.0+ earthquakes"),
        "m60_plus": ("earthquakes", "earthquakes_m60_plus", "USGS Earthquake Hazards — M6.0+ earthquakes"),
        "m70_plus": ("earthquakes", "earthquakes_m70_plus", "USGS Earthquake Hazards — M7.0+ earthquakes"),
        "tsunami_flagged": ("earthquakes", "earthquakes_tsunami_flagged", "USGS Earthquake Hazards — tsunami-flagged earthquakes"),
        "significant": ("earthquakes", "earthquakes_significant", "USGS Earthquake Hazards — significant earthquakes"),
        "felt_reports": ("earthquakes", "earthquakes_with_felt_reports", "USGS Earthquake Hazards — earthquakes with felt reports"),
    }

    rows: list[dict[str, Any]] = []
    for (series, ds), value in sorted(counts.items()):
        _topic, metric, title = specs[series]
        rows.append({
            "series_id": f"usgs_earthquakes:{series}",
            "date": ds,
            "value": float(value),
            "unit": "earthquakes",
            "metric": metric,
            "title": title,
            "examples": examples.get(f"{series}|{ds}", []),
        })
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    try:
        features = fetch_events()
    except Exception as exc:  # noqa: BLE001 — public endpoint; preserve last good file on failure
        log(f"USGS earthquake fetch failed: {exc}")
        return []
    rows = normalize(features)
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
    log(f"wrote {len(rows)} USGS earthquake observations across {series} series ({dates[0]}–{dates[-1]}) → {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("USGS global earthquakes (keyless official API):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — USGS earthquake API unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
