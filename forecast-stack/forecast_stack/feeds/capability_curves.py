"""Capability curves — fill the blind L2 layer (cost/unit, perf/$, perf/watt, efficiency).

L2 (capability) is the layer our whole thesis rests on: "track the SECOND derivative,
not the level" — the learning/cost curves whose slope-of-slope flagged deep learning years
before counts did. Before this feed the DB held ZERO capability-curve series.

Three keyless substrates, each a frontier capability/cost trajectory:

1. OWID technology cost & capability curves (their grapher CSV, redistributable slugs only):
   solar PV module $/W, genome sequencing $/genome, levelized cost of energy by tech,
   memory/storage $/TB, transistors per microprocessor, supercomputer FLOP/s. These are the
   canonical learning curves.
2. Epoch AI ML-hardware dataset → derived FRONTIER perf/$ (FLOP/s per USD) and perf/watt
   (FLOP/s per W) by accelerator release year — the compute capability curve under the models.
3. Epoch AI notable-models dataset → frontier training-compute cost (2023 USD) and training
   power draw by publication year — the capability frontier's price tag.
4. Epoch AI large-scale-models + notable-models datasets → PER-DOMAIN / PER-TASK capability
   curves: for each AI domain (Language, Vision, Image generation, Speech, Games, Multimodal,
   Biology, Robotics, Video, Audio, …) the yearly FRONTIER (max) training compute (FLOP),
   parameter count, and training-dataset size by publication year. This is the fine-grained
   capability grain — it flags a per-domain frontier moving before aggregate counts do.

Leak discipline: each point is dated to the reference YEAR-END (Dec 31), never fetched_at.
These are slow historical curves; the year-end convention matches the existing epoch_ai feed
and is conservative for forward use (a year-Y point is knowable by year-end Y at the latest for
released hardware / published models; OWID cost vintages publish after the reference year).
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import date
from pathlib import Path
from forecast_stack.config import DATA_DIR
from typing import Any

UA = "forecast-stack (+https://github.com/vaticinus/vati)"
OUT_PATH = DATA_DIR / "feeds" / "capability_curves.jsonl"
WINDOW_START = 1970
CUTOFF_YEAR = 2025

OWID = "https://ourworldindata.org/grapher/{slug}.csv"
EPOCH_HW = "https://epoch.ai/data/ml_hardware.csv"
EPOCH_MODELS = "https://epoch.ai/data/notable_ai_models.csv"
EPOCH_LSM = "https://epoch.ai/data/large_scale_ai_models.csv"

# Canonical AI domains we mint per-domain frontier curves for. Epoch tags models with a
# comma-separated multi-label Domain (e.g. "Multimodal,Language,Vision"); we credit each
# constituent domain that appears in our canonical set, plus a "multimodal" bucket for any
# row carrying >=2 labels. Slugs are lowercased/underscored for clean series_ids.
AI_DOMAINS: dict[str, str] = {
    "Language": "language",
    "Vision": "vision",
    "Image generation": "image_generation",
    "Speech": "speech",
    "Audio": "audio",
    "Video": "video",
    "Games": "games",
    "Multimodal": "multimodal",
    "Biology": "biology",
    "Robotics": "robotics",
    "Mathematics": "mathematics",
    "Recommendation": "recommendation",
    "Earth science": "earth_science",
    "Medicine": "medicine",
    "3D modeling": "modeling_3d",
}

# Per-domain metrics: (csv_column, metric_stem, unit, human_label). Each becomes a
# series per domain, e.g. `frontier_compute_language`, `frontier_params_vision`.
DOMAIN_METRICS: list[tuple[str, str, str, str]] = [
    ("Training compute (FLOP)", "frontier_compute", "FLOP", "frontier training compute"),
    ("Parameters", "frontier_params", "count", "frontier parameter count"),
]

# OWID redistributable cost/capability curves. Each entry: slug -> list of
# (csv_column, metric, unit, domain, direction) where direction documents whether
# DOWN (cost falling) or UP (capability rising) is the improving direction.
OWID_CURVES: dict[str, list[tuple[str, str, str, str, str]]] = {
    "solar-pv-prices": [
        ("Solar PV module cost", "cost_per_watt_solar_pv", "USD/W", "energy", "down"),
    ],
    "cost-of-sequencing-a-full-human-genome": [
        ("Cost of sequencing a full human genome", "cost_per_genome", "USD", "biotech", "down"),
    ],
    "levelized-cost-of-energy": [
        ("Solar photovoltaic", "lcoe_solar_pv", "USD/kWh", "energy", "down"),
        ("Onshore wind", "lcoe_onshore_wind", "USD/kWh", "energy", "down"),
        ("Offshore wind", "lcoe_offshore_wind", "USD/kWh", "energy", "down"),
        ("Geothermal", "lcoe_geothermal", "USD/kWh", "energy", "down"),
        ("Bioenergy", "lcoe_bioenergy", "USD/kWh", "energy", "down"),
        ("Concentrated solar", "lcoe_concentrated_solar", "USD/kWh", "energy", "down"),
    ],
    "historical-cost-of-computer-memory-and-storage": [
        ("Memory", "cost_per_tb_memory", "USD/TB", "compute", "down"),
        ("Flash", "cost_per_tb_flash", "USD/TB", "compute", "down"),
        ("Disk", "cost_per_tb_disk", "USD/TB", "compute", "down"),
        ("Solid state", "cost_per_tb_ssd", "USD/TB", "compute", "down"),
    ],
    "transistors-per-microprocessor": [
        ("Transistors per microprocessor", "transistors_per_microprocessor", "count", "compute", "up"),
    ],
    "supercomputer-power-flops": [
        ("Computational capacity of the fastest supercomputer", "supercomputer_flops", "FLOP/s",
         "compute", "up"),
    ],
}


def _fetch(url: str, *, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 public CSV
        return resp.read().decode("utf-8-sig", "replace")


def _year(raw: Any) -> int | None:
    text = str(raw or "").strip()
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _num(raw: Any) -> float | None:
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row(series_id: str, year: int, value: float, metric: str, unit: str, domain: str,
         title: str) -> dict[str, Any]:
    day = date(year, 12, 31).isoformat()
    return {
        "series_id": series_id, "date": day, "event_time": day,
        "observed_at": day, "published_at": day, "value": float(value),
        "unit": unit, "metric": metric, "domain": domain, "title": title,
    }


def _collect_owid(*, log=print) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for slug, cols in OWID_CURVES.items():
        try:
            rows = list(csv.DictReader(io.StringIO(_fetch(OWID.format(slug=slug)))))
        except Exception as exc:  # noqa: BLE001 - one bad slug must not sink the feed
            log(f"  ! OWID {slug}: {exc}")
            continue
        # Cost curves are global learning curves: prefer the World aggregate row.
        for col, metric, unit, domain, _dir in cols:
            n = 0
            for r in rows:
                if str(r.get("Entity") or "") not in ("World", "OWID_WRL"):
                    continue
                year = _year(r.get("Year"))
                val = _num(r.get(col))
                if year is None or val is None or not (WINDOW_START <= year <= CUTOFF_YEAR):
                    continue
                if val <= 0:
                    continue
                out.append(_row(metric, year, val, metric, unit, domain,
                                f"{col} (World)"))
                n += 1
            if n:
                log(f"  + {metric:<32s} {n:3d} obs  ({slug})")
    return out


def _collect_epoch_hardware(*, log=print) -> list[dict[str, Any]]:
    """Derive frontier perf/$ and perf/watt by accelerator release year."""
    try:
        rows = list(csv.DictReader(io.StringIO(_fetch(EPOCH_HW))))
    except Exception as exc:  # noqa: BLE001
        log(f"  ! Epoch hardware: {exc}")
        return []
    best_per_dollar: dict[int, float] = {}
    best_per_watt: dict[int, float] = {}
    for r in rows:
        year = _year(r.get("Release date"))
        flops = _num(r.get("Tensor-FP16/BF16 performance (FLOP/s)"))
        price = _num(r.get("Release price (USD)"))
        tdp = _num(r.get("TDP (W)"))
        if year is None or flops is None or not (1990 <= year <= CUTOFF_YEAR):
            continue
        if price and price > 0:
            best_per_dollar[year] = max(best_per_dollar.get(year, 0.0), flops / price)
        if tdp and tdp > 0:
            best_per_watt[year] = max(best_per_watt.get(year, 0.0), flops / tdp)
    out: list[dict[str, Any]] = []
    for year, v in sorted(best_per_dollar.items()):
        out.append(_row("compute_flops_per_usd", year, v, "compute_flops_per_usd",
                        "FLOP/s per USD", "compute", "Frontier accelerator FP16 FLOP/s per USD"))
    for year, v in sorted(best_per_watt.items()):
        out.append(_row("compute_flops_per_watt", year, v, "compute_flops_per_watt",
                        "FLOP/s per W", "compute", "Frontier accelerator FP16 FLOP/s per watt"))
    if best_per_dollar:
        log(f"  + compute_flops_per_usd          {len(best_per_dollar):3d} obs  (Epoch hardware)")
    if best_per_watt:
        log(f"  + compute_flops_per_watt         {len(best_per_watt):3d} obs  (Epoch hardware)")
    return out


def _collect_epoch_models(*, log=print) -> list[dict[str, Any]]:
    """Frontier training-compute cost and power draw by model publication year."""
    try:
        rows = list(csv.DictReader(io.StringIO(_fetch(EPOCH_MODELS))))
    except Exception as exc:  # noqa: BLE001
        log(f"  ! Epoch models: {exc}")
        return []
    max_cost: dict[int, float] = {}
    max_power: dict[int, float] = {}
    max_params: dict[int, float] = {}
    for r in rows:
        year = _year(r.get("Publication date"))
        if year is None or not (2010 <= year <= CUTOFF_YEAR):
            continue
        cost = _num(r.get("Training compute cost (2023 USD)"))
        power = _num(r.get("Training power draw (W)"))
        params = _num(r.get("Parameters"))
        if cost and cost > 0:
            max_cost[year] = max(max_cost.get(year, 0.0), cost)
        if power and power > 0:
            max_power[year] = max(max_power.get(year, 0.0), power)
        if params and params > 0:
            max_params[year] = max(max_params.get(year, 0.0), params)
    out: list[dict[str, Any]] = []
    for year, v in sorted(max_cost.items()):
        out.append(_row("frontier_training_cost_usd", year, v, "frontier_training_cost_usd",
                        "USD (2023)", "AI", "Frontier model training-compute cost"))
    for year, v in sorted(max_power.items()):
        out.append(_row("frontier_training_power_w", year, v, "frontier_training_power_w",
                        "W", "AI", "Frontier model training power draw"))
    for year, v in sorted(max_params.items()):
        out.append(_row("frontier_model_parameters", year, v, "frontier_model_parameters",
                        "count", "AI", "Frontier model parameter count"))
    for label, d in (("frontier_training_cost_usd", max_cost),
                     ("frontier_training_power_w", max_power),
                     ("frontier_model_parameters", max_params)):
        if d:
            log(f"  + {label:<32s} {len(d):3d} obs  (Epoch models)")
    return out


def _domains_of(raw: Any) -> list[str]:
    """Map a comma-separated Domain cell to our canonical domain slugs (deduped)."""
    labels = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    slugs: list[str] = []
    for lab in labels:
        slug = AI_DOMAINS.get(lab)
        if slug and slug not in slugs:
            slugs.append(slug)
    # Any row tagged with >=2 recognised modalities is also a multimodal frontier point.
    if len(slugs) >= 2 and "multimodal" not in slugs:
        slugs.append("multimodal")
    return slugs


def _collect_per_domain(url: str, source: str, dataset_col: str, dataset_label: str,
                        *, log=print) -> list[dict[str, Any]]:
    """Per-domain frontier (max) compute / params / dataset-size by publication year.

    `dataset_col` is the (per-source) numeric training-dataset-size column; pass "" to skip
    it for a source that lacks a clean numeric one.
    """
    try:
        rows = list(csv.DictReader(io.StringIO(_fetch(url))))
    except Exception as exc:  # noqa: BLE001 - one bad source must not sink the feed
        log(f"  ! {source} per-domain: {exc}")
        return []

    metrics = list(DOMAIN_METRICS)
    if dataset_col:
        metrics = metrics + [(dataset_col, "frontier_dataset", "datapoints",
                              "frontier training-dataset size")]

    # (metric_stem, unit, label) -> {domain_slug -> {year -> max value}}
    acc: dict[tuple[str, str, str], dict[str, dict[int, float]]] = {}
    for r in rows:
        try:
            year = _year(r.get("Publication date"))
            if year is None or not (2010 <= year <= CUTOFF_YEAR):
                continue
            doms = _domains_of(r.get("Domain"))
            if not doms:
                continue
            for col, stem, unit, label in metrics:
                val = _num(r.get(col))
                if val is None or val <= 0:
                    continue
                key = (stem, unit, label)
                per_dom = acc.setdefault(key, {})
                for dom in doms:
                    yr_max = per_dom.setdefault(dom, {})
                    yr_max[year] = max(yr_max.get(year, 0.0), val)
        except Exception as exc:  # noqa: BLE001 - skip a malformed row, keep going
            log(f"  ! {source} per-domain row skipped: {exc}")
            continue

    out: list[dict[str, Any]] = []
    n_series = 0
    for (stem, unit, label), per_dom in sorted(acc.items()):
        for dom, yr_max in sorted(per_dom.items()):
            metric = f"{stem}_{dom}_{source}"
            title = f"Frontier {dom.replace('_', ' ')} model: {label} ({source})"
            for year, v in sorted(yr_max.items()):
                out.append(_row(metric, year, v, metric, unit, f"AI:{dom}", title))
            n_series += 1
    if out:
        log(f"  + {source} per-domain: {n_series:3d} curves, {len(out):4d} obs")
    return out


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows += _collect_owid(log=log)
    rows += _collect_epoch_hardware(log=log)
    rows += _collect_epoch_models(log=log)
    # Fine-grained per-domain capability curves. The large-scale dataset is the richer, more
    # recent frontier source; notable-models extends history and adds domains. We emit BOTH
    # under distinct (source-tagged) titles + disjoint series_ids via the source suffix below.
    rows += _collect_per_domain(
        EPOCH_LSM, "lsm", "(DEPRECATED) Training dataset size (datapoints)",
        "frontier training-dataset size", log=log)
    rows += _collect_per_domain(
        EPOCH_MODELS, "notable", "Training dataset size (total)",
        "frontier training-dataset size", log=log)
    rows.sort(key=lambda r: (str(r["series_id"]), str(r["date"])))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)
    log(f"\nwrote {len(rows)} capability-curve observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("Capability curves (L2) — keyless OWID + Epoch:")
    collect()
