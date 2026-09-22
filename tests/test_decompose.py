"""Forecast-encompassing regression: priced vs unpriced signal."""
import numpy as np
import pytest

from beyond_brier import encompassing_regression
from beyond_brier.scores import expit


def test_recovers_unpriced_signal():
    # outcome driven by a latent the price only partly sees and the forecaster
    # sees more of -> b_fc should be positive and significant.
    rng = np.random.default_rng(0)
    n = 3000
    z = rng.normal(size=n)                       # latent driver
    y = (rng.uniform(size=n) < expit(1.5 * z)).astype(float)
    p_ref = expit(0.6 * z + rng.normal(0, 0.5, n))   # price: partial view
    p_f = expit(1.4 * z + rng.normal(0, 0.3, n))     # forecaster: sharper view
    enc = encompassing_regression(p_f, p_ref, y)
    assert enc.b_fc > 0
    assert enc.p_fc < 0.01


def test_copier_has_no_unpriced_signal():
    # A copier's separate coefficient is unidentifiable, not a significance test.
    rng = np.random.default_rng(1)
    n = 2000
    z = rng.normal(size=n)
    y = (rng.uniform(size=n) < expit(z)).astype(float)
    p_ref = expit(z + rng.normal(0, 0.3, n))
    p_f = p_ref.copy()
    with pytest.raises(ValueError, match="not identifiable"):
        encompassing_regression(p_f, p_ref, y)


def test_constant_prior_drops_ref_term():
    rng = np.random.default_rng(2)
    n = 500
    p_f = rng.uniform(0.05, 0.95, n)
    y = (rng.uniform(size=n) < p_f).astype(float)
    enc = encompassing_regression(p_f, np.full(n, 0.5), y)
    assert np.isnan(enc.b_ref)
    assert "constant" in enc.note


def test_clustered_se_differs_from_iid():
    # repeated questions -> clustering changes the SE
    rng = np.random.default_rng(3)
    n = 1200
    z = rng.normal(size=n)
    y = (rng.uniform(size=n) < expit(z)).astype(float)
    p_ref = expit(0.5 * z + rng.normal(0, 0.4, n))
    p_f = expit(z + rng.normal(0, 0.3, n))
    qid = np.repeat(np.arange(n // 4), 4)   # each question answered 4x
    iid = encompassing_regression(p_f, p_ref, y)
    clu = encompassing_regression(p_f, p_ref, y, question_id=qid)
    assert "clustered" in clu.note
    assert not np.isclose(iid.se_fc, clu.se_fc)


def test_too_few_rows_raises():
    with pytest.raises(ValueError):
        encompassing_regression([0.5] * 5, [0.5] * 5, [1, 0, 1, 0, 1])
