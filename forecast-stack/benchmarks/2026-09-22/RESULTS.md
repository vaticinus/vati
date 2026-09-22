# Forecasting-model and harness audit — 22 September 2026

## Decision

**Do not promote this system as a 9/10 major-decision forecaster.** Mechanical defects were repaired, but the frozen transfer comparison did not establish a harness accuracy advantage. Both MiMo harnesses delivered fewer complete checked answers than their direct controls and still approved false explanations.

Current MiMo real-world forecasting skill is **unrated** by this study. The harness establishes **6 of 10 engineering acceptance gates**, with three partial and one unmet; partial gates do not count as passes. This is a project evidence-coverage rubric, not a 60% accuracy estimate or an independent certification. See [readiness.json](readiness.json).

Release the research code and its negative results, not an expert-replacement claim. The benchmark itself made no production model switch, application deployment, public benchmark submission or model-weight release. A separately requested post-study chat deployment is recorded below; it is not a forecasting promotion.

## What ran

- Three inexpensive OpenRouter models: DeepSeek V4.1 Flash, MiMo 2.6 Flash and MiMo 2.6 Pro. No Opus.
- Four development rounds: the original ten cases; packaging/provenance repairs and adversarial/decision checks; structured-output and event-routing changes; then explicit card-free approval and a thinking-review ablation.
- One frozen transfer run per MiMo variant, with **16 cases × three arms**. All sources, configuration and fixture hashes were recorded locally before transfer inference. They were checked against the completed reports and unchanged files afterward.
- **21 complete runs, 344 evaluated rows**, plus four saved rows in one stopped DeepSeek run. These are repeated synthetic diagnostics, not 344 independent forecasts. The inferential transfer cohort contains only 16 selected cases.
- **712 attempted provider requests.** Conservative cumulative accounting: **$1.188153**, below the authorized $2 and the runner's stricter $1.80 ceiling. Known provider-reported costs total **$0.430393**; that is not a reconciled invoice and excludes unknown-cost attempts. Reservations for the timeout and interrupted call were retained. [Cost and route audit](cost-summary.json).

Six HTTP 429 responses occurred across the DeepSeek experiments; a later timeout and interrupted request also retained reservations. Four adversarial rows were saved before that run was stopped. DeepSeek was replaced by the other MiMo comparator before transfer, with the deviation recorded in [development-amendment.json](development-amendment.json). This is an availability limitation under the selected route/price restrictions, not evidence that DeepSeek cannot solve the mathematics.

The requests fixed the model ID, disabled provider fallback and capped route prices. These IDs are served aliases, not cryptographically pinned weight revisions. Returned providers were Morph for DeepSeek and Xiaomi for both MiMos.

## Frozen transfer results

Every row below uses the same 16 stipulated cases. Fourteen identify numerical event probabilities; two identify only ranges.

“Headline” means the scalar is within the frozen tolerance, or the runner accepts the required no-point response. “Explicit checks” additionally require the requested structured calculations/actions. Neither column certifies all prose. The frozen counts include the ambiguous auxiliary fixture discussed below.

| Model / arm | Headline | Explicit checks | Mean latency | Conservative cost, 16 cases | Known reported cost |
|---|---:|---:|---:|---:|---:|
| MiMo Flash, direct thinking | 16/16 | 15/16 | 4.66 s | $0.01818 | $0.00414 |
| MiMo Flash, direct output-ceiling control | 16/16 | 15/16 | 4.70 s | $0.01780 | $0.00390 |
| MiMo Flash, harness | 13/16 | 11/16 | 7.38 s | $0.09118 | $0.01351, one unknown-cost call |
| MiMo Pro, direct thinking | 16/16 | 14/16 | 10.06 s | $0.01659 | $0.01153 |
| MiMo Pro, direct output-ceiling control | 16/16 | 16/16 | 11.95 s | $0.02188 | $0.01487 |
| MiMo Pro, harness | 15/16 | 10/16 | 21.13 s | $0.10174 | $0.05878 |

For readers requesting an out-of-ten number, the **headline-only diagnostic scores** are 10/10 for all direct arms, 8.125/10 for the Flash harness and 9.375/10 for the Pro harness. They are not forecasting-skill or full-answer scores. Pro's superficially qualifying 9.375/10 does not override its failed delivery and semantic checks.

The direct-thinking allowance is 8,400 output tokens. The ceiling-control allowance equals the standard harness's full possible output allowance, 23,800 tokens. The harness may make six requests, including a second contract extraction, two reviews and one correction. The earlier 21,600 description omitted the second extraction; old reports remain unchanged. Thinking review raises the total to 33,800, but was not selected for transfer.

**An equal output ceiling is not equal compute or equal cost.** Actual reported completion tokens over 16 cases were 12,629 / 12,313 / 18,082 for Flash and 11,303 / 15,710 / 30,293 for Pro, in table arm order. Harness prompt totals were 120,336 and 130,776 tokens versus 6,058 in each direct arm. There is no basis for attributing every difference between fresh stochastic generations to the ceiling alone.

The pre-transfer freeze identified Flash as the numerical-delivery/cost candidate on development, but promoted **neither** model because semantic defects remained. Pro's direct ceiling-control arm had the best observed explicit-check count on transfer; that is not a post-hoc production promotion or evidence of broad superiority. [Selection and freeze](transfer-freeze.json).

### Uncertainty and loss

Illustrative two-sided Wilson 95% intervals for headline passes are **80.6–100%** at 16/16, **57.0–93.4%** at 13/16 and **71.7–98.9%** at 15/16. These purposively selected synthetic cases are not a random sample of high-stakes decisions. Even a perfect small count does not establish 99% reliability.

For known oracle probability `q`, the loss is `(p-q)^2`: **expected excess Brier**, not realized-event Brier. Missing numerical outputs receive the worst possible loss at that oracle, `max(q²,(1-q)²)`, rather than being discarded or replaced by a purported prediction. The harness-minus-direct point differences are approximately **+0.13921 for Flash** and **+0.03321 for Pro**, driven by missing deliveries. Positive is worse. Paired family-cluster bootstrap intervals across the fourteen numerical cases are approximately **[0, 0.3144]** and **[0, 0.0996]**, respectively; machine-precision terms remain in [scores.json](scores.json). Do not interpret a tiny floating-point endpoint above zero as robust significance.

All issued transfer scalar probabilities were within their declared tolerances. That does not excuse missing answers, incorrect extra calculations or a false explanation attached to a correct number.

## Failures that a flattering scalar score would conceal

The [development audit](development-audit.json) and [transfer audit](transfer-audit.json) give proofs and classifications. They are author-side reviews, not independent human panels.

1. **A correct action with a wrong information value.** Flash direct thinking and its harness chose the correct base action, but valued a perfect report at $2.4 million. The optimal unaided loss is $2 million; loss with perfect information is $0.6 million. The maximum fee is therefore **$1.4 million**, not $2.4 million. The harness approved the wrong baseline calculation. A report purchase can be bad even when the separate contract/wait action has zero regret.
2. **Correct joint probability, false extra arithmetic.** Flash's transfer harness correctly gave 0.1097, then invented marginals 0.94 and 0.84. The correct marginals are 0.1775 and 0.154. Pro gave the right current calculation but incorrect counterfactual bounds after relaxing within-regime independence: **[0.105, 0.154]**, not its [0.1063, 0.1191].
3. **Correct conditional answer, wrong input-relevance claim.** Pro correctly returned 0.72 but said `P(second | no first)` would affect `P(both)`. It does not: `P(both)=0.45×0.72=0.324` regardless of that complementary conditional.
4. **Review creates friction.** Flash had one HTTP 400 and two withheld numerical answers. Pro withheld one. Duplicate-card objections, wrong auxiliary fence names and missing evaluation objects caused additional delivery failures. Pro's missing structured information-value answer was correct in prose; do not describe it as the same economic error Flash made.
5. **The reviewer can itself be wrong.** In development Pro's withheld-answer diagnostics asserted a false bound of 0.63 instead of 0.68. Before transfer, failure text was changed to label objections as **unverified diagnostics**, not established facts or validated corrections. The raw issues remain recorded. This is honest failure presentation, not a mathematical repair.

### Fixture ambiguity and strict-format penalties

The `initial-not-revised` request names `initial` and `revised` without explicitly specifying index levels. Its oracle expects 4.8 and 5.3, while returning event probabilities 0 and 1 is a plausible reading of the prompt. **This auxiliary disagreement is not established model incompetence.** The headline probability 0 is unambiguous. No fixture or grade was rewritten after seeing results.

A labeled **post-hoc sensitivity excluding that case from explicit-check rates only** gives:

| Model | Direct thinking | Direct ceiling control | Harness |
|---|---:|---:|---:|
| MiMo Flash | 14/15 | 15/15 | 11/15 |
| MiMo Pro | 14/15 | 15/15 | 10/15 |

Pro direct thinking also returned action `insured` instead of the requested enum `insure`: a consumer-contract mismatch, not evidence it chose the wrong economic policy. The frozen scorer's missing/malformed-action penalties are conservative **delivery penalties**, not observed realized financial losses. No synonyms or alternate fences were added after the test to improve the scores.

## Repairs supported by reproductions

- Malformed and nested forecast-card packaging no longer bypasses the typed boundary through a permissive card-free review.
- A card-free forecast answer requires explicit `no_point:true`; `valid:true` alone is insufficient. A typed scalar and no-point approval are inconsistent. This flag is a model classification, not proof of mathematical identification.
- Prompts distinguish stipulated assumptions from future observations, check parameter provenance before trusting arithmetic, and classify fully defined hypothetical future events by their resolving event rather than by the presence of arithmetic. These repairs improved development delivery but did not eliminate semantic failures.
- The evaluator locks and validates cumulative budget state, reserves before requests, retains unknown charges, refuses report overwrites and preserves request/response/cost provenance. Its controls do not automatically budget an arbitrary production caller.
- Python scoring now uses source-qualified identities, accepts current ForecastBench-style submissions without a mandatory legacy `direction`, handles Kalshi, rejects ambiguous compact-ID joins, and separates equal-category Brier from pooled Brier and coverage. Missing categories produce null / n/a, not misleading zero scores.
- The calibration harness fixes last-bin ECE handling and a defective bootstrap, clusters paired samples by question, screens training labels by resolution date, and selects the method on development rather than choosing the test winner. A date screen is not proof of historical publication availability. Diagnostic output no longer claims promotion eligibility.

On the original, already exposed ten development cases, Flash harness headline delivery went from **4/10 to 9/10**, and Pro from **9/10 to 10/10** after the first repairs. These unrandomized replays establish useful bug diagnoses, not fresh predictive gains. Later failures and regressions are retained in the numerical release.

## Reproduce and inspect

From the source checkout, without keys or network:

```bash
python benchmarks/analyze.py benchmarks/2026-09-22/numeric-inputs.json \
  --out /tmp/forecast-study-analysis.json
```

Use an output path that does not exist. The generated JSON should match `benchmarks/2026-09-22/scores.json`. The analyzer recomputes numeric and explicit-check grades, Wilson intervals, decision penalties and seeded paired bootstrap summaries. It refuses incomplete runs. Its `substantive_*` field names refer to **selected explicit checks**, not full semantic certification.

Released inputs contain all complete-run numerical rows and an explicit incomplete-run exclusion. Raw requests, provider responses, private operational records and the paid ledger are not released. Numerical rescoring therefore does **not** independently reproduce the original generations or audit every accepted no-point answer. Source/fixture hashes and raw-report hashes support traceability, not independent timing proof. Future reruns of served model aliases can differ.

Verification at the study freeze: **77 Python tests, 36 core tests, 219 private integration tests**, core compilation and private TypeScript checking passed. Actual offline examples, an installed-wheel forecast/seal/resolve/score lifecycle, installed plain-Node package exports and snapshot restoration, and the fail-closed diagnostic path were exercised. That benchmark phase performed no new browser, Docker or production UI acceptance; no claim is made that all upstream collectors are currently operational.

### Post-study provider and chat changes

After the frozen results, the owner separately requested a usable low-cost chat deployment. OpenRouter now defaults to MiMo 2.6 Flash; personal model keys cannot fall through to a hosted balance and their model selection survives stage/workflow overrides. The public SDK includes the funding fix and its rejected-key regression. The forecasting prompt, semantic boundary, calculator, frozen fixtures and scores were not tuned on transfer.

[`post-study-provider-changes.patch`](post-study-provider-changes.patch) records the exact forward change to `model.ts` and `byokContext.ts`. To reconstruct the study source, use a **disposable checkout**, run from its `forecast-stack/` component directory (or the root of an exported component snapshot), reverse the patch below, and verify every SHA-256 entry in `transfer-freeze.json`:

```bash
patch -R -p1 -i benchmarks/2026-09-22/post-study-provider-changes.patch
```

Reversal restores old funding behavior: use it only for the pinned evaluator, not a deployed BYOK service. Current-source tests include the security regression and are expected to reject that old behavior. Numerical rescoring does not require reversal or model calls.

The private chat deployment adds free-account BYOK, visible model/cost guidance and opt-in experimental workflows. It passed **221 private tests**, the production Worker build, a real encrypted database round-trip, model-funding failure smokes, and desktop/mobile settings checks with controlled API fixtures. A real MiMo key-verification completion passed; live runtime metadata reports the cheap model and configured encryption. **Authenticated live chat and saved-key generation were not verified:** the managed browser was signed out, and the user's signed-in Brave session was unreachable because its relay was disconnected. No auth bypass or account impersonation was used.

That one additional provider call cost $0.0000104 conservatively ($0.0000028 reported). The shared ledger therefore ends at **713 requests / $1.1881631 conservative / $0.430395537 known reported**, still below $2. `cost-summary.json` and its ledger hash intentionally describe the earlier **712-request study-end snapshot**, not this later operational check. The application itself has no equivalent global hard-dollar cap; provider key limits and ongoing hosted usage require separate management.

## What would justify a stronger claim

Follow the [model-side and harness-side walkthrough](../../docs/SUPERFORECASTING.md). Freeze an independently witnessed prospective cohort, use the same information and strong direct/crowd comparators, retain all failures, score actual resolutions, audit utility assumptions and replicate untouched. Old checkpoints help only with documented served-weight provenance and genuine point-in-time evidence; an as-of instruction does not erase training knowledge.

Use the [official ForecastBench methodology](https://www.forecastbench.org/about/), [leaderboard caveats](https://www.forecastbench.org/leaderboards/) and [submission rules](https://github.com/forecastingresearch/forecastbench/wiki/How-to-submit-to-ForecastBench). The official difficulty-adjusted Brier Index is not the local raw scorer. FRI's [July 2026 parity assessment](https://forecastingresearch.substack.com/p/ai-models-have-likely-reached-parity) concerns other evaluated systems and an aged human reference cohort; it does not establish this stack's performance. The [official Metaculus bot template](https://github.com/Metaculus/metac-bot-template) is another integration route, subject to current tournament rules and explicit publication authorization.

The project proposes 200 distinct resolved events across three domains and two issue windows plus independent replication. Those are acceptance targets, not universal statistical laws or an official leaderboard requirement. Determine sample size from the smallest useful paired effect and clustering before a funded run.

Prepared with AI assistance. No independent evaluator certified these readiness judgments. The 9/10 aspiration remains unearned.
