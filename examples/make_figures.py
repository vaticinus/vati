"""Generate the two launch figures into docs/ (committed, gitignore-exempt).

    python -m data.fetch          # once, public ForecastBench files
    python examples/make_figures.py

Both figures are built from real, public, leak-free data:

  docs/edge_with_ci.png  -- edge over the market price with 95% CIs for the three
                            seeded board entries. A market-copier sits on zero
                            despite a perfectly respectable Brier; only the
                            superforecasters clear zero.
  docs/reorder.png       -- the same 23 ForecastBench superforecasters ranked two
                            ways (Brier vs marginal edge). The board reorders at
                            Spearman rho = 0.66; one forecaster is 17th by Brier
                            and 4th by the information it adds.

No synthetic data, no hand-set numbers: every value is recomputed from the
released files at run time.
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from beyond_brier.forecastbench import load_round
from beyond_brier import build_leaderboard, reorder_stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
INK = "#1b1b1b"
BLUE = "#2c7fb8"
ORANGE = "#d95f0e"
GREY = "#b8b8b8"


def fig_edge_with_ci():
    """Horizontal edge + 95% CI for the seeded board entries (real data)."""
    path = os.path.join(ROOT, "leaderboard", "board.json")
    entries = json.load(open(path))["entries"]
    # plot worst-to-best so the superforecaster lands on top
    entries = sorted(entries, key=lambda e: e["edge"])
    labels = [e["name"].split(" (")[0] for e in entries]
    edge = [e["edge"] for e in entries]
    lo = [e["edge"] - e["edge_lo"] for e in entries]
    hi = [e["edge_hi"] - e["edge"] for e in entries]
    brier = [e["mean_brier"] for e in entries]
    clears = [e["edge_lo"] > 0 for e in entries]

    fig, ax = plt.subplots(figsize=(8.2, 3.4))
    ys = range(len(entries))
    for y, e, l, h, ok in zip(ys, edge, lo, hi, clears):
        c = BLUE if ok else ORANGE
        ax.errorbar(e, y, xerr=[[l], [h]], fmt="o", color=c, ecolor=c,
                    elinewidth=2, capsize=4, ms=8, zorder=3)
    ax.axvline(0, color=INK, lw=1.2, ls="--", zorder=1)
    ax.text(0, len(entries) - 0.4, "adds nothing\nover the market",
            ha="center", va="bottom", fontsize=8.5, color=INK)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([f"{l}\n(Brier {b:.3f})" for l, b in zip(labels, brier)],
                       fontsize=9)
    ax.set_xlabel("Marginal edge over the market price  (Brier units, >0 = beats the price)")
    ax.set_title("A good Brier can still add zero information",
                 fontsize=13, fontweight="bold", loc="left")
    ax.set_ylim(-0.6, len(entries) - 0.1)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(left=False)
    fig.text(0.01, -0.02,
             "ForecastBench 2024-07-21, market track. The copier echoes the price: "
             "fine Brier, edge indistinguishable from zero.",
             fontsize=8, color="#555")
    plt.tight_layout()
    out = os.path.join(DOCS, "edge_with_ci.png")
    plt.savefig(out, dpi=160, bbox_inches="tight")
    print(f"wrote {out}")


def fig_reorder():
    """Slopegraph: Brier rank vs marginal-edge rank, real market track."""
    df = load_round()
    board = build_leaderboard(df[df.track == "market"], min_n=20,
                              difficulty_adjust=True)
    r = reorder_stats(board)
    mover = board.loc[board["rank_change"].idxmax()]  # the headline climber

    fig, ax = plt.subplots(figsize=(6.6, 8.2))
    for _, row in board.iterrows():
        is_mover = row["forecaster"] == mover["forecaster"]
        if is_mover:
            c, lw, a, z = BLUE, 3.0, 1.0, 5
        elif row["rank_change"] > 0:
            c, lw, a, z = BLUE, 1.2, 0.45, 2
        elif row["rank_change"] < 0:
            c, lw, a, z = ORANGE, 1.2, 0.45, 2
        else:
            c, lw, a, z = GREY, 1.0, 0.5, 2
        ax.plot([0, 1], [row["brier_rank"], row["edge_rank"]], "-o",
                color=c, lw=lw, alpha=a, ms=4, zorder=z)

    ax.annotate(
        f"17th by Brier,\n4th by information added",
        xy=(1, mover["edge_rank"]), xytext=(1.18, mover["edge_rank"] - 1.5),
        fontsize=9.5, color=BLUE, fontweight="bold", va="center",
        arrowprops=dict(arrowstyle="->", color=BLUE, lw=1.5))
    ax.invert_yaxis()
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Rank by\nBrier", "Rank by\nmarginal edge"], fontsize=11)
    ax.set_ylabel("Rank  (1 = best)")
    ax.set_xlim(-0.25, 1.7)
    ax.set_yticks([1, 5, 10, 15, 20, 23])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_title(
        f"Same {r.n_forecasters} superforecasters, two rankings\n"
        f"Spearman $\\rho$ = {r.spearman_rho:.2f} (p = {r.spearman_p:.4f})",
        fontsize=13, fontweight="bold")
    fig.text(0.01, 0.005,
             "ForecastBench 2024-07-21, market track. Blue climbs under marginal "
             "edge, orange falls. Brier rewards easy questions; edge rewards "
             "beating the price.",
             fontsize=8, color="#555", wrap=True)
    plt.tight_layout(rect=(0, 0.03, 1, 1))
    out = os.path.join(DOCS, "reorder.png")
    plt.savefig(out, dpi=160, bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    os.makedirs(DOCS, exist_ok=True)
    fig_edge_with_ci()
    fig_reorder()
