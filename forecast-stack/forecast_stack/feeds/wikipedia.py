"""Wikipedia pageview attention/adoption collector.

Official Wikimedia REST API, keyless. This feed emits annual English Wikipedia pageview totals for
a bounded basket of forecast-relevant technology and adoption topics. It is deliberately an
attention/adoption proxy, not a capability signal: useful as a dated public-awareness state channel,
but never sufficient by itself for a structural forecast.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/all-agents"
OUT_PATH = DATA_DIR / "feeds" / "wikipedia.jsonl"

START_YEAR = 2016
END_YEAR = 2025
REQUEST_TIMEOUT_S = 30
REQUEST_SPACING_S = 0.15

ARTICLES: tuple[dict[str, str], ...] = (
    {"slug": "deep_learning", "topic": "Deep learning", "title": "Deep_learning"},
    {"slug": "large_language_model", "topic": "Large language model", "title": "Large_language_model"},
    {"slug": "generative_ai", "topic": "Generative artificial intelligence", "title": "Generative_artificial_intelligence"},
    {"slug": "quantum_computing", "topic": "Quantum computing", "title": "Quantum_computing"},
    {"slug": "crispr", "topic": "CRISPR", "title": "CRISPR"},
    {"slug": "mrna_vaccine", "topic": "mRNA vaccine", "title": "MRNA_vaccine"},
    {"slug": "gene_therapy", "topic": "Gene therapy", "title": "Gene_therapy"},
    {"slug": "solid_state_battery", "topic": "Solid-state battery", "title": "Solid-state_battery"},
    {"slug": "perovskite_solar_cell", "topic": "Perovskite solar cell", "title": "Perovskite_solar_cell"},
    {"slug": "lithium_ion_battery", "topic": "Lithium-ion battery", "title": "Lithium-ion_battery"},
    {"slug": "self_driving_car", "topic": "Self-driving car", "title": "Self-driving_car"},
    {"slug": "nuclear_fusion", "topic": "Nuclear fusion", "title": "Nuclear_fusion"},
    {"slug": "hydrogen_economy", "topic": "Hydrogen economy", "title": "Hydrogen_economy"},
    {"slug": "blockchain", "topic": "Blockchain", "title": "Blockchain"},
    {"slug": "single_cell_sequencing", "topic": "Single-cell sequencing", "title": "Single-cell_sequencing"},
)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_monthly_items(title: str, *, retries: int = 2) -> list[dict[str, Any]]:
    url = (
        f"{API_URL}/{urllib.parse.quote(title, safe='')}/monthly/"
        f"{START_YEAR}010100/{END_YEAR}123100"
    )
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            items = payload.get("items")
            return items if isinstance(items, list) else []
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            return []
    return []


def yearly_counts(items: list[dict[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for item in items:
        stamp = str(item.get("timestamp") or "")
        if len(stamp) < 4 or not stamp[:4].isdigit():
            continue
        try:
            views = int(item.get("views") or 0)
        except (TypeError, ValueError):
            continue
        year = int(stamp[:4])
        if START_YEAR <= year <= END_YEAR and views > 0:
            counts[year] += views
    return dict(counts)


def normalize_article(article: dict[str, str], counts: dict[int, int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, views in sorted(counts.items()):
        rows.append(
            {
                "series_id": article["title"],
                "date": f"{year}-12-31",
                "event_time": f"{year}-12-31",
                "observed_at": f"{year}-12-31",
                "published_at": f"{year}-12-31",
                "value": float(views),
                "unit": "views/year",
                "metric": "wikipedia_pageviews",
                "title": f"Wikipedia pageviews - {article['topic']}",
                "topic": article["topic"],
                "article": article["title"],
                "year": year,
            }
        )
    return rows


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for article in ARTICLES:
        items = _fetch_monthly_items(article["title"])
        article_rows = normalize_article(article, yearly_counts(items))
        if article_rows:
            rows.extend(article_rows)
            log(
                f"  + {article['slug']:<28s} "
                f"{article_rows[0]['date'][:4]}-{article_rows[-1]['date'][:4]} "
                f"{int(article_rows[0]['value'])}->{int(article_rows[-1]['value'])} views"
            )
        else:
            log(f"  - {article['slug']:<28s} no pageviews returned")
        time.sleep(REQUEST_SPACING_S)

    if not rows:
        log("no Wikipedia pageview observations fetched; not writing an empty file")
        return []
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("Wikipedia annual pageviews (Wikimedia REST, keyless):")
    observations = collect()
    for row in observations[:5]:
        print(
            "  "
            + json.dumps(
                {k: row[k] for k in ("series_id", "date", "value", "unit", "title")},
                ensure_ascii=False,
            )
        )
