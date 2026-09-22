"""Europe PMC bounded paper-metadata collector.

Official Europe PMC REST API, keyless. This V1 feed deliberately avoids bulk full-text/OCR/LLM
work. It emits two small, machine-queryable layers:

1. annual topic publication counts, useful as trend features;
2. bounded recent paper metadata rows, useful for "what papers existed by date T?" state packs.

Full-text extraction, annotation harvesting, and large backfills remain approval-gated.
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
API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OUT_PATH = DATA_DIR / "feeds" / "europe_pmc.jsonl"

START_YEAR = 2015
END_YEAR = 2025
PAPER_PAGE_SIZE = 8
REQUEST_TIMEOUT_S = 45
REQUEST_SPACING_S = 0.25

TOPICS: tuple[dict[str, str], ...] = (
    {"slug": "crispr_gene_editing", "title": "CRISPR gene editing", "term": "CRISPR"},
    {"slug": "mrna_therapeutics", "title": "mRNA therapeutics", "term": '"mRNA" AND (therapeutic OR vaccine)'},
    {"slug": "car_t_cell_therapy", "title": "CAR-T cell therapy", "term": '"CAR T" OR "CAR-T"'},
    {"slug": "gene_therapy", "title": "Gene therapy", "term": '"gene therapy"'},
    {"slug": "single_cell_rna_sequencing", "title": "Single-cell RNA sequencing", "term": '"single cell RNA" OR scRNA-seq'},
    {"slug": "cancer_immunotherapy", "title": "Cancer immunotherapy", "term": '"cancer immunotherapy"'},
    {"slug": "organoid", "title": "Organoids", "term": "organoid"},
    {"slug": "microbiome", "title": "Microbiome", "term": "microbiome"},
    {"slug": "synthetic_biology", "title": "Synthetic biology", "term": '"synthetic biology"'},
    {"slug": "radioligand_therapy", "title": "Radioligand therapy", "term": "radioligand"},
    {"slug": "glp1_obesity_drugs", "title": "GLP-1 obesity drugs", "term": "semaglutide OR tirzepatide"},
    {"slug": "antimicrobial_resistance", "title": "Antimicrobial resistance", "term": '"antimicrobial resistance"'},
    {"slug": "ai_drug_discovery", "title": "AI drug discovery", "term": '"AI drug discovery" OR \"artificial intelligence drug discovery\"'},
    {"slug": "rare_disease_therapies", "title": "Rare disease therapies", "term": '"rare disease" AND (therapy OR treatment)'},
    {"slug": "water_stress_health", "title": "Water stress and health", "term": '"water stress" AND health'},
    {"slug": "mining_environmental_health", "title": "Mining environmental health", "term": '(mining OR mine) AND "environmental health"'},
)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_json(params: dict[str, Any], *, retries: int = 2) -> dict[str, Any] | None:
    query = urllib.parse.urlencode(params)
    url = f"{API_URL}?{query}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return None
    return None


def _date_range_query(term: str, start: date, end: date) -> str:
    return f"({term}) AND FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}]"


def _hit_count(term: str, year: int) -> int | None:
    data = _fetch_json(
        {
            "query": _date_range_query(term, date(year, 1, 1), date(year, 12, 31)),
            "format": "json",
            "pageSize": 1,
            "resultType": "lite",
        }
    )
    if not isinstance(data, dict):
        return None
    raw = data.get("hitCount")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _paper_results(term: str, *, page_size: int, today: date) -> list[dict[str, Any]]:
    data = _fetch_json(
        {
            "query": _date_range_query(term, date(START_YEAR, 1, 1), today),
            "format": "json",
            "pageSize": int(page_size),
            "resultType": "core",
            "sort": "FIRST_PDATE_D desc",
        },
        retries=1,
    )
    if not isinstance(data, dict):
        return []
    result_list = data.get("resultList") or {}
    results = result_list.get("result") or []
    return [r for r in results if isinstance(r, dict)]


def _first(values: list[str | None]) -> str | None:
    for value in values:
        if value:
            text = str(value).strip()
            if text:
                return text
    return None


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _source_id(result: dict[str, Any]) -> str | None:
    source = str(result.get("source") or "").strip()
    rid = str(result.get("id") or result.get("pmid") or result.get("pmcid") or "").strip()
    if source and rid:
        return f"{source}:{rid}"
    doi = str(result.get("doi") or "").strip()
    return f"DOI:{doi.lower()}" if doi else None


def _authors(result: dict[str, Any]) -> tuple[str, int]:
    raw = str(result.get("authorString") or "").strip()
    if raw:
        parts = [p.strip(" .") for p in raw.split(",") if p.strip(" .")]
        return raw, len(parts)
    author_list = ((result.get("authorList") or {}).get("author") or [])
    if isinstance(author_list, dict):
        author_list = [author_list]
    names: list[str] = []
    for author in author_list if isinstance(author_list, list) else []:
        name = str(author.get("fullName") or author.get("lastName") or "").strip()
        if name:
            names.append(name)
    return "; ".join(names), len(names)


def _journal(result: dict[str, Any]) -> str:
    journal_info = result.get("journalInfo") if isinstance(result.get("journalInfo"), dict) else {}
    return str(result.get("journalTitle") or journal_info.get("journal", {}).get("title") or "").strip()


def _paper_row(topic: dict[str, str], result: dict[str, Any], *, today: date) -> dict[str, Any] | None:
    published = _iso_date(
        _first(
            [
                result.get("firstPublicationDate"),
                result.get("electronicPublicationDate"),
                result.get("dateOfCreation"),
            ]
        )
    )
    if not published or published > today.isoformat():
        return None
    paper_external_id = _source_id(result)
    if not paper_external_id:
        return None
    title = " ".join(str(result.get("title") or "").split())
    if not title:
        return None
    authors, n_authors = _authors(result)
    journal = _journal(result)
    doi = str(result.get("doi") or "").strip()
    pmid = str(result.get("pmid") or "").strip()
    pmcid = str(result.get("pmcid") or "").strip()
    series_id = f"europe_pmc:paper:{topic['slug']}:{paper_external_id}"
    source_label = paper_external_id.replace(":", " ")
    title_parts = [f"Europe PMC paper - {title}", f"topic {topic['title']}"]
    if journal:
        title_parts.append(journal)
    if doi:
        title_parts.append(f"DOI {doi}")
    if pmid:
        title_parts.append(f"PMID {pmid}")
    return {
        "feed": "europe_pmc",
        "series_id": series_id,
        "date": published,
        "event_time": published,
        "published_at": published,
        "observed_at": published,
        "value": 1.0,
        "unit": "paper",
        "metric": "europe_pmc_paper_publication",
        "title": " | ".join(title_parts),
        "topic": topic["title"],
        "topic_slug": topic["slug"],
        "term": topic["term"],
        "paper_provider": "europe_pmc",
        "paper_external_id": paper_external_id,
        "paper_source": str(result.get("source") or ""),
        "paper_id": str(result.get("id") or ""),
        "paper_source_label": source_label,
        "paper_title": title,
        "paper_abstract": str(result.get("abstractText") or "").strip(),
        "paper_authors": authors,
        "paper_n_authors": n_authors,
        "paper_journal": journal,
        "paper_doi": doi,
        "paper_pmid": pmid,
        "paper_pmcid": pmcid,
        "paper_first_index_date": _iso_date(result.get("firstIndexDate")),
        "paper_updated": _iso_date(result.get("dateOfRevision") or result.get("updateDate")),
        "paper_primary_category": topic["slug"],
        "paper_categories": f"europe_pmc {topic['slug']}",
        "paper_is_open_access": bool(result.get("isOpenAccess") == "Y" or result.get("isOpenAccess") is True),
        "paper_has_text_mined_terms": bool(result.get("hasTextMinedTerms") == "Y" or result.get("hasTextMinedTerms") is True),
        "provenance": "official_europe_pmc_rest_search_result",
        "cost_cents": 0,
    }


def normalize_topic(
    topic: dict[str, str],
    counts: dict[int, int | None],
    papers: list[dict[str, Any]],
    *,
    today: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, count in sorted(counts.items()):
        if count is None:
            continue
        rows.append(
            {
                "feed": "europe_pmc",
                "series_id": f"europe_pmc:{topic['slug']}:publications",
                "date": f"{year}-12-31",
                "event_time": f"{year}-12-31",
                "published_at": f"{year}-12-31",
                "observed_at": f"{year}-12-31",
                "value": float(count),
                "unit": "publications/year",
                "metric": "europe_pmc_publications_per_year",
                "title": f"Europe PMC - {topic['title']} publications per first publication year",
                "topic": topic["title"],
                "topic_slug": topic["slug"],
                "term": topic["term"],
                "publication_year": year,
                "provenance": "official_europe_pmc_rest_search_hit_count",
                "cost_cents": 0,
            }
        )
    seen: set[str] = set()
    for result in papers:
        row = _paper_row(topic, result, today=today)
        if not row:
            continue
        key = str(row["paper_external_id"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def collect(*, log=print, paper_page_size: int = PAPER_PAGE_SIZE) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    years = range(START_YEAR, END_YEAR + 1)
    today = date.today()
    for topic in TOPICS:
        counts: dict[int, int | None] = {}
        for year in years:
            counts[year] = _hit_count(topic["term"], year)
            time.sleep(REQUEST_SPACING_S)
        papers = _paper_results(topic["term"], page_size=paper_page_size, today=today)
        time.sleep(REQUEST_SPACING_S)
        topic_rows = normalize_topic(topic, counts, papers, today=today)
        rows.extend(topic_rows)
        count_rows = [r for r in topic_rows if r.get("metric") == "europe_pmc_publications_per_year"]
        paper_rows = [r for r in topic_rows if r.get("metric") == "europe_pmc_paper_publication"]
        if count_rows:
            log(
                f"  + {topic['slug']:<30s} "
                f"{count_rows[0]['date'][:4]}-{count_rows[-1]['date'][:4]} "
                f"{int(count_rows[0]['value'])}->{int(count_rows[-1]['value'])} publications, "
                f"{len(paper_rows)} papers"
            )
        else:
            log(f"  - {topic['slug']:<30s} no counts returned, {len(paper_rows)} papers")

    if not rows:
        log("no Europe PMC rows fetched; not writing an empty file")
        return []
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} rows -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("Europe PMC bounded paper metadata (REST API, keyless):")
    observations = collect()
    for row in observations[:5]:
        print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
