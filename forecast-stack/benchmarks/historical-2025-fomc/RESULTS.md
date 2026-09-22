# Old-checkpoint replay: all eight 2025 FOMC decisions

**The harness did not establish a reliable advantage.** Llama's computed Brier improved on one meeting, but that answer retained contradictory Bayesian arithmetic. Qwen's harness was worse than its direct control. Both direct models returned 40% for every meeting. These are scored historical outcomes, not a general forecasting rating or evidence about current chat.

The cohort, separate resolutions, scorer and execution source were [frozen publicly before inference](https://github.com/vaticinus/vati/commit/fe266aa8c1bc0100210d62ea096995c5d86e3474). The outcomes were already known to the operator. This is **retrospective registration**, not a prospective prediction record.

## Results

Brier loss is `(probability - outcome)^2`; **lower is better**, zero is perfect. Every model/arm issued all eight forecasts, so no missingness adjustment changes these scores.

| Model and arm | Issued | Brier | Accuracy at p>50% | Conservative cost | Median latency |
|---|---:|---:|---:|---:|---:|
| Llama 3.1 8B — direct | 8/8 | 0.23500 | 5/8 | $0.00045736 | 0.53s |
| Llama 3.1 8B — harness | 8/8 | 0.21296 | 6/8 | $0.00437881 | 2.60s |
| Qwen 2.5 7B — direct | 8/8 | 0.23500 | 5/8 | $0.00102060 | 3.87s |
| Qwen 2.5 7B — harness | 8/8 | 0.25000 | 5/8 | $0.00961660 | 19.35s |
| Constant 50% baseline | 8/8 | 0.25000 | 5/8, ties NO | — | — |
| Constant 25% baseline | 8/8 | 0.25000 | 5/8 | — | — |

Paired harness-minus-direct Brier: **−0.02204 for Llama**, **+0.01500 for Qwen**. Negative favors the harness numerically. Both comparisons contain eight complete pairs, but only **one correlated policy-year cluster**. There is no defensible independent-cluster confidence interval or broad superiority claim here.

The harness cost about 9.6× the direct Llama arm and 9.4× the direct Qwen arm. All 32 forecasts used **67 attempted calls and $0.01547337 conservative accounting**; known reported cost was $0.01515337. The difference is conservative price accounting, not a claim of an additional invoice. This task's cumulative shared ledger reached **$1.33850922 across 804 calls**, below its original $2 ceiling. Other separately authorized application work uses separate records.

Full machine-readable records: [issued.json](issued.json), [scores.json](scores.json), [protocol.json](protocol.json), [cohort.json](cohort.json), [resolutions.json](resolutions.json).

## What was actually tested

- **Issue date:** January 15, 2025. Every meeting is future relative to that date. Actual inference occurred September 22, 2026; simulated snapshot dates must not be passed off as original issuance timestamps.
- **Cohort:** every regularly scheduled 2025 FOMC decision, from the [calendar announced August 9, 2024](https://www.federalreserve.gov/newsevents/pressreleases/monetary20240809a.htm). No selecting only the meetings the model got right. The chosen year and domain are still retrospective operator choices.
- **Event:** whether the target upper bound was lowered **at that meeting**, relative to immediately beforehand—not whether a cut had happened at any earlier point that year.
- **Inputs:** the [December 18, 2024 statement](https://www.federalreserve.gov/newsevents/pressreleases/monetary20241218a.htm), selected [December projections](https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20241218.htm), and the earlier calendar. This deliberately sparse packet omits other information available by January 15; it is not an exhaustive information-set reconstruction.
- **Outcomes:** five holds followed by September, October and December cuts, verified against the eight official statements linked in `resolutions.json`. Resolutions are stored separately and never loaded by the inference runner.
- **Controls:** same evidence, fresh context per row, no search or tools, no sharing earlier 2025 outcomes, temperature zero, fixed provider routes, no retries/fallbacks, and equal 8,192-token aggregate output ceilings. Actual compute and cost were not matched. Source version `fe266aa`, not the separately updated live chat deployment.
- **Baselines:** 50%, plus a deliberately naive 25% uniform-meeting heuristic suggested by roughly two quarter-point cuts in the pre-issue projection. That heuristic is not a market forecast or a calibrated probability.

## Checkpoint and contamination evidence

**Llama 3.1 8B Instruct:** Meta's [model card](https://raw.githubusercontent.com/meta-llama/llama-models/main/models/llama3_1/MODEL_CARD.md) states a December 2023 pretraining cutoff, July 23, 2024 release, and static offline training. The [reference repository](https://huggingface.co/api/models/meta-llama/Llama-3.1-8B-Instruct) reported revision `0e9e39f249a16976918f6564b8830bc894c89659`, last modified September 25, 2024. OpenRouter was restricted to Groq; returned metadata named Groq and the requested model.

**Qwen 2.5 7B Instruct:** [Qwen announced release September 19, 2024](https://qwenlm.github.io/blog/qwen2.5/). Its [reference repository](https://huggingface.co/api/models/Qwen/Qwen2.5-7B-Instruct) reported revision `a09a35458c702b33eeacc393d103063234e8bc28`, last modified January 12, 2025—before the registered issue date and all outcomes. A precise training cutoff was not established; none is invented. OpenRouter was restricted to Phala; returned metadata named Phala and the requested model.

**Important limit:** public reference revision dates are not an attestation of the weights actually served by a third-party endpoint. Quantization, provider transformations and alias fidelity remain unverified. Source pages were retrieved in 2026 as reader-text snapshots, with the long projection source explicitly excerpted, not independently timestamped original HTML archives. The historical interpretation is conditional on faithful checkpoint serving and the supplied dated source extracts. No “pretend it is 2025” prompt or failed recall probe proves decontamination.

## Why the apparent Llama gain is not a promotion

The entire Llama gain comes from October. Its typed model used prior 0.4 and likelihood ratio 2, producing the correct computed result:

`(0.4 × 2) / (0.6 + 0.4 × 2) = 0.57142857`.

But the approved answer's prose instead multiplied **probability** by the ratio and declared a posterior of **80%**. The delivered header said 57.1%, while the explanation said 80%. Both the prior and likelihood ratio were judgmental rather than empirically established. A better realized score on this one event does not make that answer internally reliable.

Qwen's October contract also invented a **2025-08-09 median projection** and described a value as below 4.375% but above 4.50%. The supplied August 9, **2024** document was a calendar announcement. Those unsupported and mutually inconsistent claims survived review. This is a post-hoc spot audit, not an exhaustive semantic-pass count; every original probability remains in the registered score.

Both direct models' constant 40% forecasts have **no meeting-level discrimination**. Their improvement over the 50% baseline is base-rate positioning on a year with three cuts out of eight. Accuracy is secondary; a classifier saying NO every time already gets five out of eight.

## Reproduce the numerical analysis without spending

From `vati/forecast-stack/`:

```sh
python3 benchmarks/historical-2025-fomc/score.py
```

This reads the public issuance projection and separate official-resolution records; it performs no network or model calls. It reports Brier, log loss including incorrect-certainty infinities, coverage, missing-loss bounds, paired deltas and the registered toy decision utility. That utility is not investment returns.

The evaluator now accepts `--study` and explicitly labeled historical mode. It checks reference release dates against the issue date, passes the issue date through the harness, and omits unsupported reasoning parameters for these older models. Capability metadata requires both `reasoning_supported` and `reasoning_mandatory`. Offline regressions cover historical dates, withholding outcome metadata, late-checkpoint rejection and provider compatibility.

For source inspection, use the registration commit above. Any later source changes must not be silently substituted in a replay. Re-running paid inference is not required to reproduce these scores and requires a fresh protocol, shared-ledger checkpoint and authorization; do not overwrite issued records or reset the ledger.

## Consequence for ForecastBench

This eight-event experiment does not estimate a ForecastBench leaderboard gain. ForecastBench contains different market and dataset questions, baselines, horizons and official aggregation. The current private submission entry point uses its own quant/crowd pipeline and optional LLM gap fill; changes to this TypeScript core do not automatically alter that path. Compare variants on the same permitted next-round question set, then wait for official resolutions. Do not substitute these Brier values for a Brier Index or rank.

**Decision:** retain cheap direct forecasts as a control. Require an independent fresh cohort, stronger semantic consistency and a score/cost advantage before expanding the harness. No 9/10 major-decision rating is supported.

AI assistance was used for protocol construction, source extraction, implementation and analysis. No independent human endorsement or replication is claimed. No model weights or private provider transcripts are redistributed.
