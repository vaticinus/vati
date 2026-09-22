# Gemma 27B: delivery works more often; forecasting advantage remains unproved

Registration: [`2c0646e`](https://github.com/vaticinus/vati/commit/2c0646e9e5998dbd4f9b819240d6644491d3e1ed), published before inference. Six already-resolved FOMC meetings, issue date May 1, 2025, one archived March statement, one correlated policy-year cluster. This is an exposed development sample, not an independent holdout or a ForecastBench submission.

## Scores

Lower Brier and log loss are better. The declared constant 50% reference has Brier 0.25 and log loss 0.6931 on these cases. It is not a market forecast or an empirically estimated base rate.

| Measure | Direct | Reviewed harness |
|---|---:|---:|
| Issued / registered | 6 / 6 | 5 / 6 |
| Brier, issued cases only | 0.29083 | 0.24500 |
| Brier, same five paired cases | 0.26450 | 0.24500 |
| Log loss, issued cases only | 0.78502 | 0.68180 |
| Brier bounds, all six cases | [0.29083, 0.29083] | [0.20417, 0.37083] |
| Accounted cost, USD | 0.00472785 | 0.00863076 |
| Total elapsed seconds | 144.879 | 163.196 |

On the five delivered pairs, harness-minus-direct Brier is **−0.01950**. That numerical difference favors the harness, but excluding its rejected September forecast can change the conclusion. The all-case bounds overlap and permit the harness to be worse. There is only one cluster: no meaningful confidence interval, calibration claim or general model ranking follows. The direct model loses to the constant 50% reference on all six cases. The harness costs **1.83×** as much in this run.

| Scheduled meeting | Outcome: cut at that meeting | Direct | Harness |
|---|---:|---:|---:|
| May 7 | 0 | 0.30 | 0.30 |
| June 18 | 0 | 0.30 | 0.40 |
| July 30 | 0 | 0.30 | 0.25 |
| September 17 | 1 | 0.35 | Rejected |
| October 29 | 1 | 0.30 | 0.35 |
| December 10 | 1 | 0.25 | 0.30 |

## What the audit found

The following is a post-hoc AI-assisted audit of the saved responses, not an independent blinded human evaluation. Private paid transcripts are not included in this public export.

- **A mechanical false rejection:** the September harness answer was rejected because URL extraction treated a standard Markdown link with its URL as the visible label as a single, unprovided URL. The supplied source was present. A similar problem consumed a July correction. This is a citation parser defect, not evidence that the model fabricated the source. No rejected row has been converted into an issued forecast after the fact.
- **Unsupported observations passed review:** the issued May harness explanation asserted sector-level inflation moderation and emerging labor-market cooling. The single supplied March statement does not establish those observations. The review gate did not catch this.
- **Event wording drift remained:** several answers discussed a cut *by* a meeting date despite a sealed question about a cut *at that particular meeting*, relative to the immediately preceding rate. This appeared in issued harness explanations for June, October and December. Their typed event fields retained the question; that does not make the surrounding reasoning equivalent.
- **A directional reasoning error remained in the direct arm:** the June answer said a sustained fall in inflation would lower its rate-cut probability. It offered no mechanism explaining that direction. This is an audit concern, not a quantitative causal test.
- **Baseline anchoring:** some drafts used the declared 50% scoring reference as a substantive prior. The reference was explicitly not a market price or empirical base rate. A scoring comparator should not acquire evidential authority merely because it is in the question record.
- **The reviewer also introduced questionable objections:** an October review demanded removal of update/kill criteria because they were not the settling rule, dismissed the March statement as mere background rather than forecasting evidence, and objected to describing uncertainty as justification for a judgmental probability. Those are not automatically valid reasons to reject a forecast. The correction removed context, but the issued prose still drifted toward a cut by October. Self-review is not an independent semantic oracle.

The harness therefore cannot be graded as major-decision ready. Delivery improved, but semantic defects still survive successful review. Business-decision competence is **unmeasured** here: no utilities, feasible actions, constraints, decision regret or realized business payoff were evaluated. An 11/12 delivery rate is not a 9/10 forecasting score.

## Reproducibility and spending

- `manifest.json`: exact questions, evidence, pre-event reference revision and execution-source hashes.
- `protocol.json` and `run.mts`: frozen provider, rates, sampling and stopping policy. The runner pins Parasail, disables provider fallbacks, uses temperature zero and enforces token-price ceilings. An offline intercepted-request smoke verified provider selection, price caps and stopping after HTTP 429 before paid execution.
- `issued.json`: all 12 registered rows, including the rejection; no selection of only successful answers.
- `outcomes.json` and `resolutions.json`: labels and official settling evidence, not loaded by inference.
- `scores.json`: output of the existing `scoreBenchmark` function. Recompute it from `issued.json` and `outcomes.json`; inference is unnecessary for scoring.

The reference Gemma checkpoint predates the simulated issue date. Parasail's served FP8 weights are not independently attested to that revision. The archived March statement is a sparse information set, not a reconstruction of everything known on May 1.

This run used **21 actual requests and $0.01335861**. Across both Gemma runs, accounted spending is **$0.01693611 of the same $0.10 allowance**, including the earlier 12B failed-request reservation. The original task's cumulative spending upper bound is **$1.36754634 of $2**. Both budget locks were released. No EC2 instance, local model, Opus workflow or benchmark submission was launched.

The earlier 12B study remains unchanged. Model size, provider, sampling and parser state differ between the studies: their delivery difference does not isolate the parser repair. All numerical scores above describe this registered 27B run only. A later source fix must use a new registration for inference rather than silently replacing these records.
