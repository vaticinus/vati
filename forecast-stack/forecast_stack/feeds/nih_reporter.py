"""NIH RePORTER biomedical grant-count collector.

Official keyless NIH RePORTER v2 API. This V1 feed emits fiscal-year project counts for a bounded
basket of forecast-relevant biomedical technologies. Counts are lexical topic matches over project
title, terms, and abstract text; they are useful as a leading funding-effort signal, with NIH-only
coverage explicitly documented.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://api.reporter.nih.gov/v2/projects/search"
OUT_PATH = DATA_DIR / "feeds" / "nih_reporter.jsonl"

START_FISCAL_YEAR = 2008
END_FISCAL_YEAR = 2023
REQUEST_TIMEOUT_S = 30
REQUEST_SPACING_S = 0.12

TOPICS: tuple[dict[str, str], ...] = (
    {"slug": "crispr_gene_editing", "title": "CRISPR gene editing", "term": "crispr"},
    {"slug": "mrna_vaccine", "title": "mRNA vaccines", "term": "mRNA vaccine"},
    {"slug": "car_t_cell_therapy", "title": "CAR-T cell therapy", "term": "car t cell"},
    {"slug": "gene_therapy", "title": "Gene therapy", "term": "\"gene therapy\""},
    {"slug": "single_cell_rna_sequencing", "title": "Single-cell RNA sequencing", "term": "single cell rna sequencing"},
    {"slug": "cancer_immunotherapy", "title": "Cancer immunotherapy", "term": "\"cancer immunotherapy\""},
    {"slug": "organoid", "title": "Organoids", "term": "organoid"},
    {"slug": "microbiome", "title": "Microbiome", "term": "microbiome"},
    {"slug": "antibody_engineering", "title": "Antibody engineering", "term": "\"antibody engineering\""},
    {"slug": "synthetic_biology", "title": "Synthetic biology", "term": "\"synthetic biology\""},
    {"slug": "radioligand_therapy", "title": "Radioligand therapy", "term": "radioligand"},
    {"slug": "glp1_obesity_drugs", "title": "GLP-1 obesity drugs", "term": "semaglutide"},
)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _post_json(payload: dict[str, Any], *, retries: int = 2) -> dict[str, Any] | None:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
    return None


def count_topic_year(topic: dict[str, str], fiscal_year: int) -> int | None:
    payload = {
        "criteria": {
            "advanced_text_search": {
                "operator": "and",
                "search_field": "projecttitle,terms,abstracttext",
                "search_text": topic["term"],
            },
            "fiscal_years": [fiscal_year],
        },
        "include_fields": ["FiscalYear"],
        "limit": 1,
        "offset": 0,
    }
    data = _post_json(payload)
    if not isinstance(data, dict):
        return None
    total = (data.get("meta") or {}).get("total")
    return int(total) if isinstance(total, int) else None


def normalize_topic(topic: dict[str, str], counts: dict[int, int | None]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fiscal_year, count in sorted(counts.items()):
        if count is None:
            continue
        rows.append(
            {
                "series_id": f"nih_reporter:{topic['slug']}:awards",
                "date": f"{fiscal_year}-12-31",
                "value": float(count),
                "unit": "awards/year",
                "metric": "nih_awards_per_year",
                "title": f"NIH RePORTER - {topic['title']} awards per fiscal year",
                "topic": topic["title"],
                "term": topic["term"],
                "fiscal_year": fiscal_year,
            }
        )
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    years = range(START_FISCAL_YEAR, END_FISCAL_YEAR + 1)
    for topic in TOPICS:
        counts: dict[int, int | None] = {}
        for year in years:
            counts[year] = count_topic_year(topic, year)
            time.sleep(REQUEST_SPACING_S)
        topic_rows = normalize_topic(topic, counts)
        if len(topic_rows) >= 8 and max(float(r["value"]) for r in topic_rows) > 0:
            rows.extend(topic_rows)
            log(
                f"  + {topic['slug']:<28s} "
                f"{topic_rows[0]['date'][:4]}-{topic_rows[-1]['date'][:4]} "
                f"{int(topic_rows[0]['value'])}->{int(topic_rows[-1]['value'])} awards"
            )
        else:
            log(f"  - {topic['slug']:<28s} only {len(topic_rows)} fiscal years returned")

    if not rows:
        log("no NIH RePORTER observations fetched; not writing an empty file")
        return []
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("NIH RePORTER biomedical grant counts (keyless official API):")
    observations = collect()
    for row in observations[:5]:
        print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
