"""The marginal edge: information a forecaster adds over the reference prior.

The per-question edge is a *difference of two proper scores at the same outcome*::

    d_i = S(p_ref_i, y_i) - S(p_f_i, y_i)

positive when the forecaster beats the prior on question ``i``. We aggregate the
difference, never the skill-score *ratio* ``1 - sum S_f / sum S_ref``, which is
biased and small-sample fragile (Wheatcroft, 2019). The difference keeps proper
incentives in ``p_f`` because the reference term does not depend on the forecast.

Two facts the rest of the library leans on:

* **Identity.** With a *constant* prior ``p_ref == c``, ranking forecasters by
  mean edge is identical to ranking by mean Brier (the prior term is a shared
  constant). So the metric can only reorder a leaderboard where the prior is
  informative and *question-varying*. See :func:`beyond_brier.tests`.

* **The 0.5/N free lunch (log score).** Adding the forecast as one extra logit
  regressor to a price-only model raises in-sample log-likelihood by ``0.5/N``
  nats *in expectation under the null of no added information* (Wilks' theorem:
  the LR statistic for one parameter has mean 1, i.e. 0.5 nats of log-likelihood,
  spread over N questions). Many reported LLM "edges over the market" are smaller
  than this artifact. :func:`debias_logscore` subtracts it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .scores import get_score


@dataclass
class EdgeResult:
    edge: float            # mean per-question edge (score units; >0 beats prior)
    n: int                 # number of questions
    se: float              # standard error of the mean edge
    score: str             # "brier" or "log"
    per_question: np.ndarray  # the raw d_i, for bootstrapping / regression

    def __repr__(self) -> str:
        return (f"EdgeResult(edge={self.edge:+.5f}, n={self.n}, "
                f"se={self.se:.5f}, score={self.score!r})")


def per_question_edge(p_f, p_ref, y, score: str = "brier") -> np.ndarray:
    """Vector of per-question edges ``S(p_ref, y) - S(p_f, y)``."""
    s = get_score(score)
    return np.asarray(s(p_ref, y) - s(p_f, y), dtype=float)


def marginal_edge(p_f, p_ref, y, score: str = "brier") -> EdgeResult:
    """Mean marginal edge of a single forecaster over the reference prior."""
    d = per_question_edge(p_f, p_ref, y, score=score)
    n = d.size
    se = float(d.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    return EdgeResult(edge=float(d.mean()), n=n, se=se, score=score,
                      per_question=d)


def debias_logscore(edge_nats: float, n: int) -> float:
    """Subtract the 0.5/N in-sample null bias from a log-score edge.

    Only meaningful for the *log* score and for an *in-sample* fit where the
    forecast was used to improve on the prior (e.g. the encompassing regression,
    or a per-forecaster log-likelihood gain). For an honest *out-of-sample* or
    held-out edge there is no such bias and you should not apply this.

    Returns ``edge_nats - 0.5 / n``.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    return float(edge_nats) - 0.5 / n
