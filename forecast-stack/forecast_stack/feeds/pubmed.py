"""PubMed topic publication-count collector.

Official NCBI E-utilities ESearch API, keyless. This V1 feed emits annual PubMed publication
counts for a bounded basket of biomedical topics. It deliberately stores only yearly counts, not
full articles or abstracts, so it gives a global biomedical literature signal without pulling a
local corpus.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
OUT_PATH = DATA_DIR / "feeds" / "pubmed.jsonl"

START_YEAR = 2015
END_YEAR = 2025
REQUEST_TIMEOUT_S = 20
REQUEST_SPACING_S = 0.36

TOPICS: tuple[dict[str, str], ...] = (
    {"slug": "crispr_gene_editing", "title": "CRISPR gene editing", "term": "CRISPR"},
    {"slug": "mrna_therapeutics", "title": "mRNA therapeutics", "term": '"mRNA" AND (therapeutic OR vaccine)'},
    {"slug": "car_t_cell_therapy", "title": "CAR-T cell therapy", "term": '"CAR T" OR "CAR-T"'},
    {"slug": "gene_therapy", "title": "Gene therapy", "term": '"gene therapy"'},
    {"slug": "single_cell_rna_sequencing", "title": "Single-cell RNA sequencing", "term": '"single cell RNA" OR scRNA-seq'},
    {"slug": "cancer_immunotherapy", "title": "Cancer immunotherapy", "term": '"cancer immunotherapy"'},
    {"slug": "organoid", "title": "Organoids", "term": "organoid"},
    {"slug": "microbiome", "title": "Microbiome", "term": "microbiome"},
    {"slug": "antibody_engineering", "title": "Antibody engineering", "term": '"antibody engineering"'},
    {"slug": "synthetic_biology", "title": "Synthetic biology", "term": '"synthetic biology"'},
    {"slug": "radioligand_therapy", "title": "Radioligand therapy", "term": "radioligand"},
    {"slug": "glp1_obesity_drugs", "title": "GLP-1 obesity drugs", "term": "semaglutide OR tirzepatide"},
)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_count(term: str, year: int, *, retries: int = 2) -> int | None:
    params = {
        "db": "pubmed",
        "term": term,
        "mindate": f"{year}/01/01",
        "maxdate": f"{year}/12/31",
        "datetype": "pdat",
        "retmode": "json",
        "rettype": "count",
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
                data = json.loads(resp.read().decode("utf-8", "replace"))
            count = ((data.get("esearchresult") or {}).get("count"))
            return int(count) if count is not None else None
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
    return None


def normalize_topic(topic: dict[str, str], counts: dict[int, int | None]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, count in sorted(counts.items()):
        if count is None:
            continue
        rows.append(
            {
                "series_id": f"pubmed:{topic['slug']}:publications",
                "date": f"{year}-12-31",
                "value": float(count),
                "unit": "publications/year",
                "metric": "pubmed_publications_per_year",
                "title": f"PubMed - {topic['title']} publications per publication year",
                "topic": topic["title"],
                "term": topic["term"],
                "publication_year": year,
            }
        )
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    years = range(START_YEAR, END_YEAR + 1)
    for topic in TOPICS:
        counts: dict[int, int | None] = {}
        for year in years:
            counts[year] = _fetch_count(topic["term"], year)
            time.sleep(REQUEST_SPACING_S)
        topic_rows = normalize_topic(topic, counts)
        if topic_rows:
            rows.extend(topic_rows)
            log(
                f"  + {topic['slug']:<28s} "
                f"{topic_rows[0]['date'][:4]}-{topic_rows[-1]['date'][:4]} "
                f"{int(topic_rows[0]['value'])}->{int(topic_rows[-1]['value'])} publications"
            )
        else:
            log(f"  - {topic['slug']:<28s} no publication counts returned")

    if not rows:
        log("no PubMed observations fetched; not writing an empty file")
        return []
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("PubMed topic publication counts (NCBI ESearch, keyless):")
    observations = collect()
    for row in observations[:5]:
        print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
