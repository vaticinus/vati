"""Software / model adoption — leading demand signal for frontier tech (GitHub + HuggingFace).

Real L5 demand for software-shaped frontiers (AI, crypto-adjacent, dev tooling) shows up first as
developers building on a technology. This feed counts NEW open-source repositories created per year
for a bounded list of frontier topics (GitHub search, keyless) plus NEW HuggingFace models created
per year per topic. Both are binned by the artifact's CREATION date and capped at the cutoff year,
so a year-Y count is knowable by year-end Y — leak-safe, never fetched_at.

Repo/model creation is a leading adoption signal: it rises while a technology is still
pre-commercial, ahead of revenue, procurement, and pricing.

Keyless. GitHub's unauthenticated search endpoint is rate-limited (~10 req/min); requests are paced.
Re-running overwrites the jsonl idempotently.
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
OUT_PATH = DATA_DIR / "feeds" / "tech_adoption.jsonl"
GH_SEARCH = "https://api.github.com/search/repositories"
HF_MODELS = "https://huggingface.co/api/models"
YEAR_START = 2014
CUTOFF_YEAR = 2025
GH_PACE_SECONDS = 7.0  # stay under the ~10 req/min unauthenticated search limit

# topic -> GitHub search term (quoted phrase where multi-word helps precision).
# Wide frontier-technology basket; each becomes its own creation-year series.
TOPICS: dict[str, str] = {
    # --- AI: architectures / techniques / paradigms ---
    "large_language_model": '"large language model"',
    "diffusion_model": '"diffusion model"',
    "transformer_model": '"transformer model"',
    "retrieval_augmented_generation": '"retrieval augmented generation"',
    "vector_database": '"vector database"',
    "reinforcement_learning": '"reinforcement learning"',
    "reinforcement_learning_human_feedback": "RLHF",
    "graph_neural_network": '"graph neural network"',
    "federated_learning": '"federated learning"',
    "neural_radiance_fields": "NeRF",
    "gaussian_splatting": '"gaussian splatting"',
    "mixture_of_experts": '"mixture of experts"',
    "model_quantization": '"model quantization"',
    "knowledge_distillation": '"knowledge distillation"',
    "prompt_engineering": '"prompt engineering"',
    "ai_agent": '"AI agent"',
    "multi_agent_systems": '"multi-agent"',
    "vision_language_model": '"vision language model"',
    "speech_recognition": '"speech recognition"',
    "text_to_speech": '"text to speech"',
    "text_to_image": '"text to image"',
    "text_to_video": '"text to video"',
    "fine_tuning": '"fine-tuning" LLM',
    "model_context_protocol": '"model context protocol"',
    "self_supervised_learning": '"self-supervised learning"',
    "neuro_symbolic": '"neuro-symbolic"',
    "world_model": '"world model"',
    # --- AI: named frameworks / tooling ---
    "langchain": "langchain",
    "llama_model": "llama LLM",
    "stable_diffusion": '"stable diffusion"',
    "whisper_asr": '"whisper" speech',
    "onnx_runtime": "onnx",
    "tensorrt": "tensorrt",
    "vllm_serving": "vllm",
    "ray_distributed": '"ray" distributed',
    # --- semiconductors / compute substrate ---
    "neuromorphic": "neuromorphic",
    "photonic_computing": '"photonic computing"',
    "risc_v": '"RISC-V"',
    "chiplet": "chiplet",
    "fpga_acceleration": '"FPGA" accelerator',
    "in_memory_computing": '"in-memory computing"',
    "analog_computing": '"analog computing"',
    # --- quantum ---
    "quantum_computing": '"quantum computing"',
    "quantum_error_correction": '"quantum error correction"',
    "quantum_machine_learning": '"quantum machine learning"',
    "quantum_cryptography": '"quantum cryptography"',
    "post_quantum_cryptography": '"post-quantum cryptography"',
    # --- security / cryptography ---
    "homomorphic_encryption": '"homomorphic encryption"',
    "zero_knowledge_proof": '"zero-knowledge proof"',
    "differential_privacy": '"differential privacy"',
    "confidential_computing": '"confidential computing"',
    # --- robotics / autonomy ---
    "humanoid_robot": '"humanoid robot"',
    "autonomous_driving": '"autonomous driving"',
    "robot_operating_system": '"ROS" robot',
    "robotic_manipulation": '"robotic manipulation"',
    "drone_autonomy": '"autonomous drone"',
    "slam_mapping": "SLAM robotics",
    "embodied_ai": '"embodied AI"',
    "sim_to_real": '"sim-to-real"',
    # --- biotech / bio ---
    "mrna": "mRNA",
    "crispr": "CRISPR",
    "protein_folding": '"protein folding"',
    "protein_design": '"protein design"',
    "alphafold": "alphafold",
    "single_cell_rnaseq": '"single-cell RNA"',
    "synthetic_biology": '"synthetic biology"',
    "gene_therapy": '"gene therapy"',
    "bioinformatics_pipeline": '"bioinformatics" pipeline',
    "spatial_transcriptomics": '"spatial transcriptomics"',
    "drug_discovery_ml": '"drug discovery" machine learning',
    # --- energy / batteries ---
    "solid_state_battery": '"solid state battery"',
    "lithium_battery": '"lithium battery"',
    "sodium_ion_battery": '"sodium-ion battery"',
    "battery_management_system": '"battery management system"',
    "green_hydrogen": '"green hydrogen"',
    "fuel_cell": '"fuel cell"',
    "perovskite_solar": "perovskite",
    "grid_optimization": '"grid optimization"',
    "smart_grid": '"smart grid"',
    "nuclear_fusion": '"nuclear fusion"',
    "small_modular_reactor": '"small modular reactor"',
    # --- climate / carbon ---
    "carbon_capture": '"carbon capture"',
    "direct_air_capture": '"direct air capture"',
    "carbon_accounting": '"carbon accounting"',
    "climate_modeling": '"climate model"',
    "precision_agriculture": '"precision agriculture"',
    # --- space ---
    "satellite_imagery": '"satellite imagery"',
    "cubesat": "cubesat",
    "spacecraft_simulation": '"spacecraft" simulation',
    "ground_station": '"ground station" satellite',
    # --- materials / manufacturing ---
    "metamaterials": "metamaterials",
    "additive_manufacturing": '"additive manufacturing"',
    "digital_twin": '"digital twin"',
    "topology_optimization": '"topology optimization"',
    # --- fintech / web3-adjacent ---
    "decentralized_finance": "DeFi",
    "zk_rollup": '"zk-rollup"',
    "smart_contract": '"smart contract"',
    "real_time_payments": '"real-time payments"',
    "open_banking": '"open banking"',
    "algorithmic_trading": '"algorithmic trading"',
    # --- networking / infra frontier ---
    "edge_computing": '"edge computing"',
    "5g_network": '"5G" network',
    "webassembly": "webassembly",
    "serverless": "serverless",
}


def _get_json(url: str, *, timeout: int = 45) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 public API
        return json.loads(resp.read().decode("utf-8", "replace"))


def _gh_count(term: str, year: int) -> int | None:
    q = f"{term} created:{year}-01-01..{year}-12-31"
    url = f"{GH_SEARCH}?{urllib.parse.urlencode({'q': q, 'per_page': 1})}"
    try:
        data = _get_json(url)
    except Exception:  # noqa: BLE001 - rate limit / transient; caller skips
        return None
    tc = data.get("total_count")
    return int(tc) if isinstance(tc, int) else None


def _collect_github(*, log=print) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for slug, term in TOPICS.items():
        counts: list[tuple[int, int]] = []
        for year in range(YEAR_START, CUTOFF_YEAR + 1):
            c = _gh_count(term, year)
            time.sleep(GH_PACE_SECONDS)
            if c is None:
                # one retry after a longer cooldown (rate-limit window)
                time.sleep(GH_PACE_SECONDS * 4)
                c = _gh_count(term, year)
                time.sleep(GH_PACE_SECONDS)
            if c is None:
                continue
            counts.append((year, c))
            day = date(year, 12, 31).isoformat()
            out.append({
                "series_id": f"github_new_repos__{slug}", "date": day, "event_time": day,
                "observed_at": day, "published_at": day, "value": float(c),
                "unit": "repos/yr", "metric": "github_new_repos", "domain": "software_adoption",
                "title": f"New GitHub repositories created ({slug})",
            })
        if counts:
            log(f"  + github {slug:<30s} {len(counts):2d}y  "
                f"{counts[0][1]}→{counts[-1][1]}")
        # flush incrementally so a mid-run interruption keeps progress
        _write(out)
    return out


def _collect_huggingface(*, log=print) -> list[dict[str, Any]]:
    """New HuggingFace models per topic per creation-year (paginated, bounded)."""
    out: list[dict[str, Any]] = []
    for slug, term in TOPICS.items():
        search = term.strip('"')
        by_year: dict[int, int] = {}
        cursor_url = (f"{HF_MODELS}?{urllib.parse.urlencode({'search': search, 'limit': 100, 'full': 'false'})}"
                      "&expand[]=createdAt")
        pages = 0
        while cursor_url and pages < 30:  # cap 3000 models/topic
            try:
                req = urllib.request.Request(cursor_url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                    link = resp.headers.get("Link", "")
            except Exception:  # noqa: BLE001
                break
            if not data:
                break
            for m in data:
                ca = str(m.get("createdAt") or "")[:4]
                if ca.isdigit():
                    y = int(ca)
                    if YEAR_START <= y <= CUTOFF_YEAR:
                        by_year[y] = by_year.get(y, 0) + 1
            cursor_url = ""
            for part in link.split(","):
                if 'rel="next"' in part:
                    cursor_url = part[part.find("<") + 1:part.find(">")]
            pages += 1
            time.sleep(0.3)
        for year, c in sorted(by_year.items()):
            day = date(year, 12, 31).isoformat()
            out.append({
                "series_id": f"hf_new_models__{slug}", "date": day, "event_time": day,
                "observed_at": day, "published_at": day, "value": float(c),
                "unit": "models/yr", "metric": "hf_new_models", "domain": "model_adoption",
                "title": f"New HuggingFace models created ({slug})",
            })
        if by_year:
            log(f"  + hf {slug:<30s} {len(by_year):2d}y  total={sum(by_year.values())}")
    return out


def _write(rows: list[dict[str, Any]]) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".jsonl.tmp")
    rows_sorted = sorted(rows, key=lambda r: (str(r["series_id"]), str(r["date"])))
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows_sorted:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(OUT_PATH)


def collect(*, log=print) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    log("GitHub repo-creation counts (paced for rate limit) ...")
    rows += _collect_github(log=log)
    log("HuggingFace model-creation counts ...")
    rows += _collect_huggingface(log=log)
    _write(rows)
    log(f"\nwrote {len(rows)} tech-adoption observations -> {OUT_PATH}")
    return rows


if __name__ == "__main__":
    print("Tech adoption (L5 demand) — GitHub + HuggingFace creation counts:")
    collect()
