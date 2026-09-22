# A repaired run, one usable pair

The registered comparison stopped after three of twelve planned rows. MiMo Flash issued a direct forecast and a reviewed forecast for August job openings. DeepSeek's draft response timed out; the remaining nine rows were not attempted. No row was retried or replaced.

The job-openings outcome is scheduled for September 29, 2026. There is no accuracy result yet.

## What issued

| Model | Event | Direct | Harness | Status |
|---|---|---:|---:|---|
| MiMo v2.6 Flash | August job openings ≥7,300 thousand | 40% | 42% | One complete pair; unresolved |
| DeepSeek v4.1 Flash | Same event | Not attempted | Missing | Draft response-body timeout |
| Both models | August core PCE; September payrolls | Not attempted | Not attempted | Global stop rule |

Coverage is **2/12 planned forecasts**, including **1/6 planned pairs**. Counting only attempted rows would conceal most of the missing cohort. All twelve rows, including failures and unattempted work, are retained in [`issued.json`](issued.json).

The MiMo direct forecast took 16.7 seconds and conservatively accounted for $0.00036848. Its harness forecast used three calls, took 83.1 seconds and accounted for $0.00222670. That is more computation and a two-percentage-point change, not evidence of a better forecast.

## What the run establishes

The [registration](https://github.com/vaticinus/vati/commit/c6145f6d3e2372def3149554ac3fc7561781cc84) was published and its remote protocol bytes checked before inference. The runner used the repaired transport-stop handling and registered reasoning capabilities. Contract, draft, review and correction allowances were 2,200, 8,192, 3,000 and 5,000 output tokens; a possible second review brings the maximum aggregate harness allowance to 21,392, equal to the direct ceiling. Realized compute was not equalized.

DeepSeek's contract completed through Morph. The next request returned HTTP 200 headers but did not finish reading its draft response within the 90-second request deadline. The runner stopped the entire study and retained the unknown charge reservation. There were no subsequent requests. This follows the registered rule; unlike the earlier pilot, it is not a stop-policy deviation. A timeout does not establish inferior forecasting skill.

This new run reuses all three questions, thresholds and archived evidence packets from the [earlier pilot](../prospective-2026-09-22/RESULTS.md). The operator had inspected earlier model answers. It is not an unseen-question holdout, a new independent event sample, or independent replication. Changes in models, stage allowances and compatibility also prevent attributing any difference to one repair.

## Review still missed substantive problems

Post-hoc, unblinded inspection found these defects. They are recorded rather than used to exclude an inconvenient forecast:

- **Deadline ambiguity:** the MiMo harness contract adds “originally published on or before 2026-09-29,” then also allows a 30-day publication delay. The original event does not impose that extra deadline. The frozen cohort governs settlement, not the model's contradictory restatement.
- **Unsupported historical claims:** both MiMo answers motivate judgmental volatility assumptions with claims about typical JOLTS fluctuations that the supplied packet does not establish. Labelling a distribution judgmental does not turn its factual justification into evidence.
- **Missing is not unpublished:** the reviewed explanation calls July's precise table value “unpublished.” It was not supplied in the packet; that does not establish that BLS had not published it.

The harness returned an empty issue list. Its internal review therefore must not be treated as an independent correctness certificate. No prompt or source was changed during the registered run to rescue these answers.

## Scoring and spending

Brier score, log loss and realized decision utility remain **uncomputed**. The first official releases settle the original events, excluding later revisions. Delays remain pending for 30 calendar days, then unresolved/void rather than NO. See [`cohort.json`](cohort.json) for the full rules.

At resolution, report paired Brier differences separately by model, the declared 50% reference, issued coverage on the full twelve-row plan, and all-case loss bounds for missing forecasts. Never insert 50% for a failed row. Three correlated macro events—and only one issued pair—cannot establish calibration or readiness for major decisions.

This run made **six paid requests**. Additional conservative accounting was **$0.01142313**; known provider-reported charges were **$0.0026014408**, with one unknown-charge timeout retaining its reservation. The original task's cumulative accounting is **$1.35061023 of $2** across 811 requests. These figures are not an invoice.

Full paid request/response traces remain private. Public records contain issued probabilities, assumptions, failure status, timing, accounting and hashes of the private report and ledger. They contain no credentials.

## Inspect the registration without spending

From `forecast-stack/forecast-core`:

```sh
node --experimental-strip-types scripts/prospective_eval.mts \
  --study benchmarks/paired-2026-09-22
```

This validates the frozen source and prints the design without contacting a model. A future paid rerun needs a new registration and budget snapshot; it must not overwrite these forecasts or reuse this run's pre-inference ledger hash.

Protocol preparation and analysis used AI assistance. There was no Opus run, external leaderboard submission or outreach. The result is a small, incomplete prospective record—not a superforecaster claim.
