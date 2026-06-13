"""Build a forecaster leaderboard ranked by marginal edge, and measure how far
it reorders against the Brier ranking.

Input is a *long* table: one row per (forecaster, question) with the forecast,
the reference prior, and the binary outcome. Output is a ranked DataFrame plus a
:class:`Reorder` summary you can drop straight into a README or a paper.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .scores import brier, get_score
from .edge import per_question_edge
from .adjust import difficulty_adjusted_effects
from .stats import bootstrap_test, diebold_mariano, benjamini_hochberg, spearman, kendall

REQUIRED = ("forecaster", "question", "p_f", "p_ref", "y")


@dataclass
class Reorder:
    n_forecasters: int
    spearman_rho: float
    spearman_p: float
    kendall_tau: float
    kendall_p: float
    n_edge_positive: int      # forecasters with bootstrap CI strictly > 0
    n_edge_positive_fdr: int  # ... that also survive BH-FDR at q
    score: str

    def as_dict(self):
        return asdict(self)


def build_leaderboard(long: pd.DataFrame,
                      score: str = "brier",
                      min_n: int = 20,
                      bootstrap: int = 10000,
                      fdr_q: float = 0.10,
                      difficulty_adjust: bool = True) -> pd.DataFrame:
    """Rank forecasters by marginal edge over the reference prior.

    Returns one row per forecaster with: ``n``, ``mean_brier``, ``edge`` (mean
    per-question edge in ``score`` units), bootstrap ``edge_lo``/``edge_hi``, the
    Diebold-Mariano ``dm_p``, the difficulty-adjusted ``alpha`` (if requested),
    and the two rankings ``edge_rank`` / ``brier_rank`` plus ``rank_change``
    (positive = the forecaster climbs under marginal edge).
    """
    missing = [c for c in REQUIRED if c not in long.columns]
    if missing:
        raise ValueError(f"long table missing columns: {missing}")

    df = long.dropna(subset=["p_f", "p_ref", "y"]).copy()
    df["d"] = per_question_edge(df["p_f"], df["p_ref"], df["y"], score=score)
    df["s_f"] = brier(df["p_f"], df["y"])  # Brier anchor is always Brier, for comparability

    rows = []
    long_edges = []  # for the two-way FE difficulty adjustment
    for fid, g in df.groupby("forecaster"):
        if len(g) < min_n:
            continue
        lo, hi, boot_p = bootstrap_test(g["d"].to_numpy(), B=bootstrap)
        _, dm_p = diebold_mariano(g["d"].to_numpy())
        rows.append({
            "forecaster": fid,
            "n": len(g),
            "mean_brier": float(g["s_f"].mean()),
            "edge": float(g["d"].mean()),
            "edge_lo": lo,
            "edge_hi": hi,
            "boot_p": boot_p,
            "dm_p": dm_p,
        })
        long_edges.append(g[["forecaster", "question", "d"]].rename(columns={"d": "edge"}))

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    if difficulty_adjust and long_edges:
        le = pd.concat(long_edges, ignore_index=True)
        alpha = difficulty_adjusted_effects(le)["alpha"]
        out["alpha"] = out["forecaster"].map(alpha)
    else:
        out["alpha"] = np.nan

    # rankings: edge / alpha higher = better; brier lower = better
    head = "alpha" if (difficulty_adjust and out["alpha"].notna().any()) else "edge"
    out["edge_rank"] = out[head].rank(ascending=False, method="min").astype(int)
    out["brier_rank"] = out["mean_brier"].rank(ascending=True, method="min").astype(int)
    out["rank_change"] = out["brier_rank"] - out["edge_rank"]  # +ve = climbs under edge

    # FDR flag — uses the bootstrap p-value, coherent with the CI
    pos_ci = out["edge_lo"] > 0
    reject = benjamini_hochberg(out["boot_p"].fillna(1.0).to_numpy(), q=fdr_q)
    out["edge_positive"] = pos_ci
    out["edge_positive_fdr"] = pos_ci & reject

    out = out.sort_values(head, ascending=False).reset_index(drop=True)
    out.attrs["score"] = score
    out.attrs["ranked_by"] = head
    return out


def reorder_stats(leaderboard: pd.DataFrame, fdr_q: float = 0.10) -> Reorder:
    """Spearman/Kendall reordering of the edge ranking against Brier, plus power."""
    out = leaderboard
    score = out.attrs.get("score", "brier")
    head = out.attrs.get("ranked_by", "edge")
    rho, rho_p = spearman(-out["mean_brier"].to_numpy(), out[head].to_numpy())
    tau, tau_p = kendall(-out["mean_brier"].to_numpy(), out[head].to_numpy())
    return Reorder(
        n_forecasters=int(len(out)),
        spearman_rho=rho, spearman_p=rho_p,
        kendall_tau=tau, kendall_p=tau_p,
        n_edge_positive=int(out["edge_positive"].sum()),
        n_edge_positive_fdr=int(out["edge_positive_fdr"].sum()),
        score=score,
    )
