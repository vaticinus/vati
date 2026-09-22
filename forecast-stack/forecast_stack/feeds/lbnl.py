"""LBNL Queued Up interconnection-queue capacity feed.

Tiny grounded feed for the existing provider="lbnl" queue-capacity series. The
values are the same conservative headline totals used by the DB-direct pillar
collector, emitted through the generic feed/rawstore path for exact provenance.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

OUT_PATH = DATA_DIR / "feeds" / "lbnl.jsonl"

QUEUE: tuple[dict[str, Any], ...] = (
    {
        "year": 2021,
        "gw": 1430.0,
        "source_url": "https://eta-publications.lbl.gov/sites/default/files/queued_up_2021_04-13-2022.pdf",
        "note": "Queued Up end-2021: about 1,000 GW generation plus about 427 GW storage active in queues.",
    },
    {
        "year": 2022,
        "gw": 2040.0,
        "source_url": "https://emp.lbl.gov/sites/default/files/queued_up_2022_04-06-2023.pdf",
        "note": "Queued Up end-2022: active capacity in queues around 2,040 GW.",
    },
    {
        "year": 2023,
        "gw": 2600.0,
        "source_url": "https://emp.lbl.gov/news/grid-connection-backlog-grows-30-2023-dominated-requests-solar-wind-and-energy-storage",
        "note": "LBNL end-2023 release: nearly 2,600 GW of generation and storage actively seeking interconnection.",
    },
)


def normalize_queue(rows: tuple[dict[str, Any], ...] = QUEUE) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        year = int(row["year"])
        out.append({
            "series_id": "queued_up_active_capacity",
            "date": date(year, 12, 31).isoformat(),
            "value": float(row["gw"]),
            "unit": "GW (active)",
            "metric": "interconnection_queue_capacity",
            "domain": "energy/grid",
            "title": "US interconnection-queue active capacity",
            "published_at": date(year + 1, 4, 30).isoformat(),
            "source_url": row["source_url"],
            "note": row["note"],
        })
    return out


def collect(*, log=print) -> list[dict[str, Any]]:
    rows = normalize_queue()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)
    log(f"wrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("LBNL Queued Up active interconnection capacity:")
    collect()
