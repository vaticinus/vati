"""Priced vs. unpriced decomposition: a forecast-encompassing regression.

To separate skill a forecaster *adds* from skill it *echoes* off the price, fit,
in logit space over questions::

    y_i ~ Lambda(b0 + b_ref * logit(p_ref_i) + b_fc * logit(p_f_i))

* ``b_fc == 0``  -> the prior encompasses the forecaster (no unpriced edge).
* ``b_fc > 0``   -> information not in the price (the unpriced component).
* ``b_ref -> 0`` with large ``b_fc`` -> the forecaster dominates the prior.

Because the same outcome ``y_i`` is shared across all forecasters on a question,
residuals are clustered by question. We report **question-clustered** standard
errors; the i.i.d. ones a naive fit prints are wildly overconfident.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm

from .scores import logit


@dataclass
class Encompass:
    n: int
    b_fc: float
    se_fc: float
    p_fc: float
    b_ref: float
    se_ref: float
    p_ref: float
    b0: float
    note: str = ""

    def __repr__(self) -> str:
        ref = "constant (dropped)" if np.isnan(self.b_ref) else f"{self.b_ref:+.3f} (p={self.p_ref:.3g})"
        return (f"Encompass(n={self.n}, b_fc={self.b_fc:+.3f} (p={self.p_fc:.3g}), "
                f"b_ref={ref})")


def encompassing_regression(p_f, p_ref, y, question_id=None) -> Encompass:
    """Forecast-encompassing logit with optional question-clustered SEs.

    If ``question_id`` is given and has repeats, standard errors are clustered on
    it (this is the honest choice whenever multiple forecasters share questions).
    If the reference prior has no variation (the data-track ``p_ref == 0.5``
    case), the ref term is dropped and flagged.
    """
    p_f = np.asarray(p_f, dtype=float)
    p_ref = np.asarray(p_ref, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(p_f) & np.isfinite(p_ref) & np.isfinite(y)
    p_f, p_ref, y = p_f[mask], p_ref[mask], y[mask]
    if y.size < 20 or np.unique(y).size < 2:
        raise ValueError("need >=20 rows and both outcomes present")

    lref = logit(p_ref)
    cols = {"const": np.ones_like(y), "logit_fc": logit(p_f)}
    has_ref = np.std(lref) > 1e-9
    if has_ref:
        cols["logit_ref"] = lref
    X = pd.DataFrame(cols)

    fit_kw = {}
    if question_id is not None:
        groups = np.asarray(question_id)[mask]
        if pd.Series(groups).nunique() < groups.size:
            fit_kw = {"cov_type": "cluster", "cov_kwds": {"groups": groups}}
    m = sm.Logit(y, X).fit(disp=0, maxiter=200, **fit_kw)

    def g(name):
        return (float(m.params[name]), float(m.bse[name]), float(m.pvalues[name]))

    b_fc, se_fc, p_fc = g("logit_fc")
    if has_ref:
        b_ref, se_ref, p_ref_ = g("logit_ref")
        note = "question-clustered SEs" if fit_kw else "i.i.d. SEs (no repeated questions)"
    else:
        b_ref = se_ref = p_ref_ = float("nan")
        note = "reference prior constant; ref term dropped"
    return Encompass(n=int(y.size), b_fc=b_fc, se_fc=se_fc, p_fc=p_fc,
                     b_ref=b_ref, se_ref=se_ref, p_ref=p_ref_, b0=float(m.params["const"]),
                     note=note)
