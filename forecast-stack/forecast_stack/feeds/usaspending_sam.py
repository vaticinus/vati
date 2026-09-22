"""USAspending/SAM procurement topic-year collector.

Small V1 feed for procurement pressure: aggregate USAspending prime-award counts and obligations
by forecast-relevant topic and fiscal year. This deliberately avoids downloading award rows; it uses
the official no-auth aggregate endpoint and writes a compact JSONL suitable for world-state facts.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
SUMMARY_URL = "https://api.usaspending.gov/api/v2/search/transaction_spending_summary/"
OUT_PATH = DATA_DIR / "feeds" / "usaspending_sam.jsonl"
REQUEST_TIMEOUT_S = float(os.environ.get("USASPENDING_TIMEOUT_S", "12"))
REQUEST_SPACING_S = float(os.environ.get("USASPENDING_SPACING_S", "0.15"))
MAX_TOPIC_LIMIT = int(os.environ.get("USASPENDING_TOPIC_LIMIT", "0"))
TOPIC_OFFSET = int(os.environ.get("USASPENDING_TOPIC_OFFSET", "0"))
START_FY = int(os.environ.get("USASPENDING_START_FY", "2018"))
END_FY = int(os.environ.get("USASPENDING_END_FY", "0") or "0")


@dataclass(frozen=True)
class Topic:
    slug: str
    keywords: tuple[str, ...]
    title: str


TOPICS: tuple[Topic, ...] = (
    # --- AI / compute ---
    Topic("artificial_intelligence", ("artificial intelligence", "machine learning"), "Artificial intelligence"),
    Topic("generative_ai", ("generative ai", "large language model", "foundation model"), "Generative AI and foundation models"),
    Topic("autonomy_robotics", ("autonomous systems", "robotics", "autonomy"), "Autonomy and robotics"),
    Topic("edge_computing", ("edge computing", "edge ai", "embedded ai"), "Edge computing"),
    Topic("high_performance_computing", ("high performance computing", "supercomputing", "exascale"), "High-performance computing"),
    Topic("data_center", ("data center", "datacenter", "hyperscale"), "Data centers"),
    # --- Semiconductors ---
    Topic("semiconductors", ("semiconductor", "microelectronics", "integrated circuit"), "Semiconductors and microelectronics"),
    Topic("advanced_packaging", ("advanced packaging", "chip packaging", "heterogeneous integration"), "Advanced semiconductor packaging"),
    Topic("chip_fabrication", ("semiconductor fabrication", "wafer fabrication", "lithography"), "Semiconductor fabrication"),
    Topic("gan_sic_power", ("gallium nitride", "silicon carbide", "wide bandgap"), "Wide-bandgap power semiconductors"),
    Topic("photonics", ("photonics", "integrated photonics", "silicon photonics"), "Photonics"),
    Topic("rf_microwave", ("radio frequency", "microwave electronics", "millimeter wave"), "RF and microwave electronics"),
    # --- Energy / power ---
    Topic("power_grid", ("power transformer", "grid modernization", "transmission"), "Power grid equipment"),
    Topic("grid_transmission", ("high voltage transmission", "grid interconnection", "transmission line"), "Grid transmission"),
    Topic("battery_storage", ("battery storage", "lithium battery", "solid state battery"), "Battery storage"),
    Topic("grid_scale_storage", ("grid scale storage", "long duration storage", "flow battery"), "Grid-scale energy storage"),
    Topic("solar_power", ("solar power", "photovoltaic", "solar panel"), "Solar power"),
    Topic("wind_power", ("wind power", "wind turbine", "offshore wind"), "Wind power"),
    Topic("geothermal", ("geothermal energy", "geothermal power", "enhanced geothermal"), "Geothermal energy"),
    Topic("hydrogen", ("hydrogen", "electrolyzer", "fuel cell"), "Hydrogen and fuel cells"),
    Topic("fusion_energy", ("fusion energy", "nuclear fusion", "tokamak"), "Fusion energy"),
    Topic("uranium_nuclear", ("uranium enrichment", "nuclear fuel", "small modular reactor"), "Uranium and nuclear fuel"),
    Topic("smr_advanced_reactor", ("small modular reactor", "advanced reactor", "microreactor"), "Advanced and small modular reactors"),
    Topic("carbon_capture", ("carbon capture", "direct air capture", "carbon sequestration"), "Carbon capture and storage"),
    Topic("nuclear_weapons_stockpile", ("nuclear weapons", "stockpile stewardship", "weapons modernization"), "Nuclear stockpile stewardship"),
    # --- Critical materials ---
    Topic("critical_minerals", ("critical minerals", "rare earth", "lithium", "cobalt"), "Critical minerals"),
    Topic("rare_earth_magnets", ("rare earth magnet", "permanent magnet", "neodymium"), "Rare-earth permanent magnets"),
    Topic("lithium_supply", ("lithium mining", "lithium processing", "lithium refining"), "Lithium supply chain"),
    Topic("graphite_anode", ("graphite", "anode material", "synthetic graphite"), "Graphite and anode materials"),
    Topic("gallium_germanium", ("gallium", "germanium", "indium"), "Gallium, germanium, indium"),
    Topic("titanium_specialty_alloy", ("titanium", "specialty alloy", "superalloy"), "Titanium and specialty alloys"),
    Topic("strategic_materials_stockpile", ("strategic materials", "national defense stockpile", "material reserve"), "Strategic materials stockpile"),
    # --- Defense / weapons ---
    Topic("hypersonics", ("hypersonic", "scramjet"), "Hypersonics"),
    Topic("directed_energy", ("directed energy", "high energy laser", "laser weapon"), "Directed-energy weapons"),
    Topic("munitions", ("munitions", "precision guided", "missile production"), "Munitions and precision strike"),
    Topic("missile_defense", ("missile defense", "interceptor", "ballistic missile defense"), "Missile defense"),
    Topic("counter_uas", ("counter-uas", "counter drone", "counter unmanned"), "Counter-UAS"),
    Topic("electronic_warfare", ("electronic warfare", "electronic attack", "signals intelligence"), "Electronic warfare"),
    Topic("drones_uav", ("drone", "unmanned aerial", "UAV"), "Drones and unmanned aerial systems"),
    Topic("autonomous_ground_vehicles", ("autonomous ground vehicle", "unmanned ground vehicle", "robotic combat vehicle"), "Autonomous ground vehicles"),
    Topic("unmanned_maritime", ("unmanned surface vessel", "unmanned underwater", "autonomous maritime"), "Unmanned maritime systems"),
    Topic("shipbuilding", ("shipbuilding", "naval vessel", "submarine construction"), "Naval shipbuilding"),
    Topic("armored_vehicles", ("combat vehicle", "armored vehicle", "main battle tank"), "Armored and combat vehicles"),
    Topic("soldier_systems", ("soldier system", "night vision", "body armor"), "Soldier systems"),
    # --- Space ---
    Topic("space_launch", ("space launch", "launch vehicle", "rocket"), "Space launch"),
    Topic("satellites", ("satellite", "smallsat", "constellation"), "Satellites"),
    Topic("space_situational_awareness", ("space situational awareness", "space domain awareness", "orbital tracking"), "Space situational awareness"),
    Topic("satellite_communications", ("satellite communication", "satcom", "space communication"), "Satellite communications"),
    Topic("space_propulsion", ("space propulsion", "electric propulsion", "nuclear propulsion"), "Space propulsion"),
    # --- Cyber / comms ---
    Topic("cybersecurity", ("cybersecurity", "zero trust", "cyber security"), "Cybersecurity"),
    Topic("zero_trust", ("zero trust architecture", "identity management", "secure access"), "Zero-trust architecture"),
    Topic("cryptography", ("cryptography", "encryption", "key management"), "Cryptography"),
    Topic("post_quantum_crypto", ("post-quantum cryptography", "quantum resistant", "pqc"), "Post-quantum cryptography"),
    Topic("five_g_six_g", ("fifth generation wireless", "next generation wireless", "open ran"), "5G/6G networks"),
    Topic("resilient_pnt", ("positioning navigation timing", "gps resilience", "assured pnt"), "Resilient PNT"),
    # --- Quantum / sensing ---
    Topic("quantum", ("quantum computing", "quantum information", "quantum sensor"), "Quantum technologies"),
    Topic("quantum_sensing", ("quantum sensing", "atomic clock", "quantum magnetometer"), "Quantum sensing"),
    Topic("quantum_networking", ("quantum networking", "quantum communication", "quantum key distribution"), "Quantum networking"),
    # --- Bio / health ---
    Topic("biomanufacturing", ("biomanufacturing", "synthetic biology", "biofoundry"), "Biomanufacturing and synthetic biology"),
    Topic("biodefense", ("biodefense", "biological defense", "medical countermeasure"), "Biodefense"),
    Topic("vaccines", ("vaccine", "vaccine manufacturing", "mrna"), "Vaccines"),
    Topic("pandemic_preparedness", ("pandemic preparedness", "biosurveillance", "outbreak response"), "Pandemic preparedness"),
    Topic("genomics", ("genomics", "gene sequencing", "gene editing"), "Genomics and gene editing"),
    Topic("medical_devices", ("medical device", "diagnostic device", "point of care"), "Medical devices and diagnostics"),
    # --- Manufacturing / materials ---
    Topic("additive_manufacturing", ("additive manufacturing", "3d printing", "metal printing"), "Additive manufacturing"),
    Topic("advanced_materials", ("advanced materials", "composite material", "nanomaterial"), "Advanced materials"),
    Topic("microelectronics_packaging", ("microelectronics packaging", "substrate", "interposer"), "Microelectronics packaging"),
    Topic("precision_optics", ("precision optics", "optical coating", "infrared optics"), "Precision optics"),
    Topic("sensors", ("sensor", "infrared sensor", "lidar"), "Sensors"),
    # --- Infrastructure / climate ---
    Topic("water_infrastructure", ("water infrastructure", "desalination", "water treatment"), "Water infrastructure"),
    Topic("ev_charging", ("electric vehicle charging", "ev charger", "charging infrastructure"), "EV charging infrastructure"),
    Topic("port_logistics", ("port infrastructure", "port automation", "cargo handling"), "Port and logistics infrastructure"),
    Topic("wildfire_disaster", ("wildfire", "disaster response", "emergency management"), "Wildfire and disaster response"),
    # --- Frontier compute infra ---
    Topic("cloud_infrastructure", ("cloud computing", "cloud infrastructure", "cloud migration"), "Cloud infrastructure"),
    Topic("ai_chips_accelerators", ("ai accelerator", "gpu", "neural processor"), "AI chips and accelerators"),
)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _current_fiscal_year(today: date | None = None) -> int:
    today = today or _today()
    return today.year + 1 if today.month >= 10 else today.year


def fiscal_period(fy: int, *, today: date | None = None) -> tuple[date, date, bool]:
    today = today or _today()
    start = date(fy - 1, 10, 1)
    end = date(fy, 9, 30)
    complete = end <= today
    return start, min(end, today), complete


def selected_topics(limit: int = MAX_TOPIC_LIMIT, offset: int = TOPIC_OFFSET) -> tuple[Topic, ...]:
    topics = TOPICS[max(0, offset):]
    return topics if limit <= 0 else topics[:limit]


def _request_summary(topic: Topic, fy: int, *, today: date | None = None) -> bytes:
    start, end, _complete = fiscal_period(fy, today=today)
    payload = {
        "filters": {
            "keywords": list(topic.keywords),
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
        }
    }
    req = urllib.request.Request(
        SUMMARY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official public API
        return resp.read()


def _finite_number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def normalize_summary(topic: Topic, fy: int, raw: bytes | str | dict[str, Any], *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or _today()
    if isinstance(raw, bytes):
        data = json.loads(raw.decode("utf-8", "replace"))
    elif isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    results = data.get("results") if isinstance(data.get("results"), dict) else {}
    count = _finite_number(results.get("prime_awards_count"))
    obligation = _finite_number(results.get("prime_awards_obligation_amount"))
    start, end, complete = fiscal_period(fy, today=today)
    rows: list[dict[str, Any]] = []
    base = {
        "date": end.isoformat(),
        "fiscal_year": fy,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "period_complete": complete,
        "topic": topic.slug,
        "keywords": list(topic.keywords),
    }
    if count is not None:
        rows.append(
            {
                **base,
                "series_id": f"usaspending_sam:{topic.slug}:prime_awards_count",
                "value": count,
                "unit": "awards",
                "metric": "prime_awards_count",
                "title": f"USAspending - {topic.title} prime awards",
            }
        )
    if obligation is not None:
        rows.append(
            {
                **base,
                "series_id": f"usaspending_sam:{topic.slug}:prime_awards_obligation_amount",
                "value": obligation,
                "unit": "USD",
                "metric": "prime_awards_obligation_amount",
                "title": f"USAspending - {topic.title} prime award obligations",
            }
        )
    return rows


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
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _merge_rows(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in [*old, *new]:
        series_id = str(row.get("series_id") or "")
        day = str(row.get("date") or "")
        if series_id and day:
            by_key[(series_id, day)] = row
    return [by_key[k] for k in sorted(by_key)]


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as f:
        return sum(1 for _ in f)


def collect(
    *,
    log=print,
    start_fy: int = START_FY,
    end_fy: int | None = None,
    topic_limit: int = MAX_TOPIC_LIMIT,
    topic_offset: int = TOPIC_OFFSET,
) -> list[dict[str, Any]]:
    today = _today()
    end_fy = end_fy or END_FY or _current_fiscal_year(today)
    rows: list[dict[str, Any]] = _read_existing_rows()
    new_rows: list[dict[str, Any]] = []
    for topic in selected_topics(topic_limit, topic_offset):
        topic_rows = 0
        topic_new: list[dict[str, Any]] = []
        for fy in range(start_fy, end_fy + 1):
            try:
                raw = _request_summary(topic, fy, today=today)
                parsed = normalize_summary(topic, fy, raw, today=today)
            except Exception as exc:  # noqa: BLE001 - public endpoint; preserve existing on partial failure
                log(f"  - {topic.slug} FY{fy}: fetch failed: {exc}")
                parsed = []
            topic_new.extend(parsed)
            topic_rows += len(parsed)
            time.sleep(REQUEST_SPACING_S)
        if topic_new:
            new_rows.extend(topic_new)
            rows = _merge_rows(rows, topic_new)
            _write_jsonl_atomic(rows)
        log(f"  + {topic.slug:<24s} {topic_rows:4d} observations FY{start_fy}-FY{end_fy}")

    if not new_rows and not rows:
        log(f"\nno USAspending observations fetched; preserved existing { _existing_line_count() } rows at {OUT_PATH}")
        return []
    rows = _merge_rows([], rows)
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} observations ({len(new_rows)} refreshed this run) -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("USAspending/SAM procurement topic-year summaries (official no-auth API):")
    observations = collect()
    if not observations:
        print("\nNO observations collected - endpoint unreachable/empty this run.")
    else:
        print(f"\nfirst {min(5, len(observations))} observations:")
        for row in observations[:5]:
            print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
        print(f"\njsonl line count: {_existing_line_count()}")
