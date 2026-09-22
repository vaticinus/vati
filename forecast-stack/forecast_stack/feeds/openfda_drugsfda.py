"""openFDA Drugs@FDA approval activity collector.

Official openFDA API over Drugs@FDA records. This is a bounded V1 regulatory-approval signal for
forecast-relevant therapeutics, not a full FDA mirror. It emits dated counts of approved submissions
and original approvals from real FDA `submission_status_date` fields, plus snapshot counts dated to
the API `last_updated` date.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
API_URL = "https://api.fda.gov/drug/drugsfda.json"
OUT_PATH = DATA_DIR / "feeds" / "openfda_drugsfda.jsonl"

REQUEST_TIMEOUT_S = 20
REQUEST_SPACING_S = 0.25
LIMIT = 100
MIN_REFRESH_FRACTION = 0.75

TOPICS: tuple[dict[str, Any], ...] = (
    {
        "slug": "glp1_obesity_drugs",
        "title": "GLP-1 obesity drugs",
        "terms": ("semaglutide", "tirzepatide", "liraglutide", "dulaglutide", "exenatide"),
    },
    {
        "slug": "crispr_gene_editing",
        "title": "CRISPR gene editing",
        "terms": ("exagamglogene autotemcel", "CASGEVY"),
    },
    {
        "slug": "gene_therapy",
        "title": "Gene therapy",
        "terms": ("voretigene", "onasemnogene", "betibeglogene", "etranacogene", "valoctocogene", "delandistrogene"),
    },
    {
        "slug": "mrna_therapeutics",
        "title": "mRNA therapeutics",
        "terms": ("SPIKEVAX", "COMIRNATY", "mRNA"),
    },
    {
        "slug": "radioligand_therapy",
        "title": "Radioligand therapy",
        "terms": ("PLUVICTO", "LUTATHERA", "XOFIGO", "lutetium", "radium ra 223"),
    },
    {
        "slug": "car_t_cell_therapy",
        "title": "CAR-T cell therapy",
        "terms": ("tisagenlecleucel", "axicabtagene", "idecabtagene", "lisocabtagene", "ciltacabtagene"),
    },
    {
        "slug": "alzheimers_disease",
        "title": "Alzheimer's disease",
        "terms": ("lecanemab", "donanemab", "aducanumab"),
    },
    {
        "slug": "antimicrobial_resistance",
        "title": "Antimicrobial resistance",
        "terms": ("cefiderocol", "ceftazidime avibactam", "meropenem vaborbactam", "imipenem relebactam", "omadacycline"),
    },
)


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


def _fetch_json(term: str) -> dict[str, Any] | None:
    url = f"{API_URL}?{urllib.parse.urlencode({'search': term, 'limit': str(LIMIT)})}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"meta": {"results": {"total": 0}}, "results": []}
        return None
    except Exception:  # noqa: BLE001 — keyless endpoint; skip rather than fabricate
        return None


def fetch_topic(topic: dict[str, Any], *, log=print) -> tuple[list[dict[str, Any]], str | None]:
    by_app: dict[str, dict[str, Any]] = {}
    last_updated: str | None = None
    for term in topic["terms"]:
        data = _fetch_json(str(term))
        if not isinstance(data, dict):
            log(f"  - {topic['slug']}: term {term!r} unreachable")
            continue
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        last_updated = str(meta.get("last_updated") or last_updated or "")
        for record in data.get("results") or []:
            if not isinstance(record, dict):
                continue
            app_no = str(record.get("application_number") or "")
            if not app_no:
                continue
            by_app.setdefault(app_no, record)
        time.sleep(REQUEST_SPACING_S)
    return list(by_app.values()), last_updated or None


def _date(raw: str | None) -> str | None:
    s = str(raw or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return None


def _product_names(record: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for p in record.get("products") or []:
        if not isinstance(p, dict):
            continue
        brand = p.get("brand_name")
        if brand:
            names.append(str(brand))
    return sorted(set(names))[:8]


def _submission_stub(record: dict[str, Any], sub: dict[str, Any]) -> dict[str, Any]:
    docs = sub.get("application_docs") if isinstance(sub.get("application_docs"), list) else []
    return {
        "application_number": record.get("application_number"),
        "products": _product_names(record),
        "submission_type": sub.get("submission_type"),
        "submission_number": sub.get("submission_number"),
        "submission_status": sub.get("submission_status"),
        "submission_status_date": _date(sub.get("submission_status_date")),
        "review_priority": sub.get("review_priority"),
        "submission_class_code": sub.get("submission_class_code"),
        "submission_class_code_description": sub.get("submission_class_code_description"),
        "docs": [
            {"type": d.get("type"), "date": _date(d.get("date")), "url": d.get("url")}
            for d in docs[:5]
            if isinstance(d, dict)
        ],
    }


def _examples(items: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    return items[:limit]


def normalize(topic: dict[str, Any], records: list[dict[str, Any]], *, snapshot_date: str | None) -> list[dict[str, Any]]:
    if not records:
        return []
    snapshot_date = snapshot_date or datetime.now(timezone.utc).date().isoformat()
    approved_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    original_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    class_counts = defaultdict(int)
    approved_submissions = 0
    for record in records:
        for sub in record.get("submissions") or []:
            if not isinstance(sub, dict):
                continue
            if str(sub.get("submission_status") or "").upper() != "AP":
                continue
            date = _date(sub.get("submission_status_date"))
            if date is None:
                continue
            stub = _submission_stub(record, sub)
            approved_by_date[date].append(stub)
            approved_submissions += 1
            class_counts[str(sub.get("submission_class_code") or "UNKNOWN").upper().replace(" ", "_")] += 1
            if str(sub.get("submission_type") or "").upper() == "ORIG":
                original_by_date[date].append(stub)

    rows: list[dict[str, Any]] = []
    for date, items in sorted(approved_by_date.items()):
        rows.append({
            "series_id": f"openfda_drugsfda:{topic['slug']}:approved_submissions",
            "date": date,
            "value": float(len(items)),
            "unit": "submissions",
            "metric": "fda_approved_submissions",
            "title": f"openFDA Drugs@FDA — {topic['title']} approved submissions",
            "topic": topic["title"],
            "examples": _examples(items),
        })
    for date, items in sorted(original_by_date.items()):
        rows.append({
            "series_id": f"openfda_drugsfda:{topic['slug']}:original_approvals",
            "date": date,
            "value": float(len(items)),
            "unit": "submissions",
            "metric": "fda_original_approvals",
            "title": f"openFDA Drugs@FDA — {topic['title']} original approvals",
            "topic": topic["title"],
            "examples": _examples(items),
        })
    rows.append({
        "series_id": f"openfda_drugsfda:{topic['slug']}:snapshot:applications",
        "date": snapshot_date,
        "value": float(len(records)),
        "unit": "applications",
        "metric": "fda_current_applications",
        "title": f"openFDA Drugs@FDA — {topic['title']} current applications",
        "topic": topic["title"],
        "last_updated": snapshot_date,
        "examples": _examples([
            {"application_number": r.get("application_number"), "products": _product_names(r)}
            for r in records
        ]),
    })
    rows.append({
        "series_id": f"openfda_drugsfda:{topic['slug']}:snapshot:approved_submissions",
        "date": snapshot_date,
        "value": float(approved_submissions),
        "unit": "submissions",
        "metric": "fda_current_approved_submissions",
        "title": f"openFDA Drugs@FDA — {topic['title']} current approved submissions",
        "topic": topic["title"],
        "last_updated": snapshot_date,
    })
    for cls, count in sorted(class_counts.items()):
        rows.append({
            "series_id": f"openfda_drugsfda:{topic['slug']}:snapshot:class:{cls.lower()}",
            "date": snapshot_date,
            "value": float(count),
            "unit": "submissions",
            "metric": "fda_current_approved_submission_class",
            "title": f"openFDA Drugs@FDA — {topic['title']} current {cls} approved submissions",
            "topic": topic["title"],
            "last_updated": snapshot_date,
        })
    return sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))


def collect(*, log=print) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    for topic in TOPICS:
        records, last_updated = fetch_topic(topic, log=log)
        rows = normalize(topic, records, snapshot_date=last_updated)
        if rows:
            dates = sorted({r["date"] for r in rows})
            log(f"  + {topic['slug']:<26s} {len(records):3d} apps  {dates[0]}–{dates[-1]}  {len(rows)} obs")
            all_rows.extend(rows)
        else:
            log(f"  - {topic['slug']:<26s} no FDA records")
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
    print("openFDA Drugs@FDA approval activity (keyless official API):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — openFDA Drugs@FDA API unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
