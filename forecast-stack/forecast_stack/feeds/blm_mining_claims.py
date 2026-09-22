"""BLM MLRS mining-claims and locatable-plans collector.

Official Bureau of Land Management ArcGIS REST services, keyless. This V1 collector avoids
downloading hundreds of thousands of claim geometries. Instead it stores:

* aggregate active mining-claim counts/acres by state, disposition, and claim product;
* all locatable plans-of-operations rows without geometry.

The public snapshot/modified date is used as the fact publication date. Older effective dates are
kept as event dates, but they do not make facts visible before the public dataset snapshot.
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
ACTIVE_CLAIMS_LAYER = "https://gis.blm.gov/nlsdb/rest/services/Mining_Claims/MiningClaims/MapServer/1"
PLANS_LAYER = "https://gis.blm.gov/nlsdb/rest/services/HUB/BLM_Natl_MLRS_Locatable_Plans_Of_Operations/FeatureServer/0"
OUT_PATH = DATA_DIR / "feeds" / "blm_mining_claims.jsonl"

REQUEST_TIMEOUT_S = 60
PAGE_SIZE = 2000


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


def _today() -> str:
    return date.today().isoformat()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "unknown"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _arcgis_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
        today = date.today()
        return min(parsed, today).isoformat()
    text = str(value)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
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


def _claim_aggregate_features() -> list[dict[str, Any]]:
    stats = json.dumps(
        [
            {"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "claim_count"},
            {"statisticType": "sum", "onStatisticField": "RCRD_ACRS", "outStatisticFieldName": "claim_acres"},
        ],
        separators=(",", ":"),
    )
    data = _fetch_json(
        f"{ACTIVE_CLAIMS_LAYER}/query",
        {
            "f": "json",
            "where": "1=1",
            "returnGeometry": "false",
            "groupByFieldsForStatistics": "ADMIN_STATE,CSE_DISP,BLM_PROD",
            "outStatistics": stats,
            "orderByFields": "claim_count DESC",
        },
    )
    return _feature_attrs(data)


def _count(layer_url: str) -> int:
    data = _fetch_json(
        f"{layer_url}/query",
        {"f": "json", "where": "1=1", "returnCountOnly": "true"},
    )
    try:
        return int((data or {}).get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _plan_features() -> list[dict[str, Any]]:
    total = _count(PLANS_LAYER)
    rows: list[dict[str, Any]] = []
    for offset in range(0, max(total, 1), PAGE_SIZE):
        data = _fetch_json(
            f"{PLANS_LAYER}/query",
            {
                "f": "json",
                "where": "1=1",
                "returnGeometry": "false",
                "outFields": "*",
                "orderByFields": "Modified DESC",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
            },
        )
        batch = _feature_attrs(data)
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(0.2)
    return rows


def _claim_rows(attrs: list[dict[str, Any]], *, snapshot_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in attrs:
        state = _text(r.get("GEO_STATE") or r.get("ADMIN_STATE")) or "UNSPECIFIED"
        disposition = _text(r.get("CSE_DISP")) or "Unknown"
        product = _text(r.get("BLM_PROD")) or "Unknown product"
        count = int(_number(r.get("claim_count")) or 0)
        acres = _number(r.get("claim_acres")) or 0.0
        if count <= 0:
            continue
        key = f"{_slug(state)}:{_slug(disposition)}:{_slug(product)}"
        common = {
            "feed": "blm_mining_claims",
            "date": snapshot_date,
            "as_of": snapshot_date,
            "event_time": snapshot_date,
            "published_at": snapshot_date,
            "observed_at": snapshot_date,
            "domain": "land_use",
            "jurisdiction": "United States",
            "region": "North America",
            "state": state,
            "case_disposition": disposition,
            "blm_product": product,
            "source_authority": "U.S. Bureau of Land Management",
            "source_layer": "BLM Active Mining Claims",
            "source_page_url": ACTIVE_CLAIMS_LAYER,
            "provenance": "official_blm_arcgis_rest_aggregate_no_geometry",
            "cost_cents": 0,
        }
        rows.append(
            {
                **common,
                "series_id": f"blm_mining_claims:active_claim_count:{key}",
                "value": float(count),
                "unit": "claims",
                "metric": "blm_active_mining_claim_count",
                "title": f"BLM active mining claims - {state} - {disposition} - {product} count",
            }
        )
        rows.append(
            {
                **common,
                "series_id": f"blm_mining_claims:active_claim_acres:{key}",
                "value": float(round(acres, 4)),
                "unit": "acres",
                "metric": "blm_active_mining_claim_acres",
                "title": f"BLM active mining claims - {state} - {disposition} - {product} acres",
            }
        )
    return rows


def _plan_row(r: dict[str, Any]) -> dict[str, Any] | None:
    case_nr = _text(r.get("CSE_NR")) or _text(r.get("ID"))
    if not case_nr:
        return None
    state_date = _arcgis_date(r.get("Modified")) or _today()
    event_date = _arcgis_date(r.get("EFF_DT")) or _arcgis_date(r.get("CSE_DISP_DT")) or state_date
    state = _text(r.get("GEO_STATE") or r.get("ADMIN_STATE")) or "UNKNOWN"
    name = _text(r.get("CSE_NAME")) or case_nr
    commodity = _text(r.get("CMMDTY"))
    operator = _text(r.get("CUST_NM_SEC"))
    status = _text(r.get("CSE_DISP")) or "Unknown"
    product = _text(r.get("BLM_PROD")) or "Locatable plan"
    title_bits = [name, status, product]
    if commodity:
        title_bits.append(commodity)
    if operator:
        title_bits.append(operator)
    return {
        "feed": "blm_mining_claims",
        "series_id": f"blm_mining_claims:plan:{_slug(case_nr)}",
        "date": state_date,
        "as_of": state_date,
        "event_time": event_date,
        "published_at": state_date,
        "observed_at": event_date,
        "value": 1.0,
        "unit": "plan",
        "metric": "blm_mining_plan_status",
        "domain": "land_use",
        "title": "BLM mining plan - " + " - ".join(title_bits),
        "source_authority": "U.S. Bureau of Land Management",
        "source_layer": "BLM National MLRS Locatable Plans Of Operations",
        "source_page_url": PLANS_LAYER,
        "jurisdiction": "United States",
        "region": "North America",
        "state": state,
        "admin_state": _text(r.get("ADMIN_STATE")),
        "case_serial_number": case_nr,
        "legacy_case_serial_number": _text(r.get("LEG_CSE_NR")),
        "case_name": name,
        "case_disposition": status,
        "case_disposition_date": _arcgis_date(r.get("CSE_DISP_DT")),
        "blm_product": product,
        "commodity": commodity,
        "operator": operator,
        "operator_interest_pct": _number(r.get("PCT_INT_SEC")),
        "operator_interest_relationship": _text(r.get("INT_REL_SEC")),
        "production_status": _text(r.get("PRDCNG")),
        "effective_date": _arcgis_date(r.get("EFF_DT")),
        "expiration_date": _arcgis_date(r.get("EXP_DT")),
        "case_acres": _number(r.get("RCRD_ACRS")),
        "data_quality": _text(r.get("QLTY")),
        "data_source": _text(r.get("SRC")),
        "salesforce_id": _text(r.get("SF_ID")),
        "created": _arcgis_date(r.get("Created")),
        "modified": _arcgis_date(r.get("Modified")),
        "provenance": "official_blm_arcgis_rest_plan_no_geometry",
        "cost_cents": 0,
    }


def normalize(claim_attrs: list[dict[str, Any]], plan_attrs: list[dict[str, Any]], *, snapshot_date: str | None = None) -> list[dict[str, Any]]:
    snapshot_date = snapshot_date or _today()
    rows = _claim_rows(claim_attrs, snapshot_date=snapshot_date)
    plans = [row for attrs in plan_attrs if (row := _plan_row(attrs))]
    rows.extend(sorted(plans, key=lambda r: (str(r.get("state") or ""), str(r.get("case_serial_number") or ""))))
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    claim_attrs = _claim_aggregate_features()
    plan_attrs = _plan_features()
    rows = normalize(claim_attrs, plan_attrs)
    if not rows:
        log("no BLM mining claim/plan rows fetched; not writing an empty file")
        return []
    _write_jsonl_atomic(rows)
    claim_rows = sum(1 for row in rows if str(row.get("metric") or "").startswith("blm_active_mining_claim"))
    plan_rows = sum(1 for row in rows if row.get("metric") == "blm_mining_plan_status")
    log(
        f"wrote {len(rows)} rows ({claim_rows} aggregate claim rows, {plan_rows} plan rows) "
        f"-> {OUT_PATH}"
    )
    return rows


if __name__ == "__main__":
    print("BLM MLRS mining claims/plans (ArcGIS REST, keyless, no geometry):")
    observations = collect()
    for row in observations[:5]:
        print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
