"""Cheap ForecastBench belief-state harness.

This is the $0 proof layer for "can orchestration beat the prior?" It does not
call an LLM and it does not regenerate submissions by default. It reads resolved
ForecastBench rounds plus the local submission snapshots, builds a single-row
panel, and compares:

  - free prior: market crowd where available, 0.5 elsewhere
  - source prior: market crowd + train-only source base rates for dataset rows
  - mechanical: the current local ForecastBench submission probability
  - belief cap: an incremental log-odds update from prior toward mechanical
  - calibration: global and source-hierarchical Platt maps fit on train only
  - blends: simple and train-fit mixtures of the above

The split is chronological by round. This is intentionally not a leaderboard
score; it is a fast gate for whether the agentic/calibration layer carries
measurable holdout edge before any LLM or GPU spend.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .score import DATA, DATASET_SOURCES, MARKET_SOURCES


EPS = 1e-6


@dataclass(frozen=True)
class PanelRow:
    key: str
    due_date: str
    source: str
    group: str
    qid: str
    resolution_date: str | None
    outcome: int
    prior: float
    mechanical: float
    mechanical_missing: bool
    question: str


@dataclass
class Fit:
    source_priors: dict[str, float]
    source_counts: dict[str, int]
    belief_cap: float
    global_platt: tuple[float, float]
    source_offsets: dict[str, float]
    blend_columns: list[str]
    blend_weights: list[float]


def _clip(p: float, lo: float = 0.001, hi: float = 0.999) -> float:
    return min(hi, max(lo, float(p)))


def _logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _key_id(qid) -> str:
    if isinstance(qid, list):
        return json.dumps(qid, sort_keys=True)
    return str(qid)


def _direction_key(direction) -> str:
    return json.dumps(direction, sort_keys=True) if isinstance(direction, list) else "null"


def _as_prob(value) -> float | None:
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not math.isfinite(p) or not 0 <= p <= 1:
        return None
    return p


def _load_submission(path: Path) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str, str | None], float]]:
    """Return source-qualified market and dataset forecasts."""
    market: dict[tuple[str, str], float] = {}
    dataset: dict[tuple[str, str, str | None], float] = {}
    if not path.exists():
        return market, dataset
    doc = json.loads(path.read_text())
    for row in doc.get("forecasts", []):
        if row.get("direction") is not None:
            continue
        p = _as_prob(row.get("forecast"))
        if p is None:
            continue
        src = str(row.get("source"))
        qid = _key_id(row.get("id"))
        if src in MARKET_SOURCES:
            key = (src, qid)
            if key in market:
                raise ValueError(f"duplicate market forecast: {key}")
            market[key] = p
        else:
            key = (src, qid, row.get("resolution_date"))
            if key in dataset:
                raise ValueError(f"duplicate dataset forecast: {key}")
            dataset[key] = p
    return market, dataset


def available_rounds(data_dir: Path = DATA) -> list[str]:
    rounds = []
    for qpath in sorted(data_dir.glob("q_*.json")):
        name = qpath.name
        if len(name) != len("q_YYYY-MM-DD.json"):
            continue
        due = name[2:12]
        if (data_dir / f"r_{due}.json").exists():
            rounds.append(due)
    return rounds


def load_panel(data_dir: Path = DATA, *, require_submission: bool = True) -> list[PanelRow]:
    rows: list[PanelRow] = []
    for due in available_rounds(data_dir):
        qpath = data_dir / f"q_{due}.json"
        rpath = data_dir / f"r_{due}.json"
        spath = data_dir / f"submission_{due}.json"
        if require_submission and not spath.exists():
            continue
        qdoc = json.loads(qpath.read_text())
        rdoc = json.loads(rpath.read_text())
        by_id = {
            (q["source"], _key_id(q["id"])): q
            for q in qdoc.get("questions", [])
            if not isinstance(q.get("id"), list)
        }
        sub_market, sub_dataset = _load_submission(spath)
        for res in rdoc.get("resolutions", []):
            if not res.get("resolved") or isinstance(res.get("id"), list):
                continue
            qid = _key_id(res.get("id"))
            q = by_id.get((res.get("source"), qid))
            if not q:
                continue
            src = str(res.get("source") or q.get("source"))
            if src not in MARKET_SOURCES and src not in DATASET_SOURCES:
                continue
            group = "market" if src in MARKET_SOURCES else "dataset"
            outcome = _as_prob(res["resolved_to"])
            if outcome not in (0, 1):
                raise ValueError("resolved binary outcome must be 0 or 1")
            if group == "market":
                prior = _as_prob(q.get("freeze_datetime_value"))
                if prior is None:
                    prior = 0.5
                mechanical = sub_market.get((src, qid))
            else:
                prior = 0.5
                mechanical = sub_dataset.get((src, qid, res.get("resolution_date")))
            missing = mechanical is None
            if mechanical is None:
                mechanical = prior
            rd = res.get("resolution_date")
            key = "|".join([due, src, qid, str(rd), _direction_key(res.get("direction"))])
            rows.append(PanelRow(
                key=key,
                due_date=due,
                source=src,
                group=group,
                qid=qid,
                resolution_date=rd,
                outcome=outcome,
                prior=prior,
                mechanical=mechanical,
                mechanical_missing=missing,
                question=str(q.get("question") or ""),
            ))
    rows.sort(key=lambda r: (r.due_date, r.source, r.qid, str(r.resolution_date)))
    return rows


def chronological_split(rows: list[PanelRow], *, test_rounds: int = 6,
                        split_date: str | None = None) -> tuple[list[PanelRow], list[PanelRow]]:
    dates = sorted({r.due_date for r in rows})
    if split_date is None:
        if len(dates) <= test_rounds:
            raise ValueError("not enough rounds for requested chronological split")
        split_date = dates[-test_rounds]
    # Labels resolving at/after the first test issue date are unavailable to fit.
    train = [r for r in rows if r.due_date < split_date
             and r.resolution_date is not None and r.resolution_date < split_date]
    test = [r for r in rows if r.due_date >= split_date]
    if not train or not test:
        raise ValueError(f"empty train/test split at {split_date}")
    return train, test


def _brier(ps: list[float], ys: list[int]) -> float:
    return statistics.fmean((p - y) ** 2 for p, y in zip(ps, ys))


def _log_score(ps: list[float], ys: list[int]) -> float:
    vals = []
    for p, y in zip(ps, ys):
        p = _clip(p, 1e-5, 1 - 1e-5)
        vals.append(-(math.log(p) if y else math.log(1 - p)))
    return statistics.fmean(vals)


def _ece(ps: list[float], ys: list[int], bins: int = 10) -> float:
    total = 0
    err = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(ps) if lo <= p and (p < hi or (b == bins - 1 and p <= hi))]
        if not idx:
            continue
        conf = statistics.fmean(ps[i] for i in idx)
        emp = statistics.fmean(ys[i] for i in idx)
        total += len(idx)
        err += len(idx) * abs(conf - emp)
    return err / total if total else float("nan")


def _auc(ps: list[float], ys: list[int]) -> float | None:
    pos = [p for p, y in zip(ps, ys) if y == 1]
    neg = [p for p, y in zip(ps, ys) if y == 0]
    if not pos or not neg:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def _source_priors(rows: list[PanelRow], alpha: float = 20.0) -> tuple[dict[str, float], dict[str, int]]:
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for r in rows:
        sums[r.source] = sums.get(r.source, 0.0) + r.outcome
        counts[r.source] = counts.get(r.source, 0) + 1
    priors = {
        src: _clip((sums[src] + 0.5 * alpha) / (counts[src] + alpha))
        for src in counts
    }
    return priors, counts


def _start_prior(row: PanelRow, fit: Fit) -> float:
    if row.group == "market":
        return row.prior
    return fit.source_priors.get(row.source, 0.5)


def _belief_update(row: PanelRow, fit: Fit) -> float:
    start = _start_prior(row, fit)
    delta = _logit(row.mechanical) - _logit(start)
    cap = fit.belief_cap
    if cap < 50:
        delta = max(-cap, min(cap, delta))
    return _clip(_sigmoid(_logit(start) + delta))


def _platt(p: float, slope: float, intercept: float, offset: float = 0.0) -> float:
    return _clip(_sigmoid(slope * _logit(p) + intercept + offset))


def _fit_global_platt(rows: list[PanelRow], prob: Callable[[PanelRow], float]) -> tuple[float, float]:
    slopes = [0.25, 0.35, 0.5, 0.65, 0.8, 1.0, 1.25, 1.5, 1.8, 2.2, 2.6, 3.0]
    intercepts = [x / 10 for x in range(-20, 21)]
    best = (1.0, 0.0, float("inf"))
    ys = [r.outcome for r in rows]
    raw = [prob(r) for r in rows]
    for slope in slopes:
        for intercept in intercepts:
            ps = [_platt(p, slope, intercept) for p in raw]
            b = _brier(ps, ys)
            if b < best[2]:
                best = (slope, intercept, b)
    # One small local refinement around the best coarse point.
    slope0, intercept0, _ = best
    for slope in [max(0.05, slope0 + d) for d in (-0.15, -0.075, 0.0, 0.075, 0.15)]:
        for intercept in [intercept0 + d for d in (-0.15, -0.075, 0.0, 0.075, 0.15)]:
            ps = [_platt(p, slope, intercept) for p in raw]
            b = _brier(ps, ys)
            if b < best[2]:
                best = (slope, intercept, b)
    return round(best[0], 4), round(best[1], 4)


def _fit_source_offsets(rows: list[PanelRow], slope: float, intercept: float,
                        prob: Callable[[PanelRow], float],
                        *, min_n: int = 25, shrink_n: float = 75.0) -> dict[str, float]:
    offsets: dict[str, float] = {}
    by_source: dict[str, list[PanelRow]] = {}
    for r in rows:
        by_source.setdefault(r.source, []).append(r)
    grid = [x / 20 for x in range(-30, 31)]
    for src, rs in by_source.items():
        if len(rs) < min_n:
            continue
        ys = [r.outcome for r in rs]
        raw = [prob(r) for r in rs]
        best_o, best_b = 0.0, float("inf")
        for off in grid:
            b = _brier([_platt(p, slope, intercept, off) for p in raw], ys)
            if b < best_b:
                best_o, best_b = off, b
        offsets[src] = round(best_o * (len(rs) / (len(rs) + shrink_n)), 4)
    return offsets


def _fit_belief_cap(rows: list[PanelRow], fit: Fit) -> float:
    caps = [0.15, 0.25, 0.4, 0.6, 0.85, 1.1, 1.4, 1.75, 2.2, 3.0, 99.0]
    ys = [r.outcome for r in rows]
    best_cap, best_b = 99.0, float("inf")
    for cap in caps:
        fit.belief_cap = cap
        ps = [_belief_update(r, fit) for r in rows]
        b = _brier(ps, ys)
        if b < best_b:
            best_cap, best_b = cap, b
    return best_cap


def _simplex(k: int, step: float):
    slots = round(1.0 / step)

    def rec(n: int, rem: int):
        if n == 1:
            yield (rem,)
            return
        for i in range(rem + 1):
            for tail in rec(n - 1, rem - i):
                yield (i,) + tail

    for comp in rec(k, slots):
        yield [c / slots for c in comp]


def _project_simplex(values: list[float]) -> list[float]:
    """Euclidean projection onto {w: w>=0, sum(w)=1}."""
    u = sorted(values, reverse=True)
    cssv = []
    total = 0.0
    rho = 0
    for i, val in enumerate(u, 1):
        total += val
        cssv.append(total)
        if val - (total - 1.0) / i > 0:
            rho = i
    theta = (cssv[rho - 1] - 1.0) / rho if rho else 0.0
    return [max(0.0, v - theta) for v in values]


def _fit_blend(rows: list[PanelRow], fit: Fit, columns: list[str]) -> tuple[list[str], list[float]]:
    ys = [r.outcome for r in rows]
    col_values = [[predict_one(r, fit, c) for r in rows] for c in columns]
    k = len(columns)
    n = len(rows)
    groups = sorted({r.group for r in rows})
    group_counts = {g: sum(r.group == g for r in rows) for g in groups}
    row_weights = [
        1.0 / (len(groups) * group_counts[r.group])
        for r in rows
    ]
    w = [1.0 / k] * k
    # Convex least-squares over the simplex, weighted to match the reported
    # equal market/dataset Brier. Same objective, without the brute-force grid.
    lr = 0.8
    for _ in range(180):
        grad = [0.0] * k
        for i, y in enumerate(ys):
            p = sum(w[j] * col_values[j][i] for j in range(k))
            err = p - y
            for j in range(k):
                grad[j] += 2.0 * row_weights[i] * err * col_values[j][i]
        w = _project_simplex([w[j] - lr * grad[j] for j in range(k)])
        lr *= 0.985

    # Compare against the solo columns as a guard against a bad learning rate.
    def blend_brier(weights: list[float]) -> float:
        ps = [sum(weights[j] * col_values[j][i] for j in range(k)) for i in range(n)]
        return sum(row_weights[i] * (ps[i] - ys[i]) ** 2 for i in range(n))

    candidates = [w]
    for j in range(k):
        solo = [0.0] * k
        solo[j] = 1.0
        candidates.append(solo)
    best_w = min(candidates, key=blend_brier)
    return columns, [round(x, 4) for x in best_w]


def fit_methods(train: list[PanelRow]) -> Fit:
    priors, counts = _source_priors(train)
    fit = Fit(
        source_priors=priors,
        source_counts=counts,
        belief_cap=99.0,
        global_platt=(1.0, 0.0),
        source_offsets={},
        blend_columns=[],
        blend_weights=[],
    )
    fit.belief_cap = _fit_belief_cap(train, fit)
    slope, intercept = _fit_global_platt(train, lambda r: r.mechanical)
    fit.global_platt = (slope, intercept)
    fit.source_offsets = _fit_source_offsets(train, slope, intercept, lambda r: r.mechanical)
    cols = ["prior", "source_prior", "mechanical", "belief_cap", "hier_cal"]
    fit.blend_columns, fit.blend_weights = _fit_blend(train, fit, cols)
    return fit


METHODS = [
    "uniform",
    "prior",
    "source_prior",
    "mechanical",
    "belief_cap",
    "global_cal",
    "hier_cal",
    "mean_ensemble",
    "best_blend",
]


def predict_one(row: PanelRow, fit: Fit, method: str) -> float:
    if method == "uniform":
        return 0.5
    if method == "prior":
        return row.prior
    if method == "source_prior":
        return _start_prior(row, fit)
    if method == "mechanical":
        return row.mechanical
    if method == "belief_cap":
        return _belief_update(row, fit)
    if method == "global_cal":
        slope, intercept = fit.global_platt
        return _platt(row.mechanical, slope, intercept)
    if method == "hier_cal":
        slope, intercept = fit.global_platt
        return _platt(row.mechanical, slope, intercept, fit.source_offsets.get(row.source, 0.0))
    if method == "mean_ensemble":
        vals = [
            predict_one(row, fit, "source_prior"),
            predict_one(row, fit, "mechanical"),
            predict_one(row, fit, "belief_cap"),
            predict_one(row, fit, "hier_cal"),
        ]
        return _clip(statistics.fmean(vals))
    if method == "best_blend":
        vals = [predict_one(row, fit, c) for c in fit.blend_columns]
        return _clip(sum(w * v for w, v in zip(fit.blend_weights, vals)))
    raise KeyError(method)


def score(rows: list[PanelRow], fit: Fit, method: str) -> dict:
    ps = [predict_one(r, fit, method) for r in rows]
    ys = [r.outcome for r in rows]
    out = {
        "n": len(rows),
        "brier": _brier(ps, ys),
        "log_score": _log_score(ps, ys),
        "certain_wrong": sum(p == 1 - y for p, y in zip(ps, ys)),
        "ece": _ece(ps, ys),
        "auc": _auc(ps, ys),
    }
    for group in ("market", "dataset"):
        idx = [i for i, r in enumerate(rows) if r.group == group]
        out[f"n_{group}"] = len(idx)
        out[f"brier_{group}"] = _brier([ps[i] for i in idx], [ys[i] for i in idx]) if idx else None
    group_scores = [out["brier_market"], out["brier_dataset"]]
    out["equal_group_brier"] = statistics.fmean([x for x in group_scores if x is not None])
    return out




def bootstrap_delta(rows: list[PanelRow], fit: Fit, challenger: str, baseline: str,
                    *, iters: int = 5000) -> dict:
    """Paired equal-category Brier, clustered by source/question within category."""
    if not rows or not isinstance(iters, int) or iters < 100:
        raise ValueError("bootstrap requires rows and at least 100 iterations")
    groups = {}
    for row in rows:
        c = predict_one(row, fit, challenger)
        b = predict_one(row, fit, baseline)
        group = groups.setdefault(row.group, {})
        group.setdefault((row.source, row.qid), []).append(
            (b - row.outcome) ** 2 - (c - row.outcome) ** 2)
    strata = [[(sum(values), len(values)) for values in group.values()]
              for group in groups.values()]
    observed = statistics.fmean(sum(s for s, _ in clusters) / sum(n for _, n in clusters)
                                for clusters in strata)
    rng = random.Random(1337)
    deltas = []
    for _ in range(iters):
        group_means = []
        for clusters in strata:
            sampled = [rng.choice(clusters) for _ in clusters]
            group_means.append(sum(s for s, _ in sampled) / sum(n for _, n in sampled))
        deltas.append(statistics.fmean(group_means))
    deltas.sort()
    return {
        "mean": observed,
        "lo95": deltas[int(0.025 * iters)],
        "hi95": deltas[min(iters - 1, int(0.975 * iters))],
        "p_positive": sum(1 for d in deltas if d > 0) / iters,
        "clusters": sum(len(s) for s in strata),
    }


def _round_float(x):
    return None if x is None else round(float(x), 6)


def run(*, data_dir: Path = DATA, test_rounds: int = 6, split_date: str | None = None,
        require_submission: bool = True, bootstrap_iters: int = 5000) -> dict:
    rows = load_panel(data_dir, require_submission=require_submission)
    train, test = chronological_split(rows, test_rounds=test_rounds, split_date=split_date)
    fit = fit_methods(train)
    development_scores = {m: score(train, fit, m) for m in METHODS}
    selected_method = min(METHODS, key=lambda m: development_scores[m]["equal_group_brier"])
    scores = {m: score(test, fit, m) for m in METHODS}
    deltas = {
        "selected_vs_mechanical": bootstrap_delta(test, fit, selected_method, "mechanical", iters=bootstrap_iters),
        "hier_cal_vs_mechanical": bootstrap_delta(test, fit, "hier_cal", "mechanical", iters=bootstrap_iters),
        "best_blend_vs_mechanical": bootstrap_delta(test, fit, "best_blend", "mechanical", iters=bootstrap_iters),
        "selected_vs_prior": bootstrap_delta(test, fit, selected_method, "prior", iters=bootstrap_iters),
    }
    missing = sum(r.mechanical_missing for r in rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection": "Method selected on development only before evaluating test outcomes",
        "bootstrap_unit": "source/question clusters, stratified by category; equal-category mean",
        "log_score_clip_epsilon": 1e-5,
        "promotion_eligible": False,
        "limits": [
            "Mechanical historical diagnostic, not fresh LLM forecasting skill or an official leaderboard",
            "Resolution dates screen future development labels but do not certify publication-time availability",
            "Cross-question/date dependence and repeated use of this test set require a separate audit",
            "Exploratory secondary comparisons are not multiplicity-adjusted promotion evidence",
        ],
        "data_dir": str(data_dir),
        "rounds": sorted({r.due_date for r in rows}),
        "split": {
            "train_rounds": sorted({r.due_date for r in train}),
            "test_rounds": sorted({r.due_date for r in test}),
            "train_n": len(train),
            "test_n": len(test),
            "excluded_early_rows_with_unavailable_labels": sum(r.due_date < test[0].due_date for r in rows) - len(train),
        },
        "panel": {
            "n": len(rows),
            "n_market": sum(r.group == "market" for r in rows),
            "n_dataset": sum(r.group == "dataset" for r in rows),
            "mechanical_missing": missing,
        },
        "fit": {
            "belief_cap": fit.belief_cap,
            "global_platt": list(fit.global_platt),
            "source_offsets": fit.source_offsets,
            "source_priors": fit.source_priors,
            "source_counts": fit.source_counts,
            "blend_columns": fit.blend_columns,
            "blend_weights": fit.blend_weights,
        },
        "scores": {
            m: {k: _round_float(v) for k, v in s.items()}
            for m, s in scores.items()
        },
        "selected_method": selected_method,
        "development_scores": {m: {k: _round_float(v) for k, v in s.items()}
                               for m, s in development_scores.items()},
        "bootstrap_iters": bootstrap_iters,
        "deltas": {
            k: {kk: _round_float(vv) for kk, vv in v.items()}
            for k, v in deltas.items()
        },
    }
    return report


def write_markdown(report: dict, path: Path) -> None:
    scores = report["scores"]
    best = report["selected_method"]
    mech = scores["mechanical"]["equal_group_brier"]
    best_score = scores[best]["equal_group_brier"]
    delta = mech - best_score
    verdict = "DIAGNOSTIC ONLY: no forecasting-skill promotion authorized"
    lines = [
        "# ForecastBench Belief Harness",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Rows: `{report['panel']['n']}` single resolved rows "
        f"(`{report['panel']['n_market']}` market, `{report['panel']['n_dataset']}` dataset).",
        f"Train rounds: `{report['split']['train_rounds'][0]}` -> "
        f"`{report['split']['train_rounds'][-1]}` (`{report['split']['train_n']}` rows).",
        f"Test rounds: `{report['split']['test_rounds'][0]}` -> "
        f"`{report['split']['test_rounds'][-1]}` (`{report['split']['test_n']}` rows).",
        "",
        f"Verdict: **{verdict}** Development-selected method: `{best}`. Equal-group Brier delta vs "
        f"`mechanical`: `{delta:+.4f}`.",
        "",
        "| method | equal Brier | market | dataset | row Brier | log score | ECE | AUC |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in sorted(scores.items(), key=lambda kv: kv[1]["equal_group_brier"]):
        auc = "" if s["auc"] is None else f"{s['auc']:.4f}"
        market = "n/a" if s["brier_market"] is None else f"{s['brier_market']:.4f}"
        dataset = "n/a" if s["brier_dataset"] is None else f"{s['brier_dataset']:.4f}"
        lines.append(
            f"| `{name}` | {s['equal_group_brier']:.4f} | "
            f"{market} | {dataset} | "
            f"{s['brier']:.4f} | {s['log_score']:.4f} | {s['ece']:.4f} | {auc} |"
        )
    lines.extend([
        "",
        "## Fit",
        "",
        f"- Belief cap: `{report['fit']['belief_cap']}` log-odds.",
        f"- Global Platt `(slope, intercept)`: `{tuple(report['fit']['global_platt'])}`.",
        f"- Blend: `{dict(zip(report['fit']['blend_columns'], report['fit']['blend_weights']))}`.",
        "",
        "## Bootstrap",
        "",
        "Positive delta lowers equal-category Brier. Resampling clusters source/question within category. Log score uses the disclosed 1e-5 clipping diagnostic; certain-wrong counts remain visible in JSON.",
        "",
        "| comparison | mean | 95% low | 95% high | P(delta>0) |",
        "|---|---:|---:|---:|---:|",
    ])
    for name, d in report["deltas"].items():
        lines.append(
            f"| `{name}` | {d['mean']:+.4f} | {d['lo95']:+.4f} | "
            f"{d['hi95']:+.4f} | {d['p_positive']:.3f} |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def print_summary(report: dict) -> None:
    print("ForecastBench belief harness")
    print(f"  rows: {report['panel']['n']} "
          f"(market {report['panel']['n_market']}, dataset {report['panel']['n_dataset']})")
    print(f"  train: {report['split']['train_rounds'][0]} -> "
          f"{report['split']['train_rounds'][-1]} ({report['split']['train_n']} rows)")
    print(f"  test : {report['split']['test_rounds'][0]} -> "
          f"{report['split']['test_rounds'][-1]} ({report['split']['test_n']} rows)")
    print(f"  fit  : cap={report['fit']['belief_cap']} "
          f"platt={tuple(report['fit']['global_platt'])} "
          f"blend={dict(zip(report['fit']['blend_columns'], report['fit']['blend_weights']))}")
    print()
    print(f"{'method':16s} {'eq_brier':>9s} {'market':>9s} {'dataset':>9s} {'row':>9s} {'auc':>7s}")
    for name, s in sorted(report["scores"].items(), key=lambda kv: kv[1]["equal_group_brier"]):
        auc = "" if s["auc"] is None else f"{s['auc']:.3f}"
        market = "n/a" if s["brier_market"] is None else f"{s['brier_market']:.4f}"
        dataset = "n/a" if s["brier_dataset"] is None else f"{s['brier_dataset']:.4f}"
        print(f"{name:16s} {s['equal_group_brier']:9.4f} "
              f"{market:>9s} {dataset:>9s} "
              f"{s['brier']:9.4f} {auc:>7s}")
    best = report["selected_method"]
    delta = report["scores"]["mechanical"]["equal_group_brier"] - report["scores"][best]["equal_group_brier"]
    print()
    print(f"development-selected={best}  equal-group delta vs mechanical={delta:+.4f}; diagnostic, not promotion")
    for name, d in report["deltas"].items():
        print(f"  {name}: mean={d['mean']:+.4f}  "
              f"95%=[{d['lo95']:+.4f},{d['hi95']:+.4f}]  P>0={d['p_positive']:.3f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cheap local belief-state ForecastBench harness.")
    ap.add_argument("--data-dir", type=Path, default=DATA)
    ap.add_argument("--test-rounds", type=int, default=6)
    ap.add_argument("--split-date")
    ap.add_argument("--bootstrap-iters", type=int, default=5000)
    ap.add_argument("--allow-missing-submission", action="store_true")
    ap.add_argument("--out", type=Path, default=DATA / "belief_harness_results.json")
    ap.add_argument("--md", type=Path, default=DATA / "belief_harness_results.md")
    args = ap.parse_args(argv)
    report = run(
        data_dir=args.data_dir,
        test_rounds=args.test_rounds,
        split_date=args.split_date,
        require_submission=not args.allow_missing_submission,
        bootstrap_iters=args.bootstrap_iters,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
    write_markdown(report, args.md)
    print_summary(report)
    print(f"\nwrote {args.out}")
    print(f"wrote {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
