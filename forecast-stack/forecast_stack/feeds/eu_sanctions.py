"""EU consolidated financial sanctions list collector.

Official EU FSD XML export, keyless with the public static token used by the EU download page. This
is a current sanctions snapshot, not historical change tracking. Observations are dated to the XML
`generationDate` and aggregate entries by sanctions programme, subject type, and target country.
"""

from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
EU_XML_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList/content?token=dG9rZW4tMjAxNw"
OUT_PATH = DATA_DIR / "feeds" / "eu_sanctions.jsonl"
REQUEST_TIMEOUT_S = 60
MIN_REFRESH_FRACTION = 0.75


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


def _fetch_bytes(url: str = EU_XML_URL) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official EU export
            return resp.read()
    except Exception:  # noqa: BLE001 — preserve cache on endpoint failure
        return None


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _iter_children(node: ET.Element, name: str):
    for child in node:
        if _strip_ns(child.tag) == name:
            yield child


def _first_child(node: ET.Element, name: str) -> ET.Element | None:
    return next(_iter_children(node, name), None)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "unknown"


def _date_from_generation(raw: str) -> str:
    # Example: 2026-06-05T15:51:25.849+02:00
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()


def _entry_name(entry: ET.Element) -> str:
    for alias in _iter_children(entry, "nameAlias"):
        if (alias.attrib.get("strong") or "").lower() == "true" and alias.attrib.get("wholeName"):
            return alias.attrib["wholeName"]
    alias = _first_child(entry, "nameAlias")
    return (alias.attrib.get("wholeName") if alias is not None else None) or entry.attrib.get("logicalId", "unknown")


def parse_xml(raw: bytes) -> tuple[str, str, list[dict[str, Any]]]:
    root = ET.fromstring(raw)
    generation = root.attrib.get("generationDate") or ""
    generation_date = _date_from_generation(generation)
    global_file_id = root.attrib.get("globalFileId") or ""
    entries: list[dict[str, Any]] = []
    for entity in _iter_children(root, "sanctionEntity"):
        programmes = sorted({
            r.attrib.get("programme", "").strip()
            for r in _iter_children(entity, "regulation")
            if r.attrib.get("programme", "").strip()
        })
        subject = _first_child(entity, "subjectType")
        subject_type = subject.attrib.get("code", "unknown") if subject is not None else "unknown"
        countries: set[str] = set()
        for tag in ("citizenship", "address", "birthdate"):
            for child in _iter_children(entity, tag):
                country = child.attrib.get("countryDescription", "").strip()
                if country and country.upper() not in {"UNKNOWN", "00"}:
                    countries.add(country.title())
        entries.append({
            "logical_id": entity.attrib.get("logicalId"),
            "name": _entry_name(entity),
            "programmes": programmes or ["Unknown"],
            "subject_type": subject_type,
            "countries": sorted(countries),
            "united_nation_id": entity.attrib.get("unitedNationId") or "",
        })
    return generation_date, global_file_id, entries


def _examples(entries: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "logical_id": e["logical_id"],
            "name": e["name"],
            "programmes": e["programmes"][:5],
            "subject_type": e["subject_type"],
            "countries": e["countries"][:5],
            "united_nation_id": e["united_nation_id"],
        }
        for e in entries[:limit]
    ]


def normalize(generation_date: str, global_file_id: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_program: Counter[str] = Counter()
    by_subject: Counter[str] = Counter()
    by_country: Counter[str] = Counter()
    examples_by_program: dict[str, list[dict[str, Any]]] = {}
    examples_by_subject: dict[str, list[dict[str, Any]]] = {}
    examples_by_country: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_subject[entry["subject_type"]] += 1
        examples_by_subject.setdefault(entry["subject_type"], []).append(entry)
        for programme in entry["programmes"]:
            by_program[programme] += 1
            examples_by_program.setdefault(programme, []).append(entry)
        for country in entry["countries"] or ["Unknown"]:
            by_country[country] += 1
            examples_by_country.setdefault(country, []).append(entry)

    rows: list[dict[str, Any]] = [{
        "series_id": "eu_sanctions:total:entries",
        "date": generation_date,
        "value": float(len(entries)),
        "unit": "entries",
        "metric": "sanctions_entries",
        "title": "EU sanctions — total entries",
        "generation_date": generation_date,
        "global_file_id": global_file_id,
        "examples": _examples(entries),
    }]
    for programme, count in sorted(by_program.items()):
        rows.append({
            "series_id": f"eu_sanctions:programme:{_slug(programme)}",
            "date": generation_date,
            "value": float(count),
            "unit": "entries",
            "metric": "sanctions_entries_by_programme",
            "title": f"EU sanctions — programme {programme}",
            "generation_date": generation_date,
            "global_file_id": global_file_id,
            "examples": _examples(examples_by_program.get(programme, [])),
        })
    for subject, count in sorted(by_subject.items()):
        rows.append({
            "series_id": f"eu_sanctions:subject_type:{_slug(subject)}",
            "date": generation_date,
            "value": float(count),
            "unit": "entries",
            "metric": "sanctions_entries_by_subject_type",
            "title": f"EU sanctions — subject type {subject}",
            "generation_date": generation_date,
            "global_file_id": global_file_id,
            "examples": _examples(examples_by_subject.get(subject, [])),
        })
    for country, count in sorted(by_country.items()):
        rows.append({
            "series_id": f"eu_sanctions:country:{_slug(country)}",
            "date": generation_date,
            "value": float(count),
            "unit": "entries",
            "metric": "sanctions_entries_by_country",
            "title": f"EU sanctions — target country {country}",
            "generation_date": generation_date,
            "global_file_id": global_file_id,
            "examples": _examples(examples_by_country.get(country, [])),
        })
    return sorted(rows, key=lambda r: str(r["series_id"]))


def collect(*, log=print) -> list[dict[str, Any]]:
    raw = _fetch_bytes()
    existing = _existing_line_count()
    if not raw:
        log(f"no EU sanctions XML fetched; preserved existing {existing} rows at {OUT_PATH}")
        return []
    try:
        generation_date, global_file_id, entries = parse_xml(raw)
    except (ET.ParseError, ValueError) as exc:
        log(f"could not parse EU sanctions XML ({exc}); preserved existing {existing} rows at {OUT_PATH}")
        return []
    rows = normalize(generation_date, global_file_id, entries)
    if existing and len(rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"partial refresh fetched {len(rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(rows)
    log(
        f"EU sanctions generation_date={generation_date} global_file_id={global_file_id} "
        f"entries_parsed={len(entries)} observations={len(rows)} → {OUT_PATH}"
    )
    return rows


if __name__ == "__main__":
    observations = collect()
    if not observations:
        print("NO observations collected — EU sanctions XML unreachable/empty this run.")
    else:
        print(f"first {min(5, len(observations))} observations:")
        for o in observations[:5]:
            print("  " + json.dumps({k: o[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"jsonl line count: {_existing_line_count()}")
