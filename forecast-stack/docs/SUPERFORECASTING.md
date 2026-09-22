# From a working forecaster to a defensible forecasting claim

The target is lower forecast error, better decisions and fewer material failures on a declared evaluation cohort. Historical testing drives development; prospective records provide a separate check against hindsight and contamination. This repository does not yet establish general superforecaster-level performance.

## Three measurements, not one score

| Layer | Hold fixed | Measure | What a pass does not prove |
|---|---|---|---|
| Model | Question, information packet, issue time, sampling settings | Proper score, calibration, discrimination, factual and decision errors | That tools or orchestration improve it |
| Harness | The same model and permitted information | Contract preservation, evidence support, correct computation, useful abstention, record integrity, failures, cost and latency | That plausible assumptions predict reality |
| Whole system | A declared historical or prospective cohort, baseline policy and budget | Paired forecast skill and decision value after all costs | Expert replacement in untested domains |

Do not average these into a flattering single number. A correct 30% estimate attached to the wrong event is a failed answer. A sound forecast paired with the wrong payoff arithmetic is a failed decision. A technically clean harness can preserve a bad model's prediction perfectly.

The local experiment and explicit acceptance rubric are in [`benchmarks/2026-09-22/protocol.json`](../benchmarks/2026-09-22/protocol.json). Ten project gates make the desired “9/10” inspectable. They are acceptance requirements, not an official leaderboard or an estimated probability that the system is safe.

## Measured improvement and rejected calibration, 22 September

The probability engine now handles positive Bayesian likelihoods that previously underflowed to zero. The scorer avoids repeatedly searching the same rows and rebuilding sampled arrays. The workflow keeps scoring-only baselines out of inference prompts. These changes cost no model credits.

| Component diagnostic | Before | After |
|---|---:|---:|
| Bayesian cases within 1e-12 of a high-precision oracle | 1,339 / 1,398 | 1,398 / 1,398 |
| Valid Bayesian cases rejected as impossible | 31 | 0 |
| Largest absolute probability error among returned values | 0.333333 | 1.76e-14 |
| Scoring-baseline isolation, direct and reviewed arms | 0 / 2 | 2 / 2 |
| Median scoring time, 2,000 rows | 70.68 ms | 18.05 ms |
| Median scoring time, 10,000 rows | 613.65 ms | 53.43 ms |

The arithmetic grid uses 1,200-digit Decimal calculations on the exact binary floating-point inputs. It deliberately stresses rare evidence; its pass rate does not estimate the prevalence of failures in ordinary forecasts. Timing uses five warmed repetitions on one arm64 machine, with 100 event clusters. Every score field agrees within 8.2e-16, including missingness bounds and bootstrap endpoints. Timing is machine dependent. The baseline-isolation checks use injected synthetic completions, not a claim that a real model now ignores misleading evidence.

Receipts: [`harness-before.json`](../benchmarks/performance-2026-09-22/harness-before.json), [`harness-after.json`](../benchmarks/performance-2026-09-22/harness-after.json), and the [executable benchmark](../benchmarks/performance-2026-09-22/harness_benchmark.mts).

### Historical policy comparison

Nine development policies covered raw crowd probabilities, the incumbent calibration, a fitted source map, partial blends, shrinkage toward source frequencies, and small residual offsets. The selected offset policy was frozen before loading the August evaluation rounds. It did not qualify for adoption.

| August evaluation: 131 forecasts, 104 event IDs | Brier | Log loss |
|---|---:|---:|
| Raw frozen crowd reference | 0.088924 | 0.292385 |
| Incumbent calibration | 0.092506 | 0.304440 |
| Frozen residual candidate | 0.092038 | 0.303089 |

Candidate minus incumbent Brier was -0.000467, with an event-cluster bootstrap 95% interval of [-0.001989, +0.001135]. That misses the declared 0.002 minimum improvement and includes harm. Raw crowd also has a better point score, but its interval against the incumbent crosses zero. Neither result justifies selecting a new default after seeing this sample. The existing calibration is unchanged.

The evaluation uses the August 2, 16 and 30 ForecastBench rounds at a pinned upstream commit. Of 720 market rows, 512 were unresolved and 77 had an ID already exposed in the earlier archive; all remaining 131 were scored. An extraction audit corrected the exclusion set to include earlier unresolved questions too. No candidate probability was changed after evaluation began. The local freeze is not an independent preregistration witness. Nominal settlement dates do not prove when historical training labels first became available, and distinct IDs can still concern related events.

This is a crowd-aware mechanical replay, not a test of a new LLM, a crowd-free result, or an official ForecastBench score. Earlier old-checkpoint Llama, Qwen and Gemma studies remain unchanged. Inputs and transformations derived from [ForecastBench](https://github.com/forecastingresearch/forecastbench-datasets) retain [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); the code license does not replace the data license. Protocol, numerical inputs, development data and scores are in [`benchmarks/performance-2026-09-22/`](../benchmarks/performance-2026-09-22/).

### What earns the next improvement claim

| Target | Required evidence |
|---|---|
| Forecast accuracy | At least 0.002 lower mean Brier against the declared incumbent, paired cluster interval wholly below zero, no log-loss regression, consistent temporal signs, then fresh replication. Compare the same events and permitted information; include a strong direct model and an allowed crowd/reference policy. |
| Harness reliability | At least 99% issued coverage on the registered cohort, zero critical event/arithmetic/source failures in the declared adversarial suite, and every rejection/error retained. Synthetic passes alone cannot establish 99% real-world reliability. |
| Extra inference or research | Better paired scores or fewer substantive failures at the same total cost ceiling. Report actual cost and latency per attempted and issued forecast. A probability change or an approving reviewer is insufficient. |
| Decision usefulness | Lower regret under explicit utilities and constraints, including acquisition cost. This run did not measure business-decision quality. |
| External standing | A separately authorized official submission and its published rank, followed by replication across domains and issue windows. No top-rank claim follows from this release. |

These are project targets, not established achievements or universal sample-size rules. Set the next cohort size from development variance and the smallest useful effect. For the next judgment experiment, use a checkpoint released before its historical issue dates with auditable served-weight provenance and genuinely dated evidence. Freeze the direct control and evidence-selection treatment separately; keep the scoring comparator out of both unless it is explicitly permitted evidence. Do not spend on another calibration search over this exposed August cohort.

Reproduce the released measurements from `forecast-stack/`, with new output paths:

```sh
python benchmarks/performance-2026-09-22/replay.py --out /tmp/vati-policy-replay.json
python benchmarks/performance-2026-09-22/replay.py --arithmetic-cases --out /tmp/vati-arithmetic.json
node --experimental-strip-types benchmarks/performance-2026-09-22/harness_benchmark.mts \
  --cases /tmp/vati-arithmetic.json --out /tmp/vati-harness.json
```

For the before comparison, the three source hashes in `harness-before.json` identify `forecastEngine.ts`, `benchmark.ts` and `workflow.ts` at commit `9e0e7d1`. Place those unmodified files beside the current modules under `.before-`-prefixed names and pass `--prefix .before-` to the benchmark; remove the temporary copies afterward. This compares the named components, not a deployment of the entire old stack. Analysis and documentation were AI-assisted.

## Keep development moving without waiting for outcomes

Use already-resolved historical events with an older, pinned checkpoint and evidence available at each simulated issue date. Keep prospective forecasts running as a background audit, not as a prerequisite for the next engineering experiment. A historical score is available immediately; its credibility depends on checkpoint provenance, dated evidence and separation between development and held-out cases.

Run these comparisons separately:

- **Forecasting:** the same model and information, scored against actual outcomes and a declared baseline.
- **Harness reliability:** event preservation, evidence support, arithmetic, missing answers and cost.
- **Decision quality:** inventory allocation, capital budgets, scheduling or information purchases with explicit constraints and an executable payoff model. Measure regret against the best feasible action, not whether an evaluator likes the prose. Synthetic business cases establish bounded competence, not real-world profitability.

A forecast moving from 40% to 42% is not a two-point accuracy gain. More model calls earn their place only through better scores, fewer substantive failures or useful decision improvements on held-out cases.

Start a new ablation from the existing [structured workflow](../forecast-core/WORKFLOW.md). Its caller supplies the event definition; `runForecast` constructs the contract without asking a model to rewrite it. The older experimental runner extracts a contract with a model. Equality to that extraction cannot prove preservation of the original request. Compare direct generation with deterministic validation, then add review or better evidence as separate interventions. Keep previously registered runners and results unchanged.

## Run the free path first

Run commands from `vati/forecast-stack/`, the component directory in the source checkout:

```bash
python -m pip install -e '.[dev]'
python -m pytest
python examples/quickstart.py
npm --prefix forecast-core test
npm --prefix forecast-core run example
npm --prefix forecast-core run eval:list
```

The examples are synthetic. They run the actual calculator and ledger but do not call a real forecasting model. Passing them establishes neither calibration nor investment skill. The paid evaluator below is a separate, explicit action.

Rescore the released study without a key or network:

```bash
python benchmarks/analyze.py benchmarks/2026-09-22/numeric-inputs.json \
  --out /tmp/forecast-study-analysis.json
```

The output path must not exist. Compare it with the committed `benchmarks/2026-09-22/scores.json`. These compact inputs preserve numerical outcomes and explicit checks, not raw provider responses. No-point acceptance is retained from the runner; rescoring does not independently audit the original prose. Read the [results and limitations](../benchmarks/2026-09-22/RESULTS.md) before interpreting the numbers.

The maintained provider adapter received a separate post-study BYOK funding repair. For historical source-level replay, follow the reversible-patch instructions in the results before checking `transfer-freeze.json`. The current default OpenRouter model is MiMo 2.6 Flash; it is a cost choice, not a certification from the frozen transfer results. Personal keys never fall back to hosted funding. The default completion path still needs a caller-enforced spending cap.

## Run a bounded same-model comparison

Install Node 22.18 or later. Set `OPENROUTER_API_KEY` in your shell or an untracked environment file. Never paste a key into a fixture or report. From `forecast-core/`:

```bash
node --experimental-strip-types scripts/forecast_eval.mts \
  --provider openrouter --model xiaomi/mimo-v2.6-flash \
  --arms direct_thinking,harness --split dev \
  --max-cost 0.50 --ledger /tmp/forecast-study-budget.json \
  --out /tmp/forecast-study-dev.json

node --experimental-strip-types scripts/forecast_eval.mts \
  --provider openrouter --model xiaomi/mimo-v2.6-flash \
  --arms direct_thinking,harness \
  --cases ../benchmarks/2026-09-22/decision-dev.json \
  --max-cost 0.50 --ledger /tmp/forecast-study-budget.json \
  --out /tmp/forecast-study-decisions.json
```

Both commands share one cumulative $0.50 ceiling, not $0.50 each. Reusing a ledger does not create a new spending authorization. Use a **new output filename** for every attempt; the runner refuses overwrites. OpenRouter models currently allowlisted are `deepseek/deepseek-v4.1-flash`, `xiaomi/mimo-v2.6-flash` and `xiaomi/mimo-v2.6-pro`. Check current availability before a new experiment. The runner caps provider prices at $0.50/M input and $1.20/M output, disables fallback, and requests support for its parameters. A model above those prices is unavailable to this evaluation, not silently substituted.

The runner holds an exclusive lock on a validated cumulative ledger for the invocation. Before each request, it atomically records a conservative reservation and only then calls the provider. Unknown-cost failures keep their reservation. Token accounting uses the price ceiling without cache discounts; reported provider cost is preserved separately and cannot lower the conservative charge. A crash can leave a lock: confirm the process is no longer running before removing that lock, and **retain the ledger and outstanding charge**. The budget covers this runner only, not another application, retrieval service or browser session.

The default direct-thinking and harness draft each allow 8,400 output tokens. The harness also extracts the contract, reviews, and may correct and review once. A draft card can require a second contract extraction when initial planning returned no contract. The full ceiling is therefore 23,800 output tokens with standard review, or 33,800 with `--review-thinking`; the earlier study's 21,600 description omitted that extra extraction. `direct_budget` uses the configured full ceiling. This is an output-limit control, **not equal consumed compute or equal dollars**. Record actual usage and cost. For a truly matched-budget experiment, predeclare independent sampling/tool policies under the same total cost ceiling and evaluate those policies without choosing answers after outcomes.

Every output includes cohort and source hashes, raw visible answers, numerical results, failures, latency, cost and completion status. The budget ledger includes exact requests. Do not publish private questions or evidence merely because an output is JSON.

## Improve without training on your test

1. **Define the decision and resolving event.** Preserve geography, threshold, equality, deadline, settlement source, conditioning and initial-release/revision treatment. Define the action set and payoffs separately. A prediction market about a related event is not the same baseline.
2. **Select the cohort and comparators before running.** Use a simple reference: persistence for a series, an applicable base rate, and contemporaneous crowd where the rules permit. Include a strong reasoning-enabled direct model. Report the crowd-free and crowd-aware tracks separately.
3. **Freeze development and transfer.** Save selection rules, cases, evidence, source publication times, model/version, code/config hashes and the primary metric. Hold out mechanisms, sources and time periods, not just renamed companies. Once results are examined, that cohort is development forever.
4. **Diagnose failures before adding agents.** Classify event substitution, unsupported evidence, math errors, unjustified precision, decision mistakes, provider failures and unhelpful abstention separately. Do not count a provider error as a correct refusal. Do not hide it by substituting 50% in the forecast record.
5. **Change one component.** First repair the demonstrated defect. Then compare at fixed model and evidence. A review step earns its cost only if it reduces substantive failures or adds measured workflow value. More reviewers seeing the same evidence are not independent experts.
6. **Freeze again and run transfer once.** Retain the full result, including nulls and regressions. Do not “adapt until passing” on the same holdout. Failure means a new hypothesis and a genuinely new cohort, not a renamed retry.
7. **Maintain prospective scoring in parallel.** Publish a pre-outcome manifest through an independent witness, issue immutable predictions, and score every eligible resolution under the frozen missingness rule. Continue historical and executable-decision experiments while those outcomes mature. Local hashes and git timestamps alone are not independent timing evidence.

Useful model-side work includes selecting a better base model, evaluating the evidence-selection policy, and fitting a calibration map using only earlier development data. Fine-tuning should follow a replicated failure diagnosis and a frozen baseline, not precede them. Publish the raw adapter and any residual/blending policy as different systems. An adapter mixed at 10% with a base model has not demonstrated that the adapter alone is better. Parent-model rights, training-data rights and checkpoint hashes are separate release requirements.

Useful harness-side work includes source-vintage admission, full contract preservation, explicit conditional/joint models, decision arithmetic, coherent abstention and immutable records. Retrieval earns its place through a paired evidence/no-evidence ablation. Fresh but irrelevant documents can make a model worse. A URL match proves identity only, not that the page was fetched or supports the claim.

A card-free forecast answer requires an explicit `no_point:true` classification from the reviewer, not merely `valid:true`. This prevents an untyped scalar from passing through an underspecified approval response; it does not make the reviewer's mathematical or factual judgment infallible. Ordinary conversation without a forecast contract is a different path.

A withheld answer is not a verified correction either. The development audit found a critic supplying a false bound while rejecting a draft. Failure responses therefore label review objections as unverified diagnostics. This disclosure preserves the warning; it does not repair the reviewer's reasoning or make the extracted event infallible.

## Metrics that resist flattering stories

**Binary outcomes:** report Brier, `mean((p-y)^2)`, and paired differences on exactly matched questions. Lower is better. A constant 50% scores 0.25, but beating that weak baseline alone is not superforecasting. Prefer the difference of proper scores for inference against a reference; the independent [Beyond Brier package](../../README.md#beyond-brier) in this repository implements reference-relative evaluation. If also displaying the descriptive ratio `1 - BS_system / BS_baseline`, declare its baseline and denominator, omit it when the denominator is zero, and do not treat it as a proper per-question score or the primary inferential target. Include log loss to expose confident mistakes; report impossible-event errors explicitly rather than secretly clipping them away.

**Exact synthetic worlds:** when the true event probability is known, `(p-p_oracle)^2` is expected excess Brier loss. It is **not a realized Brier score** and says nothing about finding the right probabilities in real evidence. Count missing estimates, invalid cards and unsupported point estimates. Check sharp bounds and explanatory arithmetic separately. A correct scalar with a false explanation fails substantive acceptance.

In the synthetic evaluator, `oracle:null` means that the stipulated inputs do **not identify a point probability**. It does not mean an ordinary future event has not resolved yet. Do not use that fixture convention to grade live forecasts before outcomes arrive; record prospective predictions separately and apply proper scores when their declared events resolve.

The [22 September prospective pilot](../benchmarks/prospective-2026-09-22/RESULTS.md) records three future US macro events across MiMo v2.6 Pro, Gemini 3.8 Flash and DeepSeek v4.1 Flash. Nine direct forecasts issued; all nine harness rows failed. Outcomes remain unresolved. A disclosed stop-policy deviation makes the run exploratory, and zero complete arm pairs means no paired harness accuracy estimate. Its runner safety repair does not retroactively repair the experiment.

The [subsequent paired run](../benchmarks/paired-2026-09-22/RESULTS.md) issued one MiMo Flash pair: 40% direct and 42% harness for August job openings. DeepSeek's draft timed out and the repaired global stop prevented subsequent requests. Two of twelve planned rows issued; only one of six planned pairs is complete, and it remains unresolved. Reviewed text still contained deadline ambiguity and unsupported historical claims.

**Uncertainty:** use paired bootstrap intervals with clusters for shared events, source series or issue periods. Repeated horizons are not independent questions. Report the number of clusters and the cohort selection rule. Wilson intervals on pass rates describe finite samples under assumptions, not cross-domain guarantees. At 16/16 passes, even an ordinary two-sided 95% Wilson interval has a lower endpoint around 81%; that is not proof of 99% reliability. A small bootstrap with no observed failures cannot manufacture evidence about unobserved failure modes.

**Calibration:** report reliability by probability range with counts, uncertainty, sharpness and tail errors. Calibration is not merely a small average calibration error: predicting the base rate for everything can be calibrated but uninformative. Fit calibration on earlier data, freeze it, and evaluate later. Do not fit and score on the same resolved pool.

**Numeric outcomes:** use a proper distributional score such as CRPS, plus interval coverage and width at predeclared levels. A wide interval can achieve coverage without being useful. The typed core's predictive interval describes its assumed outcome distribution, not the uncertainty of its probability estimate.

**Decisions:** given action utilities `U(a,y)`, score expected regret as `max_a E[U(a,Y)] - E[U(chosen,Y)]`. Include costs, recovery, timing, liquidity and constraints. Where probabilities are only bounded, identify whether one action is preferred across the whole range; otherwise expose the decision threshold rather than invent a midpoint. Value of information is the improvement from conditioning an optimal action on a signal, less acquisition cost. Do not treat a profitable historical outcome as proof a high-risk policy was good.

**Reliability:** report attempted/issued/valid/abstained/error counts, material semantic failures, cost per attempted and useful answer, and latency quantiles. Survivor-only accuracy is not the headline. A critical event or decision error vetoes high-stakes readiness even if nine other checks pass.

## A model with an old cutoff can help, but a prompt is not a time machine

The suggested historical design is sound only with independently documented checkpoint provenance. Prefer a frozen checkpoint published before every evaluated outcome, pin its weights/tokenizer revision, and ensure the provider is actually serving that checkpoint. A marketing cutoff date or model alias is weaker evidence. The current MiMo catalogue does not supply a verified knowledge cutoff; this experiment therefore does not use MiMo's answers to resolved historical events as forecasting evidence.

Reconstruct evidence as it was available at the issue time. Preserve publication and reference dates separately, include archived initial releases rather than today's revised series, and audit retrieval for later snippets or outcome reveals. Use temporal development/validation/test blocks and keep all rows of the same event together. The `cutoff` recall probes can detect knowledge; failing a probe cannot prove its absence. Neither an `asof` prompt nor `outcome_after_checkpoint` performs this complete audit.

The completed [2025 FOMC historical replay](../benchmarks/historical-2025-fomc/RESULTS.md) applies this distinction to older Llama 3.1 8B and Qwen 2.5 7B checkpoints: 32/32 forecasts, about $0.0155 conservative cost, mixed harness score changes and material semantic failures. Reference revisions predate outcomes, but third-party served weights are not independently attested. The report preserves the retrospective nature of the study and does not establish a ForecastBench gain.

Gemma 3 is another candidate for this design. Google's [model card](https://ai.google.dev/gemma/docs/core/model_card_3) documents an August 2024 training-data cutoff; its [release announcement](https://blog.google/technology/developers/gemma-3/) is dated March 12, 2025. Prefer an original pinned checkpoint and simulated issue dates after its release, with later resolved outcomes. This is a provenance-based candidate choice, not a measured Gemma forecasting result.

Self-hosting can provide exact weight control and credit-funded bulk inference, but it is not inherently free or cheaper than an API. Record the checkpoint, tokenizer, quantization, serving version, billed GPU time, storage and achieved throughput. Verify current credit eligibility and require a spending ceiling and automatic shutdown before launch. A neutral endpoint name changes neither model knowledge nor cost.

## External proof and the route to 9/10

### ForecastBench

Use the [official methodology](https://www.forecastbench.org/about/) and [submission rules](https://github.com/forecastingresearch/forecastbench/wiki/How-to-submit-to-ForecastBench), not a local imitation of its leaderboard.

- New rounds are published every two weeks. The forecast set must be uploaded during the announced due-date window. Participation requires registration; do not send registration email or upload forecasts without owner authorization.
- Forecast at least 95% of requested market and dataset forecasts under the current rules. Missing forecasts are officially imputed at 0.5. This is an **evaluation policy**, not permission to replace an API failure in the source record with an invented prediction.
- Source plus question ID plus horizon identifies an event. Current submissions do not require the legacy `direction` field. Market settlement dates are not additional forecast horizons.
- The official leaderboard uses difficulty adjustment, equal category weighting and a Brier Index transformation. The local scorer returns raw category scores, equal-category `overall`, and separate `pooled_brier`. `overall` is null when a category has no resolved observations. `coverage_resolved` is not the official all-requested-forecast participation gate.
- Humans and newer AI systems may have answered different question sets. Read the [leaderboard caveats](https://www.forecastbench.org/leaderboards/). FRI's [July 2026 report](https://forecastingresearch.substack.com/p/ai-models-have-likely-reached-parity) describes several systems as statistically indistinguishable from the human reference, while noting the aged human cohort and uncertainty. That is not evidence that this stack has reached parity.

Score a downloaded, permitted submission locally:

```bash
forecast-stack score --forecast submission.json --resolutions resolutions.json
```

The scorer rejects duplicate identities, invalid probabilities and nonbinary resolved labels. The compact ID-map API rejects cross-source ID collisions; use the full source-aware submission format for multiple platforms.

### Metaculus / FutureEval

The [official bot template](https://github.com/Metaculus/metac-bot-template) and [forecasting-tools](https://github.com/Metaculus/forecasting-tools) provide the integration route. Verify the active tournament's rules, crowd permissions, costs and scoring. A latest-forecast Brier calculation is not interchangeable with a platform's time-weighted peer or baseline score. Keep publishing disabled until the exact run is authorized.

### Project promotion gate

Predeclare a domain-balanced, prospective cohort and require an interval-supported gain over both a naive comparator and an allowed strong comparator. The project rubric proposes at least 200 distinct resolved events, three domains, two issue windows, untouched replication, an independent witness and independent reproduction. These counts are conservative project targets, not a power calculation or official benchmark rule. Estimate required sample size from development variance and the smallest useful effect before spending on a large run.

For major decisions, additionally require source-grounded semantic review, explicit decision losses, sensitivity to uncertain assumptions, and human approval before irreversible action. No finite benchmark establishes competence on every geopolitical crisis, investment or operational decision.

## Release without overstating the result

Ship the maintained code, runnable examples, fixtures, protocol, scoring fixes and qualified results. Do not wait for a miracle claim to make the toolkit useful. Follow [`RELEASE.md`](RELEASE.md): explicit allowlist, no private history or operational records, installed-package acceptance, and reviewed public owner/contents. Weights and mixed-source datasets need their own rights and reproducibility audit. A code release can be ready while superforecaster evidence is not.

This walkthrough and the local experimental analysis were prepared with AI assistance. Numerical acceptance is computed from declared oracles and preserved records; prose review is not an independent human evaluation.
