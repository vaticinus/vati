"""Reproduce the paper's headline numbers from public ForecastBench data.

    python -m data.fetch                  # once, to get the public files
    python examples/reproduce_paper.py

Prints the market-track reorder (the result) and the data-track identity check,
and writes a slopegraph to out/fig_reordering.png if matplotlib is installed.
"""
import os

from beyond_brier.forecastbench import load_round
from beyond_brier import build_leaderboard, reorder_stats, encompassing_regression

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
os.makedirs(OUT, exist_ok=True)


def main():
    df = load_round()
    print(f"Loaded {len(df)} leak-free human forecast rows "
          f"({df.forecaster.nunique()} forecasters).")

    # ---- MARKET track: the reorder ----
    mkt = df[df.track == "market"]
    board = build_leaderboard(mkt, min_n=20, difficulty_adjust=True)
    r = reorder_stats(board)
    print("\n=== MARKET track (informative prior) ===")
    print(f"  rankable forecasters: {r.n_forecasters}")
    print(f"  reorder vs Brier: Spearman rho={r.spearman_rho:.3f} (p={r.spearman_p:.4f}), "
          f"Kendall tau={r.kendall_tau:.3f}")
    print(f"  forecasters with edge CI > 0: {r.n_edge_positive}  "
          f"(survive FDR q=0.10: {r.n_edge_positive_fdr})")
    print("\n  top 5 by difficulty-adjusted edge:")
    print(board.head(5)[["forecaster", "n", "mean_brier", "edge", "alpha",
                         "brier_rank", "edge_rank"]].to_string(index=False))

    # pooled encompassing: do superforecasters carry unpriced signal?
    sup = mkt[mkt.tag == "super"]
    enc = encompassing_regression(sup["p_f"], sup["p_ref"], sup["y"], question_id=sup["question"])
    print(f"\n  pooled encompassing (supers): {enc}")
    print(f"    -> b_fc>0 means information beyond the market price ({enc.note})")

    # ---- DATA track: the identity check ----
    dat = df[df.track == "data"]
    bd = build_leaderboard(dat, min_n=30, difficulty_adjust=False)
    rd = reorder_stats(bd)
    print("\n=== DATA track (constant prior 0.5 -> identity) ===")
    print(f"  rankable forecasters: {rd.n_forecasters}")
    print(f"  edge rank vs Brier rank Spearman rho = {rd.spearman_rho:.4f} "
          f"(should be 1.000 by construction)")

    _figure(board)


def _figure(board):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("\n(matplotlib not installed; skipping figure)")
        return
    sel = board.sort_values("brier_rank")
    fig, ax = plt.subplots(figsize=(7, max(6, 0.4 * len(sel))))
    for _, r in sel.iterrows():
        color = "#2c7fb8" if r.rank_change >= 0 else "#d95f0e"
        ax.plot([0, 1], [r.brier_rank, r.edge_rank], "-o", color=color, alpha=0.8, lw=1.5, ms=4)
        lab = r.forecaster.split(":")[-1][:6]
        ax.text(-0.03, r.brier_rank, lab, ha="right", va="center", fontsize=7)
        ax.text(1.03, r.edge_rank, lab, ha="left", va="center", fontsize=7)
    ax.invert_yaxis()
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Rank by\nBrier", "Rank by\nmarginal edge"])
    ax.set_ylabel("Rank (1 = best)")
    ax.set_xlim(-0.35, 1.35)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_title("Leaderboard reorders under marginal edge (market track)")
    plt.tight_layout()
    path = os.path.join(OUT, "fig_reordering.png")
    plt.savefig(path, dpi=150)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
