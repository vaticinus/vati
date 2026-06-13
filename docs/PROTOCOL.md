# Pre-registered forward protocol: does the LLM leaderboard reorder?

The natural target — recomputing the **LLM** forecasting leaderboard under
marginal edge — is not possible from public data today. ForecastBench releases
per-question raw forecasts only for human forecasters on one round; the LLM
leaderboard's score is a multi-round fixed-effect aggregate. So we **pre-register**
the LLM result as a forward, falsifiable prediction with a fixed instrument, in
the spirit of the Good Judgment Project's pre-specified design and Registered
Report practice.

Everything below is fixed before the outcomes it refers to are known. The
analysis instrument is the released code in this repository, run unmodified.

## Fixed instrument

- **Metric.** Per-question marginal edge `d_i = S(p_ref_i, y_i) - S(p_f_i, y_i)`,
  Brier as primary, log score as the robustness check
  (`beyond_brier.edge.per_question_edge`).
- **Reference prior.** The market price / crowd value at the benchmark freeze
  time for market-source questions; `0.5` for data-source questions.
- **Ranking statistic.** The difficulty-adjusted forecaster effect `alpha_f` from
  the two-way fixed-effects model (`beyond_brier.adjust`), not the raw mean edge.
- **Decomposition.** Forecast-encompassing logit with question-clustered SEs
  (`beyond_brier.decompose`).
- **Inference.** Percentile bootstrap over questions (B = 10,000), Diebold–Mariano
  with the Harvey–Leybourne–Newbold small-sample correction, Benjamini–Hochberg
  FDR at q = 0.10 (`beyond_brier.stats`).
- **In-sample log-score edges** are bias-corrected by `0.5/N`
  (`beyond_brier.edge.debias_logscore`); honest out-of-sample edges are not.

## Hypotheses

- **H1 (reorder exists).** On future ForecastBench rounds where per-question LLM
  forecasts and market priors coincide, ranking LLMs by `alpha_f` reorders the
  Brier ranking at Spearman `rho < 0.95` on the market track.
- **H2 (copying is detectable).** Models given the market freeze value as context
  will show a smaller unpriced component `b_fc` than models forecasting without it,
  at matched Brier.
- **H0 (the null we could land on).** If `rho ~ 1` and `b_fc` does not separate
  the two model classes, marginal edge adds nothing over Brier for LLMs and we
  say so.

## Evaluation venues

ForecastBench rounds as their per-question data become available; live
submissions to Metaculus and Prophet Arena where a per-question market prior and a
resolved outcome both exist.

## What would falsify the paper's framing

A persistent finding that the LLM leaderboard does **not** reorder under marginal
edge (Spearman `rho ~ 1` across rounds) would show that, for current LLMs on this
benchmark, Brier and marginal edge rank the same thing — i.e. the informative,
question-varying prior condition is not met in practice. We commit to reporting
that outcome.
