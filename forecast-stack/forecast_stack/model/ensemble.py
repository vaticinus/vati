"""Decorrelation + ensemble blend — the NORTH-STAR number for the whole build.

The moat (FORECAST_LLM.md §0) is NOT "8B beats 120B solo". It is **marginal-ensemble-value per $**:
a small model whose structured-data-grounded errors are UNCORRELATED with frontier web-text models, so
adding it to an ensemble cuts error variance ~1/N (which only happens when errors are independent —
"Not All Accuracy Is Equal", 2025). This module IS that measurement harness. It answers, on the
leak-free eval set, the two questions that decide whether the project has an edge:

  1. DECORRELATION — how independent is each forecaster's error from the others? (error-correlation matrix)
  2. MARGINAL VALUE — how much does adding column X cut the blended Brier of the rest? (the headline)

It is signal-agnostic: it reads the eval jsonl for the built-in baselines carried in each row
(`model_prob` = our leak-free quant/stat baseline, `crowd_prob` = the market crowd, `base_rate` =
reference-class prior) and accepts any number of EXTERNAL prediction columns via --pred name=path.jsonl
(id->prob), so the trained LLM's eval output and a frontier model's output plug straight in post-train.
Until those exist, it reports the decorrelation + blend among the signals we already have — a real,
$0, leak-free number today, and the exact frame the final artifact is scored on.

Blend math: linear pooling p = Σ wᵢ pᵢ (Σw=1, w≥0) — Brier is convex in p and p is linear in w, so a
simplex grid search finds the global optimum transparently (no sklearn). Also reports equal-weight and
logit-pool blends. Weights are fit on HALF the rows and scored on the OTHER half (no fit-on-itself).

Run:  python -m engine.forecastbench.ensemble                       # built-in baselines on grpo_eval
      python -m engine.forecastbench.ensemble --pred llm=eval_llm.jsonl --pred gpt=eval_frontier.jsonl
"""
from __future__ import annotations

import json
import math
import sys
from itertools import combinations
from pathlib import Path
from forecast_stack.config import DATA_DIR

DIR = DATA_DIR / "forecastbench" / "trainset"
BUILTIN = ["model_prob", "crowd_prob", "base_rate"]   # signals carried in the eval rows themselves


def _load(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def _clip(p):
    return min(1 - 1e-6, max(1e-6, float(p)))


def _logit(p):
    p = _clip(p)
    return math.log(p / (1 - p))


def _sigmoid(x):
    return 1 / (1 + math.exp(-x))


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)


def auc(ps, ys):
    pos = [p for p, y in zip(ps, ys) if y == 1]
    neg = [p for p, y in zip(ps, ys) if y == 0]
    if not pos or not neg:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def corr(a, b):
    """Pearson correlation of two equal-length vectors; None if degenerate."""
    n = len(a)
    if n < 2:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(va * vb)


def _simplex(k, step):
    """All weight vectors on the k-simplex with the given grid step (compositions of 1.0)."""
    n = round(1.0 / step)
    def rec(slots, rem):
        if slots == 1:
            yield (rem,)
            return
        for i in range(rem + 1):
            for tail in rec(slots - 1, rem - i):
                yield (i,) + tail
    for comp in rec(k, n):
        yield tuple(c / n for c in comp)


def best_linear_blend(cols, ys, fit_idx, rep_idx, step=0.05):
    """Fit Σwᵢpᵢ weights minimising Brier on fit_idx, return (weights, rep_brier). Grid on the simplex;
    refine once around the winner. cols = list of per-row prob vectors (all same length, no missing)."""
    k = len(cols)
    def b_at(w, idx):
        s = 0.0
        for i in idx:
            p = sum(w[j] * cols[j][i] for j in range(k))
            s += (p - ys[i]) ** 2
        return s / len(idx)
    best_w = min(_simplex(k, step), key=lambda w: b_at(w, fit_idx))
    # local refine at half-step around the winner
    fine = step / 2
    cands = [best_w]
    for j in range(k):
        for dj in (-fine, fine):
            w = list(best_w); w[j] += dj
            for l in range(k):
                if l != j:
                    w[l] -= dj / (k - 1)
            if all(x >= -1e-9 for x in w):
                cands.append(tuple(max(0.0, x) for x in w))
    best_w = min(cands, key=lambda w: b_at(w, fit_idx))
    return best_w, b_at(best_w, rep_idx)


def main():
    args = sys.argv[1:]
    def opt(flag, default=None):
        return args[args.index(flag) + 1] if flag in args else default
    data = opt("--data", str(DIR / "grpo_eval.jsonl"))
    preds = {}
    i = 0
    while i < len(args):                       # collect every --pred name=path
        if args[i] == "--pred":
            name, path = args[i + 1].split("=", 1)
            preds[name] = {r["id"]: _clip(r["prob"]) for r in _load(path) if r.get("prob") is not None}
        i += 1

    rows = _load(data)
    # Restrict to MARKET rows by default for the crowd story unless --all; the dataset half has no crowd.
    scope = "all rows" if "--all" in args else "rows with >=2 signals"
    # Assemble the signal table: every column we can score.
    columns = {}
    for name in BUILTIN:
        columns[name] = {r["id"]: _clip(r[name]) for r in rows if r.get(name) is not None}
    for name, m in preds.items():
        columns[name] = m

    # The comparison set = rows where outcome is known. For correlation/blend we need the rows where a
    # given PAIR/SET of columns all have a value → computed per subset (honest about coverage).
    y_by_id = {r["id"]: int(r["outcome"]) for r in rows if r.get("outcome") in (0, 1)}
    ids = [i for i in y_by_id]

    print(f"=== ENSEMBLE / DECORRELATION · {data} · {len(ids)} scored rows ===")
    print(f"signals: {', '.join(f'{k}({len(v)})' for k, v in columns.items())}\n")

    # 1) Solo skill per column (Brier/AUC on its own coverage).
    print("SOLO skill (each on its own covered rows):")
    solo = {}
    for name, m in columns.items():
        sub = [i for i in ids if i in m]
        if len(sub) < 20:
            print(f"  {name:12s} n={len(sub):5d}  (too thin to score)")
            continue
        ps = [m[i] for i in sub]; ys = [y_by_id[i] for i in sub]
        a = auc(ps, ys)
        solo[name] = (brier(ps, ys), a, len(sub))
        print(f"  {name:12s} n={len(sub):5d}  Brier {brier(ps, ys):.4f}  AUC {a if a is None else round(a,3)}")

    # 2) Pairwise ERROR-correlation matrix (signed residual p−y) on shared rows. LOW = decorrelated = good.
    names = [n for n in columns if n in solo]
    print("\nERROR-correlation ρ(eᵢ,eⱼ) on shared rows  (LOW = independent = high ensemble value):")
    for a_name, b_name in combinations(names, 2):
        shared = [i for i in ids if i in columns[a_name] and i in columns[b_name]]
        if len(shared) < 20:
            continue
        ea = [columns[a_name][i] - y_by_id[i] for i in shared]
        eb = [columns[b_name][i] - y_by_id[i] for i in shared]
        r = corr(ea, eb)
        print(f"  {a_name:12s} × {b_name:12s} n={len(shared):5d}  ρ={r if r is None else round(r,3)}")

    # 3) Best linear blend over the columns that share a common support (so the blend is honest).
    #    Pick the largest subset of columns with >=200 shared rows; fit weights on half, score on half.
    print("\nBLEND (fit weights on half, score on the held-out half):")
    best = None
    for k in range(len(names), 1, -1):
        for combo in combinations(names, k):
            shared = [i for i in ids if all(i in columns[c] for c in combo)]
            if len(shared) >= 200:
                best = (combo, shared)
                break
        if best:
            break
    if not best:
        print("  no column subset shares >=200 rows — add --pred columns or use --all"); return
    combo, shared = best
    ys = [y_by_id[i] for i in shared]
    cols = [[columns[c][i] for i in shared] for c in combo]
    fit_idx = list(range(0, len(shared), 2))
    rep_idx = list(range(1, len(shared), 2))
    # component Briers on the SAME shared support + held-out half (apples to apples)
    print(f"  support: {combo} on n={len(shared)} shared rows")
    for j, c in enumerate(combo):
        print(f"    {c:12s} Brier(holdout) {brier([cols[j][i] for i in rep_idx], [ys[i] for i in rep_idx]):.4f}")
    # equal-weight linear + logit pools
    eq = [sum(cols[j][i] for j in range(len(combo))) / len(combo) for i in range(len(shared))]
    lg = [_sigmoid(sum(_logit(cols[j][i]) for j in range(len(combo))) / len(combo)) for i in range(len(shared))]
    print(f"    {'equal-linear':12s} Brier(holdout) {brier([eq[i] for i in rep_idx], [ys[i] for i in rep_idx]):.4f}")
    print(f"    {'equal-logit':12s} Brier(holdout) {brier([lg[i] for i in rep_idx], [ys[i] for i in rep_idx]):.4f}")
    # Extremized log-odds pool (Satopää/Baron, arXiv 1705.02391): scale the mean log-odds by d>1.
    # When members carry INDEPENDENT information — exactly our decorrelation thesis: a frontier model +
    # our structured-grounded 8B — the simple average is under-confident and extremizing recovers the
    # shared signal (optimal d→√3≈1.73 for many independent forecasters; fewer/correlated → smaller).
    # Fit d on the fit half, report on the held-out half so it's not fit-on-itself. With the LLM column
    # added (--pred llm=...) this is the row that should beat both equal pools — the marginal-ensemble win.
    def _ext(d, idx):
        return [_sigmoid(d * sum(_logit(cols[j][i]) for j in range(len(combo))) / len(combo)) for i in idx]
    best_d = min((x / 10 for x in range(10, 31)),
                 key=lambda d: brier(_ext(d, fit_idx), [ys[i] for i in fit_idx]))
    ext_rep = _ext(best_d, rep_idx)
    print(f"    {'extremized':12s} Brier(holdout) {brier(ext_rep, [ys[i] for i in rep_idx]):.4f}  (d={best_d:.1f})")
    w, rep_b = best_linear_blend(cols, ys, fit_idx, rep_idx)
    print(f"    {'best-linear':12s} Brier(holdout) {rep_b:.4f}  weights "
          f"{dict(zip(combo, (round(x,2) for x in w)))}")

    # 4) MARGINAL VALUE of each column = how much the best blend WORSENS if you drop it (the headline:
    #    a column with high marginal value + low error-correlation is worth more than a 2nd frontier model).
    if len(combo) >= 2:
        print("\nMARGINAL VALUE (Δ holdout Brier if this column is removed from the blend; higher = more valuable):")
        _, full_b = best_linear_blend(cols, ys, fit_idx, rep_idx)
        for drop in range(len(combo)):
            sub_cols = [cols[j] for j in range(len(combo)) if j != drop]
            _, sub_b = best_linear_blend(sub_cols, ys, fit_idx, rep_idx)
            print(f"    {combo[drop]:12s} without it Brier {sub_b:.4f}  →  Δ {sub_b - full_b:+.4f}")


if __name__ == "__main__":
    main()
