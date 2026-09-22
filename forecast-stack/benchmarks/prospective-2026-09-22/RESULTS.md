# Prospective pilot: MiMo Pro, Gemini Flash and DeepSeek Flash

**Issued 22 September 2026. Outcomes unresolved. No forecasting winner.** This is three purposively selected, correlated US macroeconomic events—not a representative benchmark or a calibration study. Kimi was not included. Exact model IDs, price caps, evidence and settlement rules were registered before inference in [commit 581be0a](https://github.com/vaticinus/vati/commit/581be0a008885a73fd3740a22e12a8b4a3e3edea).

The complete public numerical record is [issued.json](issued.json). It retains all 18 planned rows, including nine failures. The shared private ledger and raw provider transcripts are not published. Selected diagnostic observations are included; SHA-256 receipts commit to the retained private report and ledger. GitHub observation is not an independent, irrevocable timestamp or a replication.

## Issued direct probabilities

| First-publication event | Scheduled release | MiMo v2.6 Pro | Gemini 3.8 Flash | DeepSeek v4.1 Flash |
|---|---|---:|---:|---:|
| August JOLTS openings ≥7,300 thousand | 29 September | 45% | 47% | 50% |
| August core PCE published monthly change ≥0.3% | 30 September | 32% | 40% | 30% |
| September nonfarm payroll monthly change ≥100,000 | 2 October | 35% | 32% | 55% |

The fixed baseline is 50% for every event. [cohort.json](cohort.json), not this abbreviated table, controls reference months, seasonal adjustment, rounding, first-release vintages and unresolved cases. No outcomes have been assigned.

All three direct arms issued 3/3 estimates. All three harness arms issued 0/3. There are **zero complete direct–harness pairs**, so this run cannot estimate a paired harness accuracy increment.

## Execution failures and protocol deviation

- MiMo and DeepSeek each completed three contract calls, then hit the 90-second response-body timeout on all three draft calls. HTTP headers had already returned status 200; there was no usable draft body.
- Gemini's three contract calls returned HTTP 400. The runner did not retain the provider error payload, so the exact cause is not established. A configuration incompatibility was found afterward: the retrieved OpenRouter catalog marks Gemini 3.8 Flash reasoning as mandatory, while the runner requested `reasoning.enabled:false` for contract/review/correction stages. [OpenRouter documents mandatory reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens). This is a plausible explanation, not a recovered error message or evidence of inferior forecasting accuracy. Its direct calls succeeded. A future registration must use supported model-specific reasoning settings and retain error diagnostics; this historical configuration must not be reused.
- **The registered global stop-on-transport-error rule was violated.** The runner incorrectly treated receipt of an HTTP status as evidence against a transport failure. The first violation occurred on row 2; subsequent rows continued. Treat the entire pilot as exploratory, not a protocol-compliant confirmatory comparison.
- No failed row was retried, replaced, silently removed or imputed as 50%. No new paid calls were used to repair the runner.

A post-run safety fix tracks completion of response-body decoding separately from HTTP headers. An offline CLI regression reproduced two attempted requests before the fix and one afterward, with the unknown charge still reserved and the lock released. Run it from `forecast-stack/forecast-core`:

```sh
node --experimental-strip-types --test test/prospective-transport.test.ts
```

The frozen protocol and cohort were **not** rewritten to match the repair. Consequently the current runner deliberately refuses that historical protocol's source pin. For an inspection-only view of the exact registered code, use `git show 581be0a008885a73fd3740a22e12a8b4a3e3edea:forecast-stack/forecast-core/scripts/prospective_eval.mts`. Do not execute that buggy historical runner. Any future paid study requires a newly registered packet, current source hashes, ledger checkpoint and spending authorization; do not reset or rebase this ledger.

## Cost—not an invoice

24 attempted paid calls added a conservative **$0.13487275**. Known provider-reported cost is **$0.0215920998**; nine calls lack a known charge and retain their full reservations. The shared task total is **$1.32303585 across 737 calls**, below both the authorized pilot cumulative ceiling of $1.68816310 and the original $2 ceiling. The unused allowance was not spent.

Direct-arm conservative costs for the three events were $0.00393890 for MiMo, $0.01303800 for Gemini and $0.00176010 for DeepSeek. These tiny, sparse-packet costs are not estimates for the full application or for other provider routes.

## Qualitative audit, not a ranking

The post-hoc, unblinded review found reasoning limitations even in valid JSON answers:

- Gemini called monthly core PCE moving from +0.1% to +0.2% “mild deceleration”; that quoted monthly rate increased.
- DeepSeek called the PCE release “a month away”; it was scheduled eight calendar days after issuance.
- DeepSeek's payroll explanation calculated approximately 33–41% under several normal assumptions, then explicitly overrode to 55% on judgment without quantifying the adjustment. This is not an arithmetic error, but it limits reproducibility.
- MiMo asserted payroll sampling error of ±100,000 or more without that empirical quantity appearing in the supplied evidence packet.

Model restatements also vary in completeness. Resolve against the frozen cohort, not a shortened model restatement. These observations are not an accuracy score, proof of calibration, or evidence that any system is ready for major decisions.

## Resolution and reproducible scoring

After each release, archive the original official release bytes, URL, retrieval time and SHA-256. Record the exact originally printed quantity, units and rounding before assigning the outcome. Do not substitute a subsequently revised series. Delays remain pending for 30 calendar days after the scheduled release; unavailable or ambiguous originals remain unresolved, not NO. Preserve `issued.json`; put resolution evidence in a separate dated record.

Follow [protocol.json](protocol.json):

1. For each model/arm, report issued coverage against all three registered events. Unresolved outcomes remain excluded from resolved-event scores, with their count explicit.
2. On resolved issued forecasts, compute Brier loss `(p-y)^2`, compared on the same events with the fixed 0.5 baseline. Its Brier loss is 0.25 for either binary outcome. Do not manufacture harness probabilities or a paired comparison where no pair exists.
3. For missing forecasts on resolved events, report all-case Brier bounds using missing loss in `[0,1]`. Report issued-only scores separately; these are not comparable to full-coverage scores without the missingness qualification.
4. Compute log loss without hiding infinite losses at incorrect probabilities of exactly zero or one. Report per-event results; three correlated events cannot support reliable calibration or significance claims.
5. At registered action thresholds 0.25, 0.5 and 0.75, report the hypothetical action utility `y-threshold` when acting and zero when abstaining. The protocol did not specify equality handling: report both `p>threshold` and `p>=threshold` policies, rather than selecting the better result afterward. Missing forecasts are not automatic abstentions. This is a toy decision evaluation, not investment returns.
6. Keep this protocol deviation attached to every future score table. Independent replication and a materially larger, diverse prospective cohort are still required before any readiness promotion.
