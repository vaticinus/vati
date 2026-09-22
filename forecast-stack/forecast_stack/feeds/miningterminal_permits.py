"""Compact importer for MiningTerminal's local global mining-permit snapshot.

This collector does not rerun MiningTerminal scrapers and does not copy GeoJSON geometry into this
repo. It streams the local permit GeoJSON artifacts, emits aggregate permit/area observations, and
keeps provenance fields pointing back to the source artifact and official source URL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any, Iterable

# Optional: point at a local MiningTerminal GeoJSON permit dump. Unset by default,
# so the feed reports "not configured" instead of guessing at a path.
_PERMITS_ENV = os.environ.get("MININGTERMINAL_PERMITS_DIR", "")
DEFAULT_INPUT_DIR: Path | None = Path(_PERMITS_ENV) if _PERMITS_ENV else None
OUT_PATH = DATA_DIR / "feeds" / "miningterminal_permits.jsonl"
STATUS_PATH = OUT_PATH.with_suffix(".status.json")

CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_HOLDER_GROUPS = 50_000
DEFAULT_MIN_HOLDER_RECORDS = 2
DEFAULT_MIN_HOLDER_AREA_HA = 1_000.0

CRITICAL_COMMODITY_TERMS = {
    "antimony",
    "bauxite",
    "cobalt",
    "copper",
    "graphite",
    "gold",
    "iron",
    "lithium",
    "manganese",
    "molybdenum",
    "nickel",
    "phosphate",
    "platinum",
    "potash",
    "rare earth",
    "silver",
    "tin",
    "titanium",
    "tungsten",
    "uranium",
    "vanadium",
    "zinc",
}

COMMODITY_ALIASES = {
    "ag": "Silver",
    "al": "Aluminum",
    "au": "Gold",
    "co": "Cobalt",
    "cu": "Copper",
    "fe": "Iron",
    "li": "Lithium",
    "mn": "Manganese",
    "mo": "Molybdenum",
    "ni": "Nickel",
    "pb": "Lead",
    "pt": "Platinum",
    "ree": "Rare earth elements",
    "sn": "Tin",
    "ti": "Titanium",
    "u": "Uranium",
    "w": "Tungsten",
    "zn": "Zinc",
}


def _write_jsonl_atomic(rows: list[dict[str, Any]], output_path: Path = OUT_PATH) -> None:
    tmp = output_path.with_suffix(".jsonl.tmp")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(output_path)


def _write_status(status: dict[str, Any], status_path: Path = STATUS_PATH) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _today() -> date:
    return date.today()


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _slug(value: Any, *, max_len: int = 64) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", _text(value).lower()).strip("_")
    return (text or "unknown")[:max_len].strip("_") or "unknown"


def _stable_suffix(*parts: Any) -> str:
    return hashlib.sha1("|".join(_text(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _series_key(*parts: Any) -> str:
    slugs = [_slug(part, max_len=36) for part in parts]
    base = ":".join(slugs)
    if len(base) <= 180:
        return base
    return f"{base[:150].rstrip(':')}:{_stable_suffix(*parts)}"


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            if float(value) > 10_000_000_000:
                parsed = datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).date()
            else:
                parsed = datetime.fromtimestamp(float(value), tz=timezone.utc).date()
            return parsed.isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    raw = _text(value)
    if not raw:
        return None
    compact = re.fullmatch(r"(20\d{2})(\d{2})(\d{2})", raw)
    if compact:
        raw = f"{compact.group(1)}-{compact.group(2)}-{compact.group(3)}"
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _min_date(current: str | None, candidate: str | None) -> str | None:
    if not candidate:
        return current
    if not current or candidate < current:
        return candidate
    return current


def _max_date(current: str | None, candidate: str | None) -> str | None:
    if not candidate:
        return current
    if not current or candidate > current:
        return candidate
    return current


def _metadata_from_prefix(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            prefix = fh.read(1_000_000)
    except OSError:
        return {}
    idx = prefix.find('"metadata"')
    if idx < 0:
        return {}
    colon = prefix.find(":", idx)
    start = prefix.find("{", colon)
    if colon < 0 or start < 0:
        return {}
    try:
        meta, _ = json.JSONDecoder().raw_decode(prefix[start:])
    except json.JSONDecodeError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _snapshot_date(path: Path, metadata: dict[str, Any]) -> str:
    scraped = _date_text(metadata.get("scraped_at"))
    if scraped:
        return min(scraped, _today().isoformat())
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", path.name)
    if match:
        found = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return min(found, _today().isoformat())
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
    except OSError:
        return _today().isoformat()


def _iter_feature_objects(path: Path) -> Iterable[dict[str, Any]]:
    """Yield GeoJSON Feature objects without loading the whole FeatureCollection."""

    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        buffer = ""
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                return
            buffer += chunk
            marker = buffer.find('"features"')
            if marker < 0:
                buffer = buffer[-128:]
                continue
            bracket = buffer.find("[", marker)
            if bracket < 0:
                buffer = buffer[marker:]
                continue
            pending = buffer[bracket + 1 :]
            break

        in_object = False
        in_string = False
        escaped = False
        depth = 0
        obj_chars: list[str] = []
        done = False

        while True:
            for ch in pending:
                if not in_object:
                    if ch == "{":
                        in_object = True
                        in_string = False
                        escaped = False
                        depth = 1
                        obj_chars = ["{"]
                    elif ch == "]":
                        done = True
                        break
                    continue

                obj_chars.append(ch)
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                    continue

                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        raw = "".join(obj_chars)
                        in_object = False
                        obj_chars = []
                        try:
                            feature = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(feature, dict):
                            yield feature
            if done:
                return
            pending = fh.read(CHUNK_SIZE)
            if not pending:
                return


def _commodity_parts(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_parts = [_text(part) for part in value]
    else:
        raw = _text(value)
        raw_parts = re.split(r"[;,/|]+", raw) if raw else []
    out: list[str] = []
    for part in raw_parts:
        part = re.sub(r"\s+", " ", part).strip(" .")
        if not part or part.lower() in {"unknown", "none", "null", "n/a", "na", "metallic minerals"}:
            continue
        alias = COMMODITY_ALIASES.get(part.lower())
        if alias:
            out.append(alias)
        elif part.isupper() and len(part) <= 4:
            out.append(part)
        else:
            out.append(part[:80].title())
    seen: set[str] = set()
    uniq: list[str] = []
    for part in out:
        key = part.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(part)
    return uniq


def _is_critical_commodity(commodity: str) -> bool:
    lowered = commodity.lower()
    return any(term in lowered for term in CRITICAL_COMMODITY_TERMS)


def _new_group(
    *,
    group_type: str,
    snapshot_date: str,
    country: str,
    source_system: str,
    source_url: str,
    source_artifact: str,
    commodity: str,
    status: str,
    phase: str,
    permit_type: str,
    holder_name: str | None = None,
) -> dict[str, Any]:
    return {
        "group_type": group_type,
        "snapshot_date": snapshot_date,
        "country": country,
        "source_system": source_system,
        "source_url": source_url,
        "source_artifact": source_artifact,
        "source_artifact_count": 1,
        "commodity": commodity,
        "status": status,
        "phase": phase,
        "permit_type": permit_type,
        "holder_name": holder_name,
        "count": 0,
        "area_hectares": 0.0,
        "has_area": False,
        "earliest_grant_date": None,
        "latest_expiry_date": None,
    }


def _update_group(group: dict[str, Any], props: dict[str, Any], *, area: float | None, artifact: str) -> None:
    group["count"] += 1
    if area is not None:
        group["area_hectares"] += max(area, 0.0)
        group["has_area"] = True
    group["earliest_grant_date"] = _min_date(group["earliest_grant_date"], _date_text(props.get("grant_date")))
    group["latest_expiry_date"] = _max_date(group["latest_expiry_date"], _date_text(props.get("expiry_date")))
    if group["source_artifact"] != artifact:
        group["source_artifact_count"] += 1


def _feed_row(group: dict[str, Any], *, metric: str, value: float, unit: str) -> dict[str, Any]:
    group_type = str(group["group_type"])
    suffix = _series_key(
        group_type,
        metric,
        group.get("country"),
        group.get("source_system"),
        group.get("holder_name") or "",
        group.get("commodity"),
        group.get("status"),
        group.get("phase"),
        group.get("permit_type"),
        group.get("snapshot_date"),
    )
    metric_label = "count" if unit == "permits" else "area"
    if group_type == "holder":
        title = (
            "MiningTerminal permit holder - "
            f"{group.get('holder_name')} - {group['country']} - {group['source_system']} - "
            f"{group['commodity']} - {metric_label}"
        )
    else:
        parts = [
            group["country"],
            group["source_system"],
            group["commodity"],
            group["status"],
            group["phase"],
            group["permit_type"],
        ]
        detail = " - ".join(part for part in parts if _text(part))
        title = f"MiningTerminal permit aggregate - {detail} - {metric_label}"
    snapshot = str(group["snapshot_date"])
    return {
        "feed": "miningterminal_permits",
        "series_id": f"miningterminal_permits:{group_type}:{metric}:{suffix}",
        "date": snapshot,
        "as_of": snapshot,
        "event_time": snapshot,
        "published_at": snapshot,
        "observed_at": snapshot,
        "value": float(value),
        "unit": unit,
        "metric": metric,
        "domain": "land_use",
        "title": title[:240],
        "jurisdiction": group["country"],
        "country": group["country"],
        "source_system": group["source_system"],
        "source_page_url": group["source_url"],
        "source_artifact": group["source_artifact"],
        "source_artifact_count": group["source_artifact_count"],
        "holder_name": group.get("holder_name"),
        "commodity": group["commodity"],
        "status": group["status"],
        "phase": group["phase"],
        "permit_type": group["permit_type"],
        "earliest_grant_date": group.get("earliest_grant_date"),
        "latest_expiry_date": group.get("latest_expiry_date"),
        "record_count": int(group["count"]),
        "area_hectares": round(float(group["area_hectares"]), 4) if group["has_area"] else None,
        "source_authority": group["source_system"],
        "provenance": "derived_from_miningterminal_local_geojson_no_geometry",
        "cost_cents": 0,
    }


def summarize(
    paths: Iterable[Path],
    *,
    max_holder_groups: int = DEFAULT_MAX_HOLDER_GROUPS,
    min_holder_records: int = DEFAULT_MIN_HOLDER_RECORDS,
    min_holder_area_ha: float = DEFAULT_MIN_HOLDER_AREA_HA,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    aggregate_groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    holder_groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    files_seen = 0
    features_seen = 0
    files_empty = 0

    for path in sorted(paths):
        metadata = _metadata_from_prefix(path)
        snapshot = _snapshot_date(path, metadata)
        default_country = _text(metadata.get("country")) or "UNKNOWN"
        default_source = _text(metadata.get("source")) or path.stem
        artifact = str(path)
        file_features = 0
        for feature in _iter_feature_objects(path):
            props = feature.get("properties") if isinstance(feature, dict) else None
            if not isinstance(props, dict):
                continue
            file_features += 1
            features_seen += 1
            country = _text(props.get("country")) or default_country
            source_system = _text(props.get("source_system")) or default_source
            source_url = _text(props.get("source_url"))
            status = _text(props.get("status")) or "unknown"
            phase = _text(props.get("phase")) or "unknown"
            permit_type = _text(props.get("permit_type")) or "unknown"
            holder = _text(props.get("holder_name"))
            area = _number(props.get("area_hectares"))
            commodities = ["All commodities", *_commodity_parts(props.get("commodity"))]
            if len(commodities) == 1:
                commodities = ["All commodities"]

            for commodity in commodities:
                agg_key = (snapshot, country, source_system, source_url, commodity, status, phase, permit_type)
                if agg_key not in aggregate_groups:
                    aggregate_groups[agg_key] = _new_group(
                        group_type="aggregate",
                        snapshot_date=snapshot,
                        country=country,
                        source_system=source_system,
                        source_url=source_url,
                        source_artifact=artifact,
                        commodity=commodity,
                        status=status,
                        phase=phase,
                        permit_type=permit_type,
                    )
                _update_group(aggregate_groups[agg_key], props, area=area, artifact=artifact)

                if holder and holder.lower() not in {"unknown", "null", "none"}:
                    if commodity == "All commodities" or _is_critical_commodity(commodity):
                        holder_key = (snapshot, country, source_system, source_url, holder, commodity, status, phase)
                        if holder_key not in holder_groups:
                            holder_groups[holder_key] = _new_group(
                                group_type="holder",
                                snapshot_date=snapshot,
                                country=country,
                                source_system=source_system,
                                source_url=source_url,
                                source_artifact=artifact,
                                commodity=commodity,
                                status=status,
                                phase=phase,
                                permit_type="",
                                holder_name=holder[:180],
                            )
                        _update_group(holder_groups[holder_key], props, area=area, artifact=artifact)
        files_seen += 1
        if file_features == 0:
            files_empty += 1

    eligible_holders = [
        group
        for group in holder_groups.values()
        if (
            int(group["count"]) >= min_holder_records
            or float(group["area_hectares"]) >= min_holder_area_ha
            or _is_critical_commodity(str(group["commodity"]))
        )
    ]
    eligible_holders.sort(
        key=lambda g: (
            _is_critical_commodity(str(g["commodity"])),
            int(g["count"]),
            float(g["area_hectares"]),
            str(g["holder_name"] or ""),
        ),
        reverse=True,
    )
    kept_holders = eligible_holders[:max_holder_groups]

    rows: list[dict[str, Any]] = []
    for group in list(aggregate_groups.values()) + kept_holders:
        rows.append(
            _feed_row(
                group,
                metric="mining_land_permit_record_count",
                value=float(group["count"]),
                unit="permits",
            )
        )
        if group["has_area"] and float(group["area_hectares"]) > 0:
            rows.append(
                _feed_row(
                    group,
                    metric="mining_land_permit_area_hectares",
                    value=round(float(group["area_hectares"]), 4),
                    unit="hectares",
                )
            )

    rows.sort(key=lambda r: str(r["series_id"]))
    status = {
        "works": True,
        "rows": len(rows),
        "files_seen": files_seen,
        "files_empty": files_empty,
        "features_seen": features_seen,
        "aggregate_groups": len(aggregate_groups),
        "holder_groups_seen": len(holder_groups),
        "holder_groups_kept": len(kept_holders),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "reason": "local_miningterminal_geojson_compacted_without_geometry",
    }
    return rows, status


def collect(
    *,
    input_dir: Path | None = DEFAULT_INPUT_DIR,
    output_path: Path = OUT_PATH,
    max_holder_groups: int = DEFAULT_MAX_HOLDER_GROUPS,
    min_holder_records: int = DEFAULT_MIN_HOLDER_RECORDS,
    min_holder_area_ha: float = DEFAULT_MIN_HOLDER_AREA_HA,
    log=print,
) -> list[dict[str, Any]]:
    if input_dir is None or not input_dir.exists():
        status = {
            "works": False,
            "rows": 0,
            "files_seen": 0,
            "features_seen": 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "reason": f"missing MiningTerminal permit artifact directory: {input_dir}",
        }
        _write_status(status)
        log(status["reason"])
        return []
    paths = sorted(input_dir.glob("*.geojson"))
    if not paths:
        status = {
            "works": False,
            "rows": 0,
            "files_seen": 0,
            "features_seen": 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "reason": f"no GeoJSON permit artifacts found in {input_dir}",
        }
        _write_status(status)
        log(status["reason"])
        return []
    rows, status = summarize(
        paths,
        max_holder_groups=max_holder_groups,
        min_holder_records=min_holder_records,
        min_holder_area_ha=min_holder_area_ha,
    )
    _write_jsonl_atomic(rows, output_path=output_path)
    _write_status(status, status_path=output_path.with_suffix(".status.json"))
    log(
        f"wrote {len(rows)} compact MiningTerminal permit rows from "
        f"{status['features_seen']} features across {status['files_seen']} files to {output_path}"
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    parser.add_argument("--max-holder-groups", type=int, default=DEFAULT_MAX_HOLDER_GROUPS)
    parser.add_argument("--min-holder-records", type=int, default=DEFAULT_MIN_HOLDER_RECORDS)
    parser.add_argument("--min-holder-area-ha", type=float, default=DEFAULT_MIN_HOLDER_AREA_HA)
    args = parser.parse_args()
    collect(
        input_dir=args.input_dir,
        output_path=args.output,
        max_holder_groups=args.max_holder_groups,
        min_holder_records=args.min_holder_records,
        min_holder_area_ha=args.min_holder_area_ha,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
