"""GDACS global disaster-alert collector.

GDACS (Global Disaster Alert and Coordination System) provides near-real-time global alerts for
natural disasters with potential humanitarian impact. This keyless collector uses the public GeoJSON
API and emits dated counts by disaster type, alert level, country, and current-open status.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
OUT_PATH = DATA_DIR / "feeds" / "gdacs_alerts.jsonl"

WINDOW_DAYS = 365
REQUEST_TIMEOUT_S = 45
MIN_REFRESH_FRACTION = 0.5
EVENT_TYPES = ("EQ", "TC", "FL", "VO", "DR", "WF")
EVENT_TYPE_NAMES = {
    "EQ": "Earthquake",
    "TC": "Tropical cyclone",
    "FL": "Flood",
    "VO": "Volcano",
    "DR": "Drought",
    "WF": "Wildfire",
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "unknown"


def _parse_date(raw: Any) -> date | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
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


def fetch_events(*, today: date | None = None, window_days: int = WINDOW_DAYS) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=window_days)
    params = {
        "eventtypes": ",".join(EVENT_TYPES),
        "fromDate": start.isoformat(),
        "toDate": today.isoformat(),
    }
    req = urllib.request.Request(
        f"{API_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
        data = json.loads(resp.read().decode("utf-8", "replace"))
    features = data.get("features") if isinstance(data, dict) else []
    return [f for f in features if isinstance(f, dict)]


def _props(feature: dict[str, Any]) -> dict[str, Any]:
    return feature.get("properties") if isinstance(feature.get("properties"), dict) else {}


def _country_entries(props: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    affected = props.get("affectedcountries") if isinstance(props.get("affectedcountries"), list) else []
    for c in affected:
        if not isinstance(c, dict):
            continue
        iso3 = str(c.get("iso3") or "").strip().upper()
        name = str(c.get("countryname") or "").strip()
        if iso3 and name:
            out.append((iso3, name))
    if not out:
        iso3 = str(props.get("iso3") or "").strip().upper()
        name = str(props.get("country") or "").strip()
        if iso3 and name:
            out.append((iso3, name))
    return sorted(set(out))


def _report_url(props: dict[str, Any]) -> str | None:
    url = props.get("url")
    if isinstance(url, dict):
        report = url.get("report")
        if report:
            return str(report)
    return None


def _example(feature: dict[str, Any], event_date: date) -> dict[str, Any]:
    props = _props(feature)
    severity = props.get("severitydata") if isinstance(props.get("severitydata"), dict) else {}
    geom = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
    return {
        "eventtype": props.get("eventtype"),
        "eventid": props.get("eventid"),
        "episodeid": props.get("episodeid"),
        "date": event_date.isoformat(),
        "name": props.get("name") or props.get("description"),
        "country": props.get("country"),
        "iso3": props.get("iso3"),
        "affectedcountries": _country_entries(props),
        "alertlevel": props.get("alertlevel"),
        "alertscore": props.get("alertscore"),
        "iscurrent": props.get("iscurrent"),
        "fromdate": props.get("fromdate"),
        "todate": props.get("todate"),
        "datemodified": props.get("datemodified"),
        "severity": severity.get("severity"),
        "severityunit": severity.get("severityunit"),
        "severitytext": severity.get("severitytext"),
        "coordinates": geom.get("coordinates"),
        "report": _report_url(props),
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
    current: dict[str, set[str]] = defaultdict(set)
    current_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def bump(series: str, d: date, feature: dict[str, Any]) -> None:
        ds = d.isoformat()
        counts[(series, ds)] += 1
        _append_example(examples, f"{series}|{ds}", _example(feature, d))

    for feature in features:
        props = _props(feature)
        event_date = _parse_date(props.get("fromdate")) or _parse_date(props.get("datemodified"))
        if event_date is None or event_date < since or event_date > today:
            continue
        event_id = f"{props.get('eventtype')}:{props.get('eventid')}:{props.get('episodeid')}"
        event_type = str(props.get("eventtype") or "unknown").upper()
        event_name = EVENT_TYPE_NAMES.get(event_type, event_type)
        level = str(props.get("alertlevel") or "Unknown").strip().title()
        level_slug = _slug(level)

        bump("all", event_date, feature)
        bump(f"type:{event_type}", event_date, feature)
        bump(f"level:{level_slug}", event_date, feature)
        bump(f"type_level:{event_type}:{level_slug}", event_date, feature)
        for iso3, country in _country_entries(props):
            bump(f"country:{iso3}:{_slug(country)}", event_date, feature)

        is_current = str(props.get("iscurrent") or "").lower() == "true"
        to_date = _parse_date(props.get("todate"))
        if is_current and (to_date is None or to_date >= today):
            for series in ("current:all", f"current:type:{event_type}", f"current:level:{level_slug}"):
                current[series].add(event_id)
                _append_example(current_examples, series, _example(feature, event_date))
            for iso3, country in _country_entries(props):
                series = f"current:country:{iso3}:{_slug(country)}"
                current[series].add(event_id)
                _append_example(current_examples, series, _example(feature, event_date))

    rows: list[dict[str, Any]] = []

    def title_for(series: str) -> str:
        if series == "all":
            return "GDACS — All disaster alerts"
        if series.startswith("type:"):
            code = series.split(":", 1)[1]
            return f"GDACS — {EVENT_TYPE_NAMES.get(code, code)} disaster alerts"
        if series.startswith("level:"):
            level = series.split(":", 1)[1].replace("_", " ").title()
            return f"GDACS — {level} disaster alerts"
        if series.startswith("type_level:"):
            _prefix, code, level = series.split(":", 2)
            return f"GDACS — {EVENT_TYPE_NAMES.get(code, code)} {level.replace('_', ' ').title()} disaster alerts"
        if series.startswith("country:"):
            _prefix, iso3, country_slug = series.split(":", 2)
            return f"GDACS — {country_slug.replace('_', ' ').title()} disaster alerts"
        if series == "current:all":
            return "GDACS — Current open disaster alerts"
        if series.startswith("current:type:"):
            code = series.split(":", 2)[2]
            return f"GDACS — {EVENT_TYPE_NAMES.get(code, code)} current open disaster alerts"
        if series.startswith("current:level:"):
            level = series.split(":", 2)[2].replace("_", " ").title()
            return f"GDACS — {level} current open disaster alerts"
        if series.startswith("current:country:"):
            _a, _b, _iso3, country_slug = series.split(":", 3)
            return f"GDACS — {country_slug.replace('_', ' ').title()} current open disaster alerts"
        return f"GDACS — {series}"

    def metric_for(series: str) -> str:
        if series.startswith("current:"):
            return "gdacs_current_open_alerts"
        if series.startswith("level:") or series.startswith("type_level:"):
            return "gdacs_alerts_by_level"
        if series.startswith("country:"):
            return "gdacs_alerts_by_country"
        if series.startswith("type:"):
            return "gdacs_alerts_by_type"
        return "gdacs_disaster_alerts"

    daily_series = sorted({series for series, _ds in counts})
    day = since
    while day <= today:
        ds = day.isoformat()
        for series in daily_series:
            value = counts.get((series, ds), 0)
            rows.append({
                "series_id": f"gdacs_alerts:{series}",
                "date": ds,
                "value": float(value),
                "unit": "alerts",
                "metric": metric_for(series),
                "title": title_for(series),
                "examples": examples.get(f"{series}|{ds}", []),
            })
        day += timedelta(days=1)
    for series, ids in sorted(current.items()):
        rows.append({
            "series_id": f"gdacs_alerts:{series}",
            "date": today.isoformat(),
            "value": float(len(ids)),
            "unit": "alerts",
            "metric": "gdacs_current_open_alerts",
            "title": title_for(series),
            "examples": current_examples.get(series, []),
        })
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    try:
        features = fetch_events()
    except Exception as exc:  # noqa: BLE001 — public endpoint; preserve last good file on failure
        log(f"GDACS fetch failed: {exc}")
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
    log(f"wrote {len(rows)} GDACS observations across {series} series ({dates[0]}–{dates[-1]}) → {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("GDACS global disaster alerts (keyless public API):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — GDACS API unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
