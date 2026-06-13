"""60-second tour, no downloads. Run: python examples/quickstart.py

Shows the three ideas that make marginal edge different from Brier:
  1. A market-copier has a great Brier and ~zero edge.
  2. A contributor with a slightly worse Brier can have a real, positive edge.
  3. On a *constant* prior the edge ranking is identical to Brier (the identity).
"""
import numpy as np
import pandas as pd

from beyond_brier import marginal_edge, build_leaderboard, reorder_stats

rng = np.random.default_rng(0)
N = 600

# A world where a *decent but beatable* market price exists per question.
truth = rng.uniform(0.05, 0.95, N)          # latent probability
y = (rng.uniform(size=N) < truth).astype(int)
price = np.clip(truth + rng.normal(0, 0.14, N), 0.02, 0.98)   # mediocre market

# Three forecasters:
copier = np.clip(price + rng.normal(0, 0.01, N), 0.02, 0.98)  # just tracks the price
contributor = np.clip(truth + rng.normal(0, 0.07, N), 0.02, 0.98)  # its OWN, sharper read
noise = np.clip(rng.uniform(size=N), 0.02, 0.98)             # clueless

print("Per-forecaster marginal edge over the market price (Brier units):")
for name, p in [("copier", copier), ("contributor", contributor), ("noise", noise)]:
    er = marginal_edge(p, price, y)
    bs = float(((p - y) ** 2).mean())
    print(f"  {name:12s}  Brier={bs:.4f}   edge={er.edge:+.4f}  (n={er.n})")

print("\nThe copier wins on Brier but adds ~nothing; the contributor adds real signal.\n")

# Same three as a leaderboard.
long = pd.concat([
    pd.DataFrame({"forecaster": name, "question": np.arange(N), "p_f": p,
                  "p_ref": price, "y": y})
    for name, p in [("copier", copier), ("contributor", contributor), ("noise", noise)]
])
board = build_leaderboard(long, min_n=50, difficulty_adjust=False)
print(board[["forecaster", "mean_brier", "edge", "edge_lo", "edge_hi", "brier_rank", "edge_rank"]]
      .to_string(index=False))

# The identity: with a constant prior, edge-rank == brier-rank, exactly.
long_const = long.copy()
long_const["p_ref"] = 0.5
b2 = build_leaderboard(long_const, min_n=50, difficulty_adjust=False)
same = (b2.sort_values("forecaster")["edge_rank"].values ==
        b2.sort_values("forecaster")["brier_rank"].values).all()
print(f"\nIdentity check (constant prior -> edge rank == Brier rank): {same}")
