"""CORDIS EU research-project grant collector.

Small V1 feed for European public R&D funding pressure. It downloads the official CORDIS
HORIZON and H2020 project CSV ZIPs, aggregates project counts and EC contribution by
forecast-relevant topic and signed year, and writes a compact JSONL feed for the world-state layer.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import time
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any, Iterable

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OUT_PATH = DATA_DIR / "feeds" / "cordis.jsonl"
REQUEST_TIMEOUT_S = float(os.environ.get("CORDIS_TIMEOUT_S", "90"))
REQUEST_SPACING_S = float(os.environ.get("CORDIS_SPACING_S", "0.5"))
MIN_REFRESH_FRACTION = float(os.environ.get("CORDIS_MIN_REFRESH_FRACTION", "0.75"))
MIN_PROJECT_YEAR = int(os.environ.get("CORDIS_MIN_PROJECT_YEAR", "2014"))


@dataclass(frozen=True)
class DatasetSpec:
    program: str
    dataset_id: str
    url: str


@dataclass(frozen=True)
class Topic:
    slug: str
    title: str
    terms: tuple[str, ...]


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "HORIZON",
        "cordis-eu-research-projects-under-horizon-europe-2021-2027",
        "https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip",
    ),
    DatasetSpec(
        "H2020",
        "cordish2020projects",
        "https://cordis.europa.eu/data/cordis-h2020projects-csv.zip",
    ),
)


TOPICS: tuple[Topic, ...] = (
    # --- AI / compute ---
    Topic(
        "artificial_intelligence",
        "Artificial intelligence",
        ("artificial intelligence", "machine learning", "deep learning", "neural network", "foundation model"),
    ),
    Topic(
        "generative_ai",
        "Generative AI and foundation models",
        ("generative ai", "large language model", "foundation model", "transformer model", "generative model"),
    ),
    Topic(
        "edge_ai",
        "Edge AI and embedded intelligence",
        ("edge computing", "edge ai", "embedded ai", "tinyml", "neuromorphic"),
    ),
    Topic(
        "high_performance_computing",
        "High-performance computing",
        ("high performance computing", "supercomputing", "exascale", "hpc"),
    ),
    # --- Semiconductors ---
    Topic(
        "semiconductors",
        "Semiconductors and microelectronics",
        ("semiconductor", "microelectronic", "integrated circuit", "chip packaging", "photonic integrated"),
    ),
    Topic(
        "chip_fabrication",
        "Semiconductor fabrication",
        ("semiconductor manufacturing", "wafer fabrication", "lithography", "euv", "chip fabrication"),
    ),
    Topic(
        "wide_bandgap",
        "Wide-bandgap power semiconductors",
        ("gallium nitride", "silicon carbide", "wide bandgap", "wide-bandgap"),
    ),
    Topic(
        "photonics",
        "Photonics",
        ("photonics", "integrated photonics", "silicon photonics", "optoelectronic"),
    ),
    # --- Energy / power ---
    Topic(
        "battery_storage",
        "Battery storage",
        ("battery", "batteries", "lithium-ion", "lithium ion", "solid-state battery", "energy storage"),
    ),
    Topic(
        "grid_scale_storage",
        "Grid-scale energy storage",
        ("grid scale storage", "long duration storage", "flow battery", "redox flow"),
    ),
    Topic(
        "power_grid",
        "Power grid equipment and modernization",
        ("power grid", "electricity grid", "transmission grid", "distribution grid", "smart grid", "transformer"),
    ),
    Topic(
        "solar_power",
        "Solar power",
        ("photovoltaic", "solar cell", "perovskite", "solar power"),
    ),
    Topic(
        "wind_power",
        "Wind power",
        ("wind energy", "wind turbine", "offshore wind"),
    ),
    Topic(
        "geothermal",
        "Geothermal energy",
        ("geothermal energy", "geothermal power", "enhanced geothermal"),
    ),
    Topic(
        "hydrogen",
        "Hydrogen and fuel cells",
        ("hydrogen", "electrolyser", "electrolyzer", "fuel cell"),
    ),
    Topic(
        "fusion_energy",
        "Fusion energy",
        ("fusion energy", "nuclear fusion", "tokamak", "stellarator"),
    ),
    Topic(
        "fission_reactors",
        "Advanced fission reactors",
        ("small modular reactor", "advanced reactor", "nuclear fission", "molten salt reactor"),
    ),
    Topic(
        "carbon_capture",
        "Carbon capture and storage",
        ("carbon capture", "carbon storage", "co2 capture", "ccs"),
    ),
    Topic(
        "direct_air_capture",
        "Direct air capture and carbon removal",
        ("direct air capture", "carbon dioxide removal", "negative emission"),
    ),
    Topic(
        "synthetic_fuels",
        "Synthetic and sustainable fuels",
        ("synthetic fuel", "e-fuel", "sustainable aviation fuel", "power-to-liquid"),
    ),
    # --- Critical materials ---
    Topic(
        "critical_minerals",
        "Critical minerals and raw materials",
        ("critical raw material", "critical mineral", "rare earth", "lithium", "cobalt", "nickel", "graphite"),
    ),
    Topic(
        "rare_earth_magnets",
        "Rare-earth permanent magnets",
        ("permanent magnet", "rare earth magnet", "neodymium", "ndfeb"),
    ),
    Topic(
        "recycling_circular",
        "Recycling and circular materials",
        ("battery recycling", "material recovery", "urban mining", "circular economy"),
    ),
    Topic(
        "advanced_materials",
        "Advanced materials",
        ("advanced material", "composite material", "nanomaterial", "metamaterial", "2d material"),
    ),
    # --- Quantum / sensing ---
    Topic(
        "quantum",
        "Quantum technologies",
        ("quantum computing", "quantum technology", "quantum technologies", "quantum sensor", "quantum communication"),
    ),
    Topic(
        "quantum_communication",
        "Quantum communication and networking",
        ("quantum communication", "quantum key distribution", "quantum network", "quantum internet"),
    ),
    Topic(
        "post_quantum_crypto",
        "Post-quantum cryptography",
        ("post-quantum cryptography", "quantum-resistant", "quantum resistant", "lattice-based cryptography"),
    ),
    # --- Bio / health ---
    Topic(
        "biomanufacturing",
        "Biomanufacturing and synthetic biology",
        ("biomanufacturing", "synthetic biology", "biofoundry", "fermentation", "industrial biotechnology"),
    ),
    Topic(
        "health_biotech",
        "Health biotechnology",
        ("gene therapy", "cell therapy", "mrna", "vaccine", "therapeutic", "oncology", "biomedicine"),
    ),
    Topic(
        "genomics",
        "Genomics and gene editing",
        ("genomics", "gene editing", "crispr", "gene sequencing"),
    ),
    Topic(
        "pandemic_preparedness",
        "Pandemic preparedness and biosurveillance",
        ("pandemic preparedness", "biosurveillance", "infectious disease", "epidemic"),
    ),
    Topic(
        "neurotechnology",
        "Neurotechnology",
        ("neurotechnology", "brain-computer interface", "neural interface", "neuromorphic computing"),
    ),
    Topic(
        "precision_medicine",
        "Precision and personalized medicine",
        ("precision medicine", "personalised medicine", "personalized medicine", "biomarker"),
    ),
    # --- Robotics / autonomy ---
    Topic(
        "robotics",
        "Robotics and autonomous systems",
        ("robotics", "autonomous robot", "robotic system", "human-robot", "humanoid robot"),
    ),
    Topic(
        "autonomous_vehicles",
        "Autonomous vehicles",
        ("autonomous vehicle", "self-driving", "connected vehicle", "automated driving"),
    ),
    Topic(
        "drones_uav",
        "Drones and unmanned aerial systems",
        ("drone", "unmanned aerial", "uav", "uas"),
    ),
    # --- Space ---
    Topic(
        "space",
        "Space and satellites",
        ("satellite", "earth observation", "space launch", "space weather", "spacecraft"),
    ),
    Topic(
        "space_propulsion",
        "Space propulsion",
        ("space propulsion", "electric propulsion", "ion thruster", "in-space propulsion"),
    ),
    Topic(
        "satellite_communications",
        "Satellite communications",
        ("satellite communication", "satcom", "non-terrestrial network", "low earth orbit"),
    ),
    # --- Cyber / comms ---
    Topic(
        "cybersecurity",
        "Cybersecurity",
        ("cybersecurity", "cyber security", "zero trust", "cyber threat", "secure software"),
    ),
    Topic(
        "cryptography",
        "Cryptography",
        ("cryptography", "encryption", "secure communication", "privacy-preserving"),
    ),
    Topic(
        "five_g_six_g",
        "5G/6G networks",
        ("5g", "6g", "open ran", "beyond 5g", "next generation network"),
    ),
    # --- Manufacturing / materials ---
    Topic(
        "additive_manufacturing",
        "Additive manufacturing",
        ("additive manufacturing", "3d printing", "3d-printing", "metal printing"),
    ),
    Topic(
        "industrial_iot",
        "Industrial IoT and digital twins",
        ("industrial internet of things", "digital twin", "industry 4.0", "smart manufacturing"),
    ),
    Topic(
        "precision_optics",
        "Precision optics and sensors",
        ("precision optics", "optical sensor", "lidar", "infrared optics"),
    ),
    # --- Infrastructure / climate ---
    Topic(
        "water_infrastructure",
        "Water infrastructure",
        ("water treatment", "desalination", "water reuse", "water purification"),
    ),
    Topic(
        "agritech",
        "Agriculture technology",
        ("precision agriculture", "agritech", "vertical farming", "agricultural robotics"),
    ),
    Topic(
        "ev_charging",
        "EV charging infrastructure",
        ("electric vehicle charging", "ev charging", "fast charging", "charging infrastructure"),
    ),
    Topic(
        "climate_adaptation",
        "Climate adaptation and resilience",
        ("climate adaptation", "climate resilience", "flood protection", "disaster resilience"),
    ),
)


def _today() -> date:
    return datetime.now().date()


def _write_jsonl_atomic(rows: list[dict[str, Any]]) -> None:
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def _existing_line_count() -> int:
    if not OUT_PATH.exists():
        return 0
    with OUT_PATH.open(encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def _fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/zip,*/*"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310 official public data file
        return resp.read()


def _parse_decimal(value: Any) -> float | None:
    text = str(value or "").strip().replace("\xa0", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        out = float(text)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def _date_year(value: Any) -> int | None:
    text = str(value or "").strip()
    if len(text) < 4:
        return None
    try:
        return date.fromisoformat(text[:10]).year
    except ValueError:
        try:
            return int(text[:4])
        except ValueError:
            return None


def _project_year(row: dict[str, Any]) -> int | None:
    return (
        _date_year(row.get("ecSignatureDate"))
        or _date_year(row.get("startDate"))
        or _date_year(row.get("contentUpdateDate"))
    )


def _row_date(year: int, *, today: date) -> str | None:
    if year < MIN_PROJECT_YEAR or year > today.year:
        return None
    if year == today.year:
        return today.isoformat()
    return f"{year}-12-31"


def _csv_rows_from_zip(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    try:
        raw = zf.read(name)
    except KeyError:
        return []
    text = raw.decode("utf-8-sig", "replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [dict(row) for row in reader]


def _label_map(zf: zipfile.ZipFile) -> dict[str, list[str]]:
    labels: dict[str, list[str]] = defaultdict(list)
    for row in _csv_rows_from_zip(zf, "topics.csv"):
        project_id = str(row.get("projectID") or "").strip()
        if project_id:
            labels[project_id].append(str(row.get("topic") or ""))
            labels[project_id].append(str(row.get("title") or ""))
    for row in _csv_rows_from_zip(zf, "euroSciVoc.csv"):
        project_id = str(row.get("projectID") or "").strip()
        if project_id:
            labels[project_id].append(str(row.get("euroSciVocTitle") or ""))
            labels[project_id].append(str(row.get("euroSciVocPath") or ""))
    return labels


def _project_text(row: dict[str, Any], labels: Iterable[str] = ()) -> str:
    parts = [
        row.get("title"),
        row.get("objective"),
        row.get("keywords"),
        row.get("topics"),
        row.get("fundingScheme"),
        row.get("frameworkProgramme"),
        *labels,
    ]
    return " ".join(str(p or "") for p in parts).casefold()


def _matches(text: str, topic: Topic) -> bool:
    return any(term.casefold() in text for term in topic.terms)


def normalize_projects(
    projects: Iterable[dict[str, Any]],
    *,
    labels: dict[str, list[str]] | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    labels = labels or {}
    today = today or _today()
    aggregates: dict[tuple[str, int], dict[str, Any]] = {}
    source_urls = [d.url for d in DATASETS]

    for row in projects:
        project_id = str(row.get("id") or row.get("projectID") or "").strip()
        if not project_id:
            continue
        year = _project_year(row)
        if year is None or _row_date(year, today=today) is None:
            continue
        text = _project_text(row, labels.get(project_id, ()))
        matched = [topic for topic in TOPICS if _matches(text, topic)]
        if not matched:
            continue
        contribution = _parse_decimal(row.get("ecMaxContribution")) or 0.0
        total_cost = _parse_decimal(row.get("totalCost")) or 0.0
        program = str(row.get("_program") or row.get("frameworkProgramme") or "").strip() or "unknown"
        for topic in matched:
            key = (topic.slug, year)
            agg = aggregates.setdefault(
                key,
                {
                    "topic": topic,
                    "year": year,
                    "project_ids": set(),
                    "ec_contribution": 0.0,
                    "total_cost": 0.0,
                    "programs": defaultdict(int),
                    "sample_projects": [],
                },
            )
            if project_id in agg["project_ids"]:
                continue
            agg["project_ids"].add(project_id)
            agg["ec_contribution"] += contribution
            agg["total_cost"] += total_cost
            agg["programs"][program] += 1
            if len(agg["sample_projects"]) < 8:
                agg["sample_projects"].append(
                    {
                        "id": project_id,
                        "acronym": row.get("acronym"),
                        "title": row.get("title"),
                        "program": program,
                    }
                )

    rows: list[dict[str, Any]] = []
    for (slug, year), agg in sorted(aggregates.items()):
        topic: Topic = agg["topic"]
        day = _row_date(year, today=today)
        if day is None:
            continue
        base = {
            "date": day,
            "year": year,
            "topic": topic.title,
            "terms": list(topic.terms),
            "program_counts": dict(sorted(agg["programs"].items())),
            "source_urls": source_urls,
            "sample_projects": agg["sample_projects"],
        }
        count = float(len(agg["project_ids"]))
        rows.append(
            {
                **base,
                "series_id": f"cordis:{slug}:projects",
                "value": count,
                "unit": "projects",
                "metric": "cordis_projects_signed",
                "title": f"CORDIS - {topic.title} signed projects",
            }
        )
        rows.append(
            {
                **base,
                "series_id": f"cordis:{slug}:ec_contribution_eur",
                "value": round(float(agg["ec_contribution"]), 2),
                "unit": "EUR",
                "metric": "cordis_ec_contribution",
                "title": f"CORDIS - {topic.title} EC contribution",
            }
        )
        rows.append(
            {
                **base,
                "series_id": f"cordis:{slug}:total_cost_eur",
                "value": round(float(agg["total_cost"]), 2),
                "unit": "EUR",
                "metric": "cordis_total_cost",
                "title": f"CORDIS - {topic.title} project total cost",
            }
        )
    return rows


def _load_dataset(spec: DatasetSpec, *, log=print) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    raw = _fetch_bytes(spec.url)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        labels = _label_map(zf)
        projects = _csv_rows_from_zip(zf, "project.csv")
    for row in projects:
        row["_program"] = spec.program
        row["_dataset_id"] = spec.dataset_id
    log(f"  + {spec.program:<7s} {len(projects):6d} projects from {len(raw) / 1_000_000:.1f} MB zip")
    return projects, labels


def collect(*, log=print) -> list[dict[str, Any]]:
    all_projects: list[dict[str, Any]] = []
    all_labels: dict[str, list[str]] = defaultdict(list)
    for spec in DATASETS:
        try:
            projects, labels = _load_dataset(spec, log=log)
        except Exception as exc:  # noqa: BLE001 - official data file; preserve existing on partial failure
            log(f"  - {spec.program:<7s} failed: {exc}")
            continue
        all_projects.extend(projects)
        for project_id, values in labels.items():
            all_labels[project_id].extend(values)
        time.sleep(REQUEST_SPACING_S)

    rows = normalize_projects(all_projects, labels=all_labels)
    existing = _existing_line_count()
    if not rows:
        log(f"\nno CORDIS observations generated; preserved existing {existing} rows at {OUT_PATH}")
        return []
    if existing and len(rows) < int(existing * MIN_REFRESH_FRACTION):
        log(
            f"\npartial CORDIS refresh generated {len(rows)} rows < "
            f"{MIN_REFRESH_FRACTION:.0%} of existing {existing}; preserved {OUT_PATH}"
        )
        return []
    _write_jsonl_atomic(rows)
    log(f"\nwrote {len(rows)} observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("CORDIS EU grant topic aggregates (official CORDIS CSV distributions):")
    observations = collect()
    if observations:
        for row in observations[:5]:
            print("  " + json.dumps({k: row[k] for k in ("series_id", "date", "value", "unit", "title")}, ensure_ascii=False))
