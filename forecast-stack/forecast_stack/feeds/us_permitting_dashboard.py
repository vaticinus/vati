"""U.S. Federal Permitting Dashboard project/action-state collector.

Official public data portal (Socrata) export for the Federal Infrastructure Permitting Dashboard.
The raw dataset is milestone-grain; this bounded V1 collector normalizes it into:

* one current project-state row per project;
* one current permit/review action-state row per action.

Rows are dated to the dataset's own ``last_data_fetched`` timestamp. Milestone target/actual dates
are carried as metadata, but the fact event date remains the public snapshot date so old milestones
do not leak into earlier as-of forecasts.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://data.permits.performance.gov/resource/mcm3-xbid.json"
OUT_PATH = DATA_DIR / "feeds" / "us_permitting_dashboard.jsonl"

MAX_ROWS = 50_000
REQUEST_TIMEOUT_S = 60


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_rows(*, limit: int = MAX_ROWS, retries: int = 2) -> list[dict[str, Any]]:
    params = {
        "$limit": int(limit),
        "$order": "project_id, action_id, milestone_id",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
                data = json.loads(resp.read().decode("utf-8", "replace"))
            return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return []
    return []


def _text(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if isinstance(value, dict) and "url" in value:
        value = value.get("url")
    return " ".join(str(value or "").split())


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _state_date(row: dict[str, Any]) -> str:
    return _iso_date(row.get("last_data_fetched")) or date.today().isoformat()


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _project_key(row: dict[str, Any]) -> str | None:
    pid = _text(row, "project_id")
    return pid or None


def _action_key(row: dict[str, Any]) -> str | None:
    aid = _text(row, "action_id")
    return aid or None


def _latest_row(current: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if current is None:
        return candidate
    if _state_date(candidate) >= _state_date(current):
        return candidate
    return current


def _project_row(row: dict[str, Any]) -> dict[str, Any] | None:
    project_id = _project_key(row)
    title = _text(row, "project_title")
    if not project_id or not title:
        return None
    state_date = _state_date(row)
    cost = _number(row.get("total_estimated_project_cost"))
    out = {
        "feed": "us_permitting_dashboard",
        "series_id": f"us_permitting_dashboard:project:{project_id}",
        "date": state_date,
        "event_time": state_date,
        "published_at": state_date,
        "observed_at": state_date,
        "value": 1.0,
        "unit": "project",
        "metric": "us_federal_permitting_project_status",
        "domain": "land_use_policy",
        "title": f"US Permitting Dashboard - {title} project status",
        "project_id": project_id,
        "project_title": title,
        "project_category": _text(row, "project_category"),
        "project_status": _text(row, "project_field_project_status"),
        "project_sector": _text(row, "project_sector"),
        "project_sector_type": _text(row, "project_sector_type"),
        "lead_agency": _text(row, "project_field_project_lead_agency"),
        "lead_agency_bureau": _text(row, "project_field_project_lead_agency_bureau"),
        "sponsor": _text(row, "project_field_project_sponsor_agency"),
        "state": _text(row, "project_field_location_state"),
        "county": _text(row, "project_field_location_county"),
        "city": _text(row, "project_field_location_city"),
        "location": _text(row, "project_field_location_other"),
        "lat": _text(row, "project_lat"),
        "lon": _text(row, "project_lon"),
        "project_url": _text(row, "project_url"),
        "major_project": _bool(row.get("major_project")),
        "large_complex_or_significant": _bool(row.get("project_large_complex_or_significant")),
        "last_data_fetched": _text(row, "last_data_fetched"),
        "provenance": "official_us_permitting_dashboard_data_portal",
        "cost_cents": 0,
    }
    if cost is not None:
        out["total_estimated_project_cost_usd"] = cost
    return out


def _milestone_date(row: dict[str, Any]) -> str | None:
    return (
        _iso_date(row.get("action_milestone_completion_actual"))
        or _iso_date(row.get("action_milestone_completion_target"))
        or _iso_date(row.get("milestone_conditional_target_date"))
        or _iso_date(row.get("action_milestone_baseline_target"))
    )


def _action_row(row: dict[str, Any], *, milestone_count: int, completed_milestones: int) -> dict[str, Any] | None:
    action_id = _action_key(row)
    project_id = _project_key(row)
    project_title = _text(row, "project_title")
    action_type = _text(row, "action_type")
    if not action_id or not project_id or not project_title:
        return None
    state_date = _state_date(row)
    latest_milestone_date = _milestone_date(row)
    return {
        "feed": "us_permitting_dashboard",
        "series_id": f"us_permitting_dashboard:action:{action_id}",
        "date": state_date,
        "event_time": state_date,
        "published_at": state_date,
        "observed_at": state_date,
        "value": 1.0,
        "unit": "action",
        "metric": "us_federal_permitting_action_status",
        "domain": "land_use_policy",
        "title": f"US Permitting Dashboard - {project_title} - {action_type or 'permitting action'} status",
        "project_id": project_id,
        "project_title": project_title,
        "project_category": _text(row, "project_category"),
        "project_status": _text(row, "project_field_project_status"),
        "project_sector": _text(row, "project_sector"),
        "project_sector_type": _text(row, "project_sector_type"),
        "state": _text(row, "project_field_location_state"),
        "county": _text(row, "project_field_location_county"),
        "city": _text(row, "project_field_location_city"),
        "project_url": _text(row, "project_url"),
        "action_id": action_id,
        "action_type": action_type,
        "action_status": _text(row, "action_status"),
        "action_agency": _text(row, "action_agency"),
        "action_milestone_name": _text(row, "action_milestone_name"),
        "action_milestone_group": _text(row, "action_milestone_group"),
        "action_milestone_complete": _bool(row.get("action_milestone_complete")),
        "latest_milestone_date": latest_milestone_date,
        "milestone_count": milestone_count,
        "completed_milestones": completed_milestones,
        "last_data_fetched": _text(row, "last_data_fetched"),
        "provenance": "official_us_permitting_dashboard_data_portal",
        "cost_cents": 0,
    }


def normalize_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    actions: dict[str, dict[str, Any]] = {}
    action_counts: dict[str, dict[str, int]] = {}
    for row in raw_rows:
        project_id = _project_key(row)
        if project_id:
            projects[project_id] = _latest_row(projects.get(project_id), row)
        action_id = _action_key(row)
        if action_id:
            counts = action_counts.setdefault(action_id, {"milestone_count": 0, "completed_milestones": 0})
            counts["milestone_count"] += 1
            counts["completed_milestones"] += 1 if _bool(row.get("action_milestone_complete")) else 0
            candidate_date = _milestone_date(row) or _state_date(row)
            current = actions.get(action_id)
            current_date = (_milestone_date(current) or _state_date(current)) if current else ""
            if current is None or candidate_date >= current_date:
                actions[action_id] = row

    out: list[dict[str, Any]] = []
    for row in sorted(projects.values(), key=lambda r: (_text(r, "project_id"), _text(r, "project_title"))):
        normalized = _project_row(row)
        if normalized:
            out.append(normalized)
    for action_id, row in sorted(actions.items(), key=lambda item: (item[1].get("project_id") or "", item[0])):
        counts = action_counts.get(action_id, {})
        normalized = _action_row(
            row,
            milestone_count=int(counts.get("milestone_count") or 0),
            completed_milestones=int(counts.get("completed_milestones") or 0),
        )
        if normalized:
            out.append(normalized)
    return out


def collect(*, log=print, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
    raw_rows = _fetch_rows(limit=limit)
    rows = normalize_rows(raw_rows)
    if not rows:
        log("no U.S. Permitting Dashboard rows fetched; not writing an empty file")
        return []
    _write_jsonl_atomic(rows)
    projects = sum(1 for row in rows if row.get("metric") == "us_federal_permitting_project_status")
    actions = sum(1 for row in rows if row.get("metric") == "us_federal_permitting_action_status")
    log(
        f"wrote {len(rows)} rows ({projects} projects, {actions} actions) "
        f"from {len(raw_rows)} milestone rows -> {OUT_PATH}"
    )
    return rows


if __name__ == "__main__":
    print("U.S. Federal Permitting Dashboard project/action states (public data portal, keyless):")
    observations = collect()
    for row in observations[:5]:
        print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
