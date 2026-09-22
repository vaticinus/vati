"""NSF award-count collector.

Official keyless NSF Research.gov Award API. This V1 feed emits calendar-year award counts for a
bounded basket of forecast-relevant science, compute, manufacturing, and energy topics. Counts come
from API metadata.totalCount, so we do not paginate the full awards warehouse or estimate dollars.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://www.research.gov/awardapi-service/v1/awards.json"
OUT_PATH = DATA_DIR / "feeds" / "nsf_awards.jsonl"

START_YEAR = int(os.environ.get("NSF_START_YEAR", "2008"))
END_YEAR = int(os.environ.get("NSF_END_YEAR", "2023"))
REQUEST_TIMEOUT_S = float(os.environ.get("NSF_REQUEST_TIMEOUT_S", "8"))
REQUEST_SPACING_S = float(os.environ.get("NSF_REQUEST_SPACING_S", "0.15"))
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("NSF_MAX_CONSECUTIVE_FAILURES", "8"))
TOPIC_LIMIT = int(os.environ.get("NSF_TOPIC_LIMIT", "0"))
TOPIC_OFFSET = int(os.environ.get("NSF_TOPIC_OFFSET", "0"))

TOPICS: tuple[dict[str, str], ...] = (
    {"slug": "artificial_intelligence", "title": "Artificial intelligence", "term": "artificial intelligence"},
    {"slug": "machine_learning", "title": "Machine learning", "term": "machine learning"},
    {"slug": "robotics", "title": "Robotics", "term": "robotics"},
    {"slug": "quantum_computing", "title": "Quantum computing", "term": "quantum computing"},
    {"slug": "semiconductors", "title": "Semiconductors", "term": "semiconductor"},
    {"slug": "microelectronics", "title": "Microelectronics", "term": "microelectronics"},
    {"slug": "advanced_manufacturing", "title": "Advanced manufacturing", "term": "advanced manufacturing"},
    {"slug": "cybersecurity", "title": "Cybersecurity", "term": "cybersecurity"},
    {"slug": "fusion_energy", "title": "Fusion energy", "term": "fusion energy"},
    {"slug": "battery_storage", "title": "Battery storage", "term": "battery"},
    {"slug": "power_grid", "title": "Power grid", "term": "power grid"},
    {"slug": "synthetic_biology", "title": "Synthetic biology", "term": "synthetic biology"},
)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _read_existing_rows() -> list[dict[str, Any]]:
    if not OUT_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with OUT_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def merge_rows(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*existing, *incoming]:
        series_id = str(row.get("series_id") or "")
        day = str(row.get("date") or "")
        if not series_id or not day:
            continue
        by_key[(series_id, day)] = row
    return [
        by_key[k]
        for k in sorted(by_key, key=lambda item: (item[0], item[1]))
    ]


def _year_range(year: int) -> tuple[str, str]:
    return f"01/01/{year}", f"12/31/{year}"


def count_topic_year(topic: dict[str, str], year: int) -> int | None:
    start, end = _year_range(year)
    params = {
        "keyword": topic["term"],
        "dateStart": start,
        "dateEnd": end,
        "offset": "1",
        "rpp": "1",
        "printFields": "id,title,date",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - official keyless endpoint; skip rather than fabricate
        return None
    metadata = ((data.get("response") or {}).get("metadata") or {}) if isinstance(data, dict) else {}
    total = metadata.get("totalCount")
    return int(total) if isinstance(total, int) else None


def normalize_topic(topic: dict[str, str], counts: dict[int, int | None]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, count in sorted(counts.items()):
        if count is None:
            continue
        rows.append(
            {
                "series_id": f"nsf_awards:{topic['slug']}:awards",
                "date": f"{year}-12-31",
                "value": float(count),
                "unit": "awards/year",
                "metric": "nsf_awards_per_year",
                "title": f"NSF Awards - {topic['title']} awards per calendar year",
                "topic": topic["title"],
                "term": topic["term"],
                "year": year,
            }
        )
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    existing_rows = _read_existing_rows()
    rows: list[dict[str, Any]] = []
    years = range(START_YEAR, END_YEAR + 1)
    topics = TOPICS[TOPIC_OFFSET:]
    if TOPIC_LIMIT > 0:
        topics = topics[:TOPIC_LIMIT]
    consecutive_failures = 0
    for topic in topics:
        counts: dict[int, int | None] = {}
        for year in years:
            count = count_topic_year(topic, year)
            counts[year] = count
            if count is None:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log(
                        f"stopping early after {consecutive_failures} consecutive NSF API failures; "
                        f"checkpoint rows kept: {len(rows)}"
                    )
                    return rows
            else:
                consecutive_failures = 0
            time.sleep(REQUEST_SPACING_S)
        topic_rows = normalize_topic(topic, counts)
        if len(topic_rows) >= 8 and max(float(r["value"]) for r in topic_rows) > 0:
            rows.extend(topic_rows)
            _write_jsonl_atomic(merge_rows(existing_rows, rows))
            log(
                f"  + {topic['slug']:<24s} "
                f"{topic_rows[0]['date'][:4]}-{topic_rows[-1]['date'][:4]} "
                f"{int(topic_rows[0]['value'])}->{int(topic_rows[-1]['value'])} awards"
            )
        else:
            log(f"  - {topic['slug']:<24s} only {len(topic_rows)} usable years returned")

    if not rows:
        log("no NSF award observations fetched; not writing an empty file")
        return []
    merged = merge_rows(existing_rows, rows)
    _write_jsonl_atomic(merged)
    log(f"\nwrote {len(merged)} observations -> {OUT_PATH} ({len(rows)} fetched this run)")
    return merged


if __name__ == "__main__":
    print("NSF award counts (keyless official Research.gov API):")
    observations = collect()
    for row in observations[:5]:
        print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
