"""Canada IAAC impact-assessment project collector.

This is a small, keyless land-permit/EIA collector for the Impact Assessment Agency of
Canada registry. It deliberately avoids bulk document downloads: it fetches bounded public
search-result pages, parses project cards, and writes one normalized JSONL row per project.

The feed is useful before a full land-permit lake exists because it gives dated, project-level
state for mines, roads, LNG, energy, and other physically constrained projects.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
BASE_URL = "https://iaac-aeic.gc.ca"
OUT_PATH = DATA_DIR / "feeds" / "land_permits_canada_iaac.jsonl"

REQUEST_TIMEOUT_S = 25
REQUEST_SPACING_S = 0.35

PAGES: tuple[dict[str, str], ...] = (
    {
        "scope": "active_projects",
        "url": f"{BASE_URL}/050/evaluations/exploration?active=true&document_type=project",
    },
    {
        "scope": "permits",
        "url": f"{BASE_URL}/050/evaluations/exploration?permits=true&document_type=project",
    },
    {
        "scope": "federal_lands",
        "url": f"{BASE_URL}/050/evaluations/exploration?fedLands=true&document_type=project",
    },
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


def _fetch_text(url: str) -> str | None:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official public register
            return resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - public endpoint; skip rather than fabricate
        return None


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value)
    value = re.sub(r"(?is)<span[^>]*class=[\"'][^\"']*wb-inv[^\"']*[\"'][^>]*>.*?</span>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    return " ".join(value.split())


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "unknown"


def _field(block: str, label: str) -> str:
    pattern = rf"(?is)<strong>\s*{re.escape(label)}\s*:\s*</strong>\s*(.*?)</li>"
    match = re.search(pattern, block)
    return _clean_text(match.group(1)) if match else ""


def _link(block: str) -> str:
    match = re.search(r'(?is)<a[^>]+class=["\'][^"\']*resultJobItem[^"\']*["\'][^>]+href=["\']([^"\']+)["\']', block)
    return html.unescape(match.group(1)) if match else ""


def _title(block: str) -> str:
    match = re.search(r'(?is)<span[^>]+class=["\'][^"\']*noctitle[^"\']*["\'][^>]*>(.*?)</span>', block)
    return _clean_text(match.group(1)) if match else ""


def _location(block: str) -> str:
    match = re.search(r'(?is)<li[^>]+class=["\'][^"\']*location[^"\']*["\'][^>]*>(.*?)</li>', block)
    return _clean_text(match.group(1)) if match else ""


def _description(block: str) -> str:
    match = re.search(r'(?is)<li[^>]+class=["\'][^"\']*business[^"\']*["\'][^>]*>(.*?)</li>', block)
    return _clean_text(match.group(1)) if match else ""


def _parse_page(source: dict[str, str], text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in re.finditer(r"(?is)<article\b.*?</article>", text):
        block = match.group(0)
        ref = _field(block, "Reference Number")
        project = _title(block)
        if not ref or not project:
            continue
        url = _link(block)
        if url.startswith("/"):
            url = BASE_URL + url
        last_modified = _field(block, "Last Modified")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", last_modified or ""):
            last_modified = _today()
        status = _field(block, "Status")
        assessment_type = _field(block, "Assessment Type")
        relevance = _field(block, "Relevance")
        try:
            relevance_score = float(relevance) if relevance else None
        except ValueError:
            relevance_score = None
        rows.append(
            {
                "feed": "land_permits_canada_iaac",
                "source_page_scope": source["scope"],
                "source_page_url": source["url"],
                "source_authority": "Impact Assessment Agency of Canada",
                "jurisdiction": "Canada",
                "region": "North America",
                "series_id": f"land_permits_canada_iaac:project:{ref}",
                "date": last_modified,
                "as_of": last_modified,
                "event_time": last_modified,
                "published_at": last_modified,
                "observed_at": last_modified,
                "value": 1.0,
                "unit": "project",
                "metric": "impact_assessment_project_status",
                "domain": "land_use_policy",
                "title": f"Canada IAAC project {project} - {status or 'status unknown'}",
                "project": project,
                "reference_number": ref,
                "status": status,
                "assessment_type": assessment_type,
                "location": _location(block),
                "description": _description(block),
                "url": url,
                "relevance_score": relevance_score,
                "cost_cents": 0,
                "provenance": "official_iaac_search_result_page",
            }
        )
    return rows


def parse_pages(pages: list[tuple[dict[str, str], str]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    scopes: dict[str, set[str]] = defaultdict(set)
    source_urls: dict[str, set[str]] = defaultdict(set)
    for source, text in pages:
        for row in _parse_page(source, text):
            ref = str(row["reference_number"])
            scopes[ref].add(str(row["source_page_scope"]))
            source_urls[ref].add(str(row["source_page_url"]))
            existing = merged.get(ref)
            if existing is None or str(row["date"]) > str(existing.get("date") or ""):
                merged[ref] = row
    rows = []
    collected_at = datetime.now(timezone.utc).isoformat()
    for ref, row in merged.items():
        scope_list = sorted(scopes[ref])
        row = {
            **row,
            "source_page_scope": ",".join(scope_list),
            "source_page_urls": sorted(source_urls[ref]),
            "collected_at": collected_at,
        }
        # Keep the title dense enough for topic matching in the generic world-state pack.
        row["title"] = (
            f"Canada IAAC project {row['project']} - {row.get('status') or 'status unknown'}"
            f" - {row.get('assessment_type') or 'assessment type unknown'}"
            f" - scopes {row['source_page_scope']}"
        )
        row["series_id"] = f"land_permits_canada_iaac:project:{ref}"
        rows.append(row)
    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("reference_number") or "")), reverse=True)
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    pages: list[tuple[dict[str, str], str]] = []
    for source in PAGES:
        text = _fetch_text(source["url"])
        if not text:
            log(f"  - {source['scope']}: unreachable")
            continue
        parsed = _parse_page(source, text)
        log(f"  + {source['scope']:<14s} {len(parsed):4d} project rows")
        pages.append((source, text))
        time.sleep(REQUEST_SPACING_S)
    rows = parse_pages(pages)
    if not rows:
        log(f"\nno IAAC project rows fetched; preserved existing feed at {OUT_PATH}")
        return []
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} unique IAAC project rows -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("Canada IAAC impact-assessment project states (keyless official pages):")
    observations = collect()
    if not observations:
        print("\nNO rows collected - IAAC registry unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} rows:")
        for row in observations[:5]:
            print(
                "  "
                + json.dumps(
                    {k: row[k] for k in ("reference_number", "date", "project", "status", "assessment_type")},
                    ensure_ascii=False,
                )
            )
