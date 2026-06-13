# The honest forecasting leaderboard

Ranked by **marginal edge over the market price** (Brier units, higher = better).
A forecaster that merely copies the price scores ~0 here regardless of its raw Brier.
Everything is recomputable: `python -m leaderboard.submit render`.

| # | Forecaster | Kind | Edge | 95% CI | Mean Brier | Unpriced? | N |
|---|---|---|---:|---|---:|:---:|---:|
| 1 | Superforecaster median (ForecastBench 2024-07-21) | human | +0.0463 | [+0.0105, +0.0840] | 0.0844 | yes | 56 |
| 2 | Public median (ForecastBench 2024-07-21) | human | +0.0092 | [-0.0056, +0.0262] | 0.1215 | no | 56 |
| 3 | Market-copier LLM (demo) | llm | +0.0026 | [-0.0009, +0.0064] | 0.1281 | no | 56 |

*Edge = mean of `S(price, y) - S(forecast, y)` per question. Unpriced? = the forecast carries signal beyond the price in a forecast-encompassing logit (b_fc > 0, p < 0.05).*
