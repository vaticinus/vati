"""ClinicalTrials.gov therapeutic pipeline collector.

Official NLM/NIH ClinicalTrials.gov API v2, keyless. This is a bounded V1 pipeline signal, not a
full trials warehouse: for a small set of forecast-relevant technologies/therapeutic areas it emits
first-posted study counts plus current snapshot counts by status and phase.

Leak discipline:
* first-posted counts are dated to `studyFirstPostDate`, the date the registry record became public.
* current status/phase snapshots are dated to the fetch date, so they never appear in older as-of
  state packs.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://clinicaltrials.gov/api/v2/studies"
OUT_PATH = DATA_DIR / "feeds" / "clinicaltrials.jsonl"

PAGE_SIZE = 100
MAX_PAGES_PER_TOPIC = 3
REQUEST_TIMEOUT_S = 25
REQUEST_SPACING_S = 0.25
MIN_REFRESH_FRACTION = 0.75

TOPICS: tuple[dict[str, str], ...] = (
    {
        "slug": "glp1_obesity_drugs",
        "title": "GLP-1 obesity drugs",
        "term": "GLP-1 OR semaglutide OR tirzepatide OR liraglutide",
    },
    {"slug": "crispr_gene_editing", "title": "CRISPR gene editing", "term": "CRISPR OR Cas9"},
    {"slug": "gene_therapy", "title": "Gene therapy", "term": "\"gene therapy\""},
    {"slug": "mrna_therapeutics", "title": "mRNA therapeutics", "term": "mRNA vaccine OR mRNA therapeutics"},
    {"slug": "radioligand_therapy", "title": "Radioligand therapy", "term": "radioligand OR radiopharmaceutical"},
    {"slug": "car_t_cell_therapy", "title": "CAR-T cell therapy", "term": "CAR-T OR chimeric antigen receptor"},
    {"slug": "alzheimers_disease", "title": "Alzheimer's disease", "term": "Alzheimer"},
    {
        "slug": "antimicrobial_resistance",
        "title": "Antimicrobial resistance",
        "term": "\"antimicrobial resistance\" OR antibiotic resistant",
    },
)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _fetch_json(term: str, *, page_token: str | None = None) -> dict[str, Any] | None:
    params = {
        "query.term": term,
        "format": "json",
        "pageSize": str(PAGE_SIZE),
    }
    if page_token:
        params["pageToken"] = page_token
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — keyless public endpoint; skip rather than fabricate
        return None


def fetch_topic(topic: dict[str, str], *, log=print) -> list[dict[str, Any]]:
    studies: list[dict[str, Any]] = []
    token: str | None = None
    for page in range(MAX_PAGES_PER_TOPIC):
        data = _fetch_json(topic["term"], page_token=token)
        if not isinstance(data, dict):
            log(f"  - {topic['slug']}: page {page + 1} unreachable")
            break
        batch = data.get("studies") if isinstance(data.get("studies"), list) else []
        studies.extend(s for s in batch if isinstance(s, dict))
        token = data.get("nextPageToken")
        if not token:
            break
        time.sleep(REQUEST_SPACING_S)
    return studies


def _date_from_struct(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict):
        return None
    raw = str(value.get("date") or "")[:10]
    if len(raw) >= 7 and raw[4] == "-":
        return raw if len(raw) == 10 else f"{raw}-01"
    return None


def _protocol(study: dict[str, Any]) -> dict[str, Any]:
    return study.get("protocolSection") if isinstance(study.get("protocolSection"), dict) else {}


def _study_first_posted(study: dict[str, Any]) -> str | None:
    status = _protocol(study).get("statusModule") or {}
    return _date_from_struct(status.get("studyFirstPostDateStruct")) or str(status.get("studyFirstSubmitDate") or "")[:10] or None


def _status(study: dict[str, Any]) -> str:
    status = _protocol(study).get("statusModule") or {}
    return str(status.get("overallStatus") or "UNKNOWN").upper().replace(" ", "_")


def _phases(study: dict[str, Any]) -> list[str]:
    design = _protocol(study).get("designModule") or {}
    phases = design.get("phases") if isinstance(design.get("phases"), list) else []
    return [str(p).upper().replace(" ", "_") for p in phases if str(p).strip()] or ["NOT_APPLICABLE"]


def _enrollment(study: dict[str, Any]) -> float | None:
    design = _protocol(study).get("designModule") or {}
    info = design.get("enrollmentInfo") if isinstance(design.get("enrollmentInfo"), dict) else {}
    try:
        value = float(info.get("count"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _study_stub(study: dict[str, Any]) -> dict[str, Any]:
    p = _protocol(study)
    ident = p.get("identificationModule") or {}
    sponsor = p.get("sponsorCollaboratorsModule") or {}
    lead = sponsor.get("leadSponsor") if isinstance(sponsor.get("leadSponsor"), dict) else {}
    return {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle"),
        "first_posted": _study_first_posted(study),
        "status": _status(study),
        "phases": _phases(study),
        "enrollment": _enrollment(study),
        "lead_sponsor": lead.get("name"),
    }


def _examples(studies: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    return [_study_stub(s) for s in studies[:limit]]


def normalize(topic: dict[str, str], studies: list[dict[str, Any]], *, snapshot_date: str | None = None) -> list[dict[str, Any]]:
    snapshot_date = snapshot_date or _today()
    posted_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    status_counts = Counter()
    phase_counts = Counter()
    for study in studies:
        posted = _study_first_posted(study)
        if posted and len(posted) == 10:
            posted_by_date[posted].append(study)
        status_counts[_status(study)] += 1
        for phase in _phases(study):
            phase_counts[phase] += 1

    rows: list[dict[str, Any]] = []
    for posted, day_studies in sorted(posted_by_date.items()):
        rows.append({
            "series_id": f"clinicaltrials:{topic['slug']}:posted_studies",
            "date": posted,
            "value": float(len(day_studies)),
            "unit": "studies",
            "metric": "trial_registry_posts",
            "title": f"ClinicalTrials.gov — {topic['title']} first posted studies",
            "topic": topic["title"],
            "term": topic["term"],
            "studies": _examples(day_studies),
        })

    rows.append({
        "series_id": f"clinicaltrials:{topic['slug']}:snapshot:total_studies",
        "date": snapshot_date,
        "value": float(len(studies)),
        "unit": "studies",
        "metric": "trial_current_total",
        "title": f"ClinicalTrials.gov — {topic['title']} current total studies",
        "topic": topic["title"],
        "term": topic["term"],
        "studies": _examples(studies),
    })
    for status, count in sorted(status_counts.items()):
        rows.append({
            "series_id": f"clinicaltrials:{topic['slug']}:snapshot:status:{status.lower()}",
            "date": snapshot_date,
            "value": float(count),
            "unit": "studies",
            "metric": "trial_current_status_count",
            "title": f"ClinicalTrials.gov — {topic['title']} current {status} studies",
            "topic": topic["title"],
            "term": topic["term"],
        })
    for phase, count in sorted(phase_counts.items()):
        rows.append({
            "series_id": f"clinicaltrials:{topic['slug']}:snapshot:phase:{phase.lower()}",
            "date": snapshot_date,
            "value": float(count),
            "unit": "studies",
            "metric": "trial_current_phase_count",
            "title": f"ClinicalTrials.gov — {topic['title']} current {phase} studies",
            "topic": topic["title"],
            "term": topic["term"],
        })
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    snapshot_date = _today()
    for topic in TOPICS:
        studies = fetch_topic(topic, log=log)
        rows = normalize(topic, studies, snapshot_date=snapshot_date)
        if rows:
            dates = sorted({r["date"] for r in rows})
            log(f"  + {topic['slug']:<26s} {len(studies):4d} studies  {dates[0]}–{dates[-1]}  {len(rows)} obs")
            all_rows.extend(rows)
        else:
            log(f"  - {topic['slug']:<26s} no dated studies")
        time.sleep(REQUEST_SPACING_S)

    existing = _existing_line_count()
    if not all_rows:
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(all_rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial refresh fetched {len(all_rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(all_rows)
    log(f"\nwrote {len(all_rows)} observations → {OUT_PATH}")
    return all_rows


if __name__ == "__main__":
    print("ClinicalTrials.gov therapeutic pipeline activity (keyless official API):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — ClinicalTrials.gov API unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
