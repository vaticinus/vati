"""ResourceContracts.org concession/contract metadata collector.

Small keyless collector for the official ResourceContracts API. It targets critical mining and
physical-supply resources, fetches a bounded set of contract metadata, and writes one normalized
row per contract. Full PDFs/text are intentionally not downloaded here.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_BASE = "https://api.resourcecontracts.org"
OUT_PATH = DATA_DIR / "feeds" / "resourcecontracts.jsonl"

REQUEST_TIMEOUT_S = 25
REQUEST_SPACING_S = 0.15
PER_RESOURCE_LIMIT = 25

RESOURCE_FILTERS: tuple[str, ...] = (
    "Copper",
    "Cobalt",
    "Nickel",
    "Lithium",
    "Critical Minerals",
    "Bauxite",
    "Iron Ore",
    "Gold",
    "Silver",
    "Rare Earth Elements",
    "Graphite",
)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_json(url: str) -> dict[str, Any] | list[Any] | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,*/*"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official public API
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - public API; skip rather than fabricate
        return None


def _group_url(resource: str, *, per_page: int) -> str:
    params = urllib.parse.urlencode(
        {
            "group": "metadata",
            "q": "",
            "resource": resource,
            "per_page": str(per_page),
            "sort_by": "year_signed",
            "order": "desc",
        }
    )
    return f"{API_BASE}/contracts/group?{params}"


def _metadata_url(contract_id: int | str) -> str:
    return f"{API_BASE}/contract/{contract_id}/metadata"


def _date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _year_end(value: Any) -> str | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if 1800 <= year <= 2100:
        return f"{year:04d}-12-31"
    return None


def _names(items: Any, *, key: str = "name") -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            value = item.get(key)
        else:
            value = item
        if value:
            out.append(str(value))
    return out


def _company_names(meta: dict[str, Any]) -> list[str]:
    companies: list[str] = []
    for part in meta.get("participation") or []:
        if not isinstance(part, dict):
            continue
        company = part.get("company")
        if isinstance(company, dict) and company.get("name"):
            companies.append(str(company["name"]))
    return sorted(set(companies))


def _file_urls(meta: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for item in meta.get("file") or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        files.append(
            {
                "url": item.get("url"),
                "media_type": item.get("media_type"),
                "byte_size": item.get("byte_size"),
            }
        )
    return files


def _row_from_metadata(meta: dict[str, Any], *, matched_resources: list[str]) -> dict[str, Any] | None:
    contract_id = meta.get("id")
    name = str(meta.get("name") or "").strip()
    if not contract_id or not name:
        return None
    date_signed = _date(meta.get("date_signed")) or _year_end(meta.get("year_signed"))
    published_at = _date(meta.get("published_at")) or date_signed or _today()
    event_time = date_signed or published_at
    resources = [str(v) for v in (meta.get("resource") or []) if v]
    countries = _names(meta.get("countries"))
    country_codes = _names(meta.get("countries"), key="code")
    concession_names = _names(meta.get("concession"))
    project = meta.get("project") if isinstance(meta.get("project"), dict) else {}
    project_name = str(project.get("name") or "").strip()
    companies = _company_names(meta)
    contract_types = [str(v) for v in (meta.get("contract_type") or []) if v]
    title_bits = [name]
    if resources:
        title_bits.append("resources " + ", ".join(resources[:4]))
    if countries:
        title_bits.append("countries " + ", ".join(countries[:3]))
    return {
        "feed": "resourcecontracts",
        "source_authority": "ResourceContracts.org API",
        "source_page_url": _metadata_url(contract_id),
        "jurisdiction": ", ".join(countries),
        "region": "Global",
        "series_id": f"resourcecontracts:contract:{contract_id}",
        "date": published_at,
        "as_of": published_at,
        "event_time": event_time,
        "published_at": published_at,
        "observed_at": event_time,
        "value": 1.0,
        "unit": "contract",
        "metric": "resource_contract_publication",
        "domain": "land_use",
        "title": "ResourceContracts " + " - ".join(title_bits),
        "contract_id": int(contract_id),
        "open_contracting_id": meta.get("open_contracting_id"),
        "contract_name": name,
        "year_signed": meta.get("year_signed"),
        "date_signed": date_signed,
        "published_at_full": meta.get("published_at"),
        "contract_type": contract_types,
        "document_type": meta.get("document_type") or meta.get("type"),
        "resources": resources,
        "matched_resource_filters": sorted(set(matched_resources)),
        "countries": countries,
        "country_codes": country_codes,
        "companies": companies,
        "government_entities": _names(meta.get("government_entity")),
        "project": project_name,
        "concessions": concession_names,
        "source_url": meta.get("source_url"),
        "file_urls": _file_urls(meta),
        "language": meta.get("language"),
        "is_contract_signed": meta.get("is_contract_signed"),
        "is_ocr_reviewed": meta.get("is_ocr_reviewed"),
        "cost_cents": 0,
        "provenance": "official_resourcecontracts_api_metadata",
    }


def collect(*, log=print, per_resource: int = PER_RESOURCE_LIMIT) -> list[dict[str, Any]]:
    matched: dict[int, set[str]] = {}
    metadata_cache: dict[int, dict[str, Any]] = {}
    for resource in RESOURCE_FILTERS:
        data = _fetch_json(_group_url(resource, per_page=per_resource))
        if not isinstance(data, dict):
            log(f"  - {resource:<20s} unreachable")
            continue
        results = data.get("results") if isinstance(data.get("results"), list) else []
        log(
            f"  + {resource:<20s} {len(results):3d} rows "
            f"(result_total={data.get('result_total') or data.get('total')})"
        )
        for item in results:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            cid = int(item["id"])
            matched.setdefault(cid, set()).add(resource)
            if cid not in metadata_cache:
                meta = _fetch_json(_metadata_url(cid))
                if isinstance(meta, dict):
                    metadata_cache[cid] = meta
                time.sleep(REQUEST_SPACING_S)
        time.sleep(REQUEST_SPACING_S)

    rows: list[dict[str, Any]] = []
    collected_at = datetime.now(timezone.utc).isoformat()
    for cid in sorted(metadata_cache):
        row = _row_from_metadata(metadata_cache[cid], matched_resources=sorted(matched.get(cid, ())))
        if row:
            row["collected_at"] = collected_at
            rows.append(row)
    rows.sort(key=lambda r: (str(r.get("published_at") or ""), int(r.get("contract_id") or 0)), reverse=True)
    if not rows:
        log(f"\nno ResourceContracts rows fetched; preserved existing feed at {OUT_PATH}")
        return []
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} unique ResourceContracts rows -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("ResourceContracts critical-resource contracts (keyless official API):")
    observations = collect()
    if not observations:
        print("\nNO rows collected - ResourceContracts API unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} rows:")
        for row in observations[:5]:
            print(
                "  "
                + json.dumps(
                    {
                        k: row[k]
                        for k in ("contract_id", "published_at", "contract_name", "resources", "countries")
                    },
                    ensure_ascii=False,
                )
            )
