"""Australia EPBC referrals collector.

Official Department of Climate Change, Energy, the Environment and Water public ArcGIS dataset.
This V1 collector stores referral/project status rows without geometry. The source dataset's
spatial boundaries represent referral extents, not development footprints, so the feed explicitly
keeps that caveat in each row.

The dataset exposes referral year but not exact publication dates for every decision in this layer.
For leak safety, rows are dated to the public dataset snapshot date and carry the referral year as
event context.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
ITEM_URL = "https://www.arcgis.com/sharing/rest/content/items/ff8ef1108dd148c99fad93799d67ee94"
LAYER_URL = "https://gis.environment.gov.au/gispubmap/rest/services/ogc_services/EPBC_Referrals/MapServer/0"
OUT_PATH = DATA_DIR / "feeds" / "australia_epbc_referrals.jsonl"

REQUEST_TIMEOUT_S = 60
PAGE_SIZE = 10_000


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_json(url: str, params: dict[str, Any], *, retries: int = 2) -> dict[str, Any] | None:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                full_url,
                headers={"User-Agent": UA, "Accept": "application/json,*/*"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
    return None


def _feature_attrs(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    out: list[dict[str, Any]] = []
    for feature in data.get("features") or []:
        attrs = feature.get("attributes") if isinstance(feature, dict) else None
        if isinstance(attrs, dict):
            out.append(attrs)
    return out


def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("\\\\", "/").split())


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "unknown"


def _snapshot_date() -> str:
    meta = _fetch_json(ITEM_URL, {"f": "json"})
    modified = (meta or {}).get("modified")
    if isinstance(modified, (int, float)):
        try:
            return datetime.fromtimestamp(float(modified) / 1000.0, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return date.today().isoformat()


def _fetch_rows() -> list[dict[str, Any]]:
    data = _fetch_json(
        f"{LAYER_URL}/query",
        {
            "f": "json",
            "where": "1=1",
            "returnGeometry": "false",
            "outFields": "*",
            "orderByFields": "OBJECTID DESC",
            "resultRecordCount": PAGE_SIZE,
        },
    )
    return _feature_attrs(data)


def _event_year(value: Any) -> str | None:
    try:
        year = int(float(value))
    except (TypeError, ValueError):
        return None
    if 1900 <= year <= 2100:
        return f"{year:04d}-12-31"
    return None


def _row(attrs: dict[str, Any], *, snapshot_date: str) -> dict[str, Any] | None:
    ref = _text(attrs.get("REFERENCE_NUMBER"))
    name = _text(attrs.get("NAME"))
    object_id = _text(attrs.get("OBJECTID"))
    if not ref or not name:
        return None
    event_time = _event_year(attrs.get("YEAR")) or snapshot_date
    jurisdiction = _text(attrs.get("PRIMARY_JURISDICTION")) or "Australia"
    decision = _text(attrs.get("REFERRAL_DECISION")) or "Decision unknown"
    status = _text(attrs.get("STATUS_DESCRIPTION")) or "Status unknown"
    stage = _text(attrs.get("STAGE_NAME"))
    category = _text(attrs.get("CATEGORY"))
    proposal_id = _text(attrs.get("PROPOSAL_ID"))
    area = attrs.get("SHAPE.AREA") or attrs.get("SHAPE__AREA")
    length = attrs.get("SHAPE.LEN") or attrs.get("SHAPE__LEN")
    series_key = _slug(f"{ref}:{object_id or proposal_id}")
    return {
        "feed": "australia_epbc_referrals",
        "series_id": f"australia_epbc_referrals:referral:{series_key}",
        "date": snapshot_date,
        "as_of": snapshot_date,
        "event_time": event_time,
        "published_at": snapshot_date,
        "observed_at": event_time,
        "value": 1.0,
        "unit": "referral",
        "metric": "australia_epbc_referral_status",
        "domain": "land_use_policy",
        "title": f"Australia EPBC referral {ref} - {name} - {decision} - {status}",
        "source_authority": "Australian Department of Climate Change, Energy, the Environment and Water",
        "source_layer": "Referrals Spatial Database - Public",
        "source_page_url": LAYER_URL,
        "jurisdiction": "Australia",
        "primary_jurisdiction": jurisdiction,
        "region": "Oceania",
        "reference_number": ref,
        "proposal_id": proposal_id,
        "project_name": name,
        "referral_decision": decision,
        "standard_determination": _text(attrs.get("STANDARD_DETERMINATION")),
        "status_description": status,
        "stage_name": stage,
        "referral_type": _text(attrs.get("REFERRAL_TYPE")),
        "referral_year": int(float(attrs.get("YEAR"))) if attrs.get("YEAR") not in (None, "") else None,
        "category": category,
        "referral_url": _text(attrs.get("REFERRAL_URL")),
        "crm_id": _text(attrs.get("CRM_ID")),
        "object_id": object_id,
        "shape_area": float(area) if area not in (None, "") else None,
        "shape_len": float(length) if length not in (None, "") else None,
        "boundary_caveat": "Referral boundaries are maximum referral extents, not development footprints.",
        "provenance": "official_australia_epbc_referrals_arcgis_no_geometry",
        "cost_cents": 0,
    }


def normalize(attrs_rows: list[dict[str, Any]], *, snapshot_date: str) -> list[dict[str, Any]]:
    rows = [row for attrs in attrs_rows if (row := _row(attrs, snapshot_date=snapshot_date))]
    rows.sort(key=lambda r: (str(r.get("reference_number") or ""), str(r.get("project_name") or "")))
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    snapshot_date = _snapshot_date()
    attrs_rows = _fetch_rows()
    rows = normalize(attrs_rows, snapshot_date=snapshot_date)
    if not rows:
        log("no Australia EPBC referral rows fetched; not writing an empty file")
        return []
    _write_jsonl_atomic(rows)
    log(f"wrote {len(rows)} Australia EPBC referral rows -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("Australia EPBC referrals (official ArcGIS, keyless, no geometry):")
    observations = collect()
    for row in observations[:5]:
        print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
