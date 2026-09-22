"""OFAC sanctions-activity collector (time-series + dated snapshot).

Two complementary keyless feeds:

1. A REAL ANNUAL TIME-SERIES of OFAC sanctions activity, per program theme. The SDN.XML itself carries
   only a single list-wide Publish_Date and no reliable per-entry add-date, so it cannot yield a time
   series. We instead count OFAC (Office of Foreign Assets Control) actions published in the Federal
   Register per calendar year — each such notice carries a real publication date — filtered by program
   theme (Russia, Iran, China/tech, counter-narcotics/fentanyl, counter-terrorism, etc.). This is a true
   count-over-time that a changepoint detector can fire on. Annual bins stop at the last COMPLETE year
   (leak/partial-safe); dates are the publication year, never fetched_at.

2. A DATED SNAPSHOT of the current SDN list: per-program / per-type / per-country current entry counts,
   all dated to the OFAC list Publish_Date in the XML. This is explicitly a single dated snapshot, NOT a
   fabricated time series (series_id suffix `:snapshot`), because the source has no per-entry history.

If a source is unreachable we skip it and preserve the cache rather than fabricate.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
SDN_XML_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.XML"
FR_BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"
OFAC_AGENCY_SLUG = "foreign-assets-control-office"
OUT_PATH = DATA_DIR / "feeds" / "ofac_sdn.jsonl"
REQUEST_TIMEOUT_S = 60
FR_TIMEOUT_S = 20
FR_SPACING_S = 0.20
MIN_REFRESH_FRACTION = 0.75

# Real annual time-series window (Federal Register OFAC notices).
FR_START_YEAR = 2010

# Program themes — Federal Register full-text terms within the OFAC agency. "" = all OFAC activity.
SANCTIONS_PROGRAMS: tuple[dict[str, str], ...] = (
    {"slug": "all", "term": "", "title": "All OFAC sanctions activity"},
    {"slug": "russia", "term": "Russia OR Russian", "title": "Russia"},
    {"slug": "iran", "term": "Iran OR Iranian", "title": "Iran"},
    {"slug": "china_tech", "term": "China OR PRC OR Chinese", "title": "China / tech"},
    {"slug": "north_korea", "term": '"North Korea" OR DPRK', "title": "North Korea"},
    {"slug": "venezuela", "term": "Venezuela OR Venezuelan", "title": "Venezuela"},
    {"slug": "counter_narcotics", "term": '"narcotics" OR fentanyl OR "drug trafficking" OR cartel', "title": "Counter-narcotics"},
    {"slug": "counter_terrorism", "term": '"terrorism" OR terrorist OR "counterterrorism"', "title": "Counter-terrorism"},
    {"slug": "cyber", "term": "cyber OR ransomware", "title": "Cyber"},
    {"slug": "human_rights", "term": '"human rights" OR Magnitsky', "title": "Human rights / Magnitsky"},
)


def _cutoff_year() -> int:
    return datetime.now(timezone.utc).date().year


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


# ---------------------------------------------------------------------------
# (1) Real annual time-series: Federal Register OFAC notices per program-year.
# ---------------------------------------------------------------------------

def _fr_count(term: str, gte: str, lte: str) -> int | None:
    params = [
        ("conditions[publication_date][gte]", gte),
        ("conditions[publication_date][lte]", lte),
        ("conditions[agencies][]", OFAC_AGENCY_SLUG),
        ("per_page", "1"),
        ("page", "1"),
        ("fields[]", "document_number"),
    ]
    if term:
        params.append(("conditions[term]", term))
    url = f"{FR_BASE_URL}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=FR_TIMEOUT_S) as resp:  # noqa: S310 official API
            data = json.loads(resp.read().decode("utf-8", "replace"))
        count = data.get("count")
        return int(count) if isinstance(count, (int, float)) else None
    except Exception:  # noqa: BLE001 — keyless public endpoint; skip rather than fabricate
        return None


def fetch_timeseries(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    last_full_year = _cutoff_year() - 1  # drop partial current year → leak/partial-safe
    for prog in SANCTIONS_PROGRAMS:
        n = 0
        for year in range(FR_START_YEAR, last_full_year + 1):
            c = _fr_count(prog["term"], f"{year}-01-01", f"{year}-12-31")
            time.sleep(FR_SPACING_S)
            if c is None:
                log(f"    ! sanctions:{prog['slug']} {year}: FR unreachable, skip")
                continue
            if c <= 0:
                continue
            n += 1
            rows.append({
                "series_id": f"ofac_sanctions:program:{prog['slug']}:per_year",
                "date": f"{year}-12-31",
                "value": float(c),
                "unit": "notices/yr",
                "metric": "sanctions_entries_per_year",
                "domain": "sanctions",
                "title": f"OFAC sanctions notices per year — {prog['title']}",
                "program": prog["title"],
                "source": "federal_register_ofac_notices",
            })
        log(f"  + sanctions:{prog['slug']:<18s} {n} annual yrs")
    return rows


# ---------------------------------------------------------------------------
# (2) Dated snapshot: current SDN list per-program/type/country counts.
# ---------------------------------------------------------------------------

def _fetch_bytes(url: str = SDN_XML_URL) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official API
            return resp.read()
    except Exception:  # noqa: BLE001 — keyless public endpoint; preserve cache on failure
        return None


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(node: ET.Element, name: str) -> str:
    if _strip_ns(node.tag) == name:
        return (node.text or "").strip()
    for child in node:
        if _strip_ns(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _iter_children(node: ET.Element, name: str):
    for child in node:
        if _strip_ns(child.tag) == name:
            yield child


def _parse_publish_date(raw: str) -> str:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unparseable OFAC Publish_Date: {raw!r}")


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "unknown"


def _entry_name(entry: ET.Element) -> str:
    parts = [_child_text(entry, "firstName"), _child_text(entry, "lastName")]
    name = " ".join(p for p in parts if p).strip()
    return name or _child_text(entry, "uid") or "unknown"


def parse_xml(raw: bytes) -> tuple[str, int, list[dict[str, Any]]]:
    root = ET.fromstring(raw)
    info = next(_iter_children(root, "publshInformation"), None)
    if info is None:
        raise ValueError("OFAC XML missing publshInformation")
    publish_date = _parse_publish_date(_child_text(info, "Publish_Date"))
    try:
        record_count = int(_child_text(info, "Record_Count"))
    except ValueError:
        record_count = 0

    entries: list[dict[str, Any]] = []
    for entry in _iter_children(root, "sdnEntry"):
        programs: list[str] = []
        for plist in _iter_children(entry, "programList"):
            programs.extend(_child_text(p, "program") for p in _iter_children(plist, "program"))
        countries: list[str] = []
        for alist in _iter_children(entry, "addressList"):
            for address in _iter_children(alist, "address"):
                country = _child_text(address, "country")
                if country:
                    countries.append(country)
        entries.append({
            "uid": _child_text(entry, "uid"),
            "name": _entry_name(entry),
            "type": _child_text(entry, "sdnType") or "Unknown",
            "programs": sorted({p for p in programs if p}),
            "countries": sorted({c for c in countries if c}),
        })
    return publish_date, record_count, entries


def _examples(entries: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "uid": e["uid"],
            "name": e["name"],
            "type": e["type"],
            "programs": e["programs"][:5],
            "countries": e["countries"][:5],
        }
        for e in entries[:limit]
    ]


def normalize_snapshot(publish_date: str, record_count: int, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dated SNAPSHOT (not a time series): all rows carry the single list Publish_Date."""
    by_type = Counter(e["type"] for e in entries)
    by_program: Counter[str] = Counter()
    by_country: Counter[str] = Counter()
    examples_by_program: dict[str, list[dict[str, Any]]] = {}
    examples_by_country: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        for program in entry["programs"] or ["Unknown"]:
            by_program[program] += 1
            examples_by_program.setdefault(program, []).append(entry)
        for country in entry["countries"] or ["Unknown"]:
            by_country[country] += 1
            examples_by_country.setdefault(country, []).append(entry)

    total = record_count or len(entries)
    rows: list[dict[str, Any]] = [{
        "series_id": "ofac_sdn:total:snapshot",
        "date": publish_date,
        "value": float(total),
        "unit": "entries",
        "metric": "sanctions_entries_snapshot",
        "domain": "sanctions",
        "title": "OFAC SDN — total entries (dated snapshot)",
        "snapshot": True,
        "publish_date": publish_date,
        "record_count": record_count,
        "examples": _examples(entries),
    }]

    for typ, count in sorted(by_type.items()):
        rows.append({
            "series_id": f"ofac_sdn:type:{_slug(typ)}:snapshot",
            "date": publish_date,
            "value": float(count),
            "unit": "entries",
            "metric": "sanctions_entries_by_type_snapshot",
            "domain": "sanctions",
            "title": f"OFAC SDN — type {typ} (dated snapshot)",
            "snapshot": True,
            "publish_date": publish_date,
        })
    for program, count in sorted(by_program.items()):
        rows.append({
            "series_id": f"ofac_sdn:program:{_slug(program)}:snapshot",
            "date": publish_date,
            "value": float(count),
            "unit": "entries",
            "metric": "sanctions_entries_by_program_snapshot",
            "domain": "sanctions",
            "title": f"OFAC SDN — program {program} (dated snapshot)",
            "snapshot": True,
            "publish_date": publish_date,
            "examples": _examples(examples_by_program.get(program, [])),
        })
    for country, count in sorted(by_country.items()):
        rows.append({
            "series_id": f"ofac_sdn:country:{_slug(country)}:snapshot",
            "date": publish_date,
            "value": float(count),
            "unit": "entries",
            "metric": "sanctions_entries_by_country_snapshot",
            "domain": "sanctions",
            "title": f"OFAC SDN — target country {country} (dated snapshot)",
            "snapshot": True,
            "publish_date": publish_date,
            "examples": _examples(examples_by_country.get(country, [])),
        })
    return sorted(rows, key=lambda r: str(r["series_id"]))


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # (1) Real annual sanctions-activity time series (Federal Register OFAC notices).
    ts_rows = fetch_timeseries(log=log)
    rows.extend(ts_rows)

    # (2) Dated SDN snapshot (current list state).
    raw = _fetch_bytes()
    if not raw:
        log("no OFAC SDN XML fetched; snapshot rows skipped this run")
    else:
        try:
            publish_date, record_count, entries = parse_xml(raw)
            snap_rows = normalize_snapshot(publish_date, record_count, entries)
            rows.extend(snap_rows)
            log(
                f"  + snapshot publish_date={publish_date} record_count={record_count} "
                f"entries={len(entries)} rows={len(snap_rows)}"
            )
        except (ET.ParseError, ValueError) as exc:
            log(f"could not parse OFAC SDN XML ({exc}); snapshot rows skipped this run")

    existing = _existing_line_count()
    if not rows:
        log(f"\nno observations fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial refresh fetched {len(rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    rows.sort(key=lambda r: (str(r["series_id"]), str(r["date"])))
    _write_jsonl_atomic(rows)
    series = len({r["series_id"] for r in rows})
    log(f"\nwrote {len(rows)} observations across {series} series → {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("OFAC sanctions activity (annual time-series + dated SDN snapshot, keyless):")
    observations = collect()
    if not observations:
        print("\nNO observations collected — sources unreachable/empty this run.")
    else:
        ts = [o for o in observations if not o.get("snapshot")]
        snap = [o for o in observations if o.get("snapshot")]
        print(f"\ntime-series obs: {len(ts)}  |  dated-snapshot obs: {len(snap)}")
        print("first 5 observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
