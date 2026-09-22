# Gemma historical run: delivery failed before accuracy could be measured

The registered Gemma 3 12B comparison attempted three of twelve planned rows and issued no validated forecasts. Two distinct problems occurred: output-format failures on the May meeting, followed by an upstream rate limit on the June harness draft. The runtime stopped at that rate limit without retrying or changing providers.

This is a negative delivery result, not a measured forecasting accuracy result. Both arms have null issued-only Brier scores and all-case Brier bounds of [0, 1]. No pair is available for an accuracy comparison. See [all planned rows](issued.json) and [computed scores](scores.json).

## Design and historical evidence

[Registration](https://github.com/vaticinus/vati/commit/30a2853239264bb15cb517c41e04980673cfd9ae) preceded inference. The simulated issue date was May 1, 2025; cases were all six remaining scheduled 2025 FOMC meetings. These events had already been examined in an earlier experiment. This is exposed development material, not an untouched holdout or independent replication.

The evidence was the March 19, 2025 Fed statement, recovered from a Common Crawl WARC response captured on April 22, 2025 at 03:27:33 UTC. The actual 2026 retrieval time is recorded separately. The manifest's evidence `fetched_at` is the archiver's original fetch, witnessed by `WARC-Date`, not a backdated claim about our retrieval. The [protocol](protocol.json) records the archive file, offset, length and checksum. Only the statement body was supplied, excluding navigation and unrelated page text. It is a sparse information set, not all information available on May 1.

Gemma's reference repository revision `96b6f1eccf38110c56df3a15bffe176da04bfd80` was last modified March 21, 2025, before the simulated issue date. OpenRouter's actual served weights and transformations are not independently attested. The historical interpretation remains conditional on faithful serving of that reference model.

Both arms used the maintained structured workflow, the same caller-defined event and typed-output draft prompt. Direct applies deterministic validation; harness additionally reviews and may correct once. Neither arm asks a model to extract or rewrite the original event. Realized compute was not equalized. Outcomes were stored separately and were not loaded by the execution command.

## Observed failures

| Row | Result | Diagnostic |
|---|---|---|
| May direct | Abstained in the record | The response contained a binary object with `p_yes: 0.15`, but used a generic JSON fence rather than the required forecast fence. The registered parser did not issue it. |
| May harness | Rejected | The draft omitted `kind`. Its correction added `kind: binary`, but returned a forecast block rather than the requested correction envelope containing `answer` and `spec`. The registered correction handler did not accept it. |
| June harness | Error | OpenRouter returned HTTP 429, identifying DeepInfra's upstream shared pool as overloaded. |
| Remaining nine rows | Not attempted | Registered global stop after the rate-limit response. |

The unaccepted 15% and 25% text values are diagnostic observations only. They are not substituted into the frozen scored record after seeing outcomes. The direct explanation also invoked Fed-funds-futures expectations absent from the supplied statement. Fixing serialization alone would not validate those claims.

The failure distinguishes transport, serialization and substantive reasoning. It does not justify concluding that Gemma cannot forecast, that review improves forecasts, or that another hosted provider would return the same outputs. A new parser or prompting experiment must retain this failed run unchanged and use a separate registration.

## Cost and reproduction

Four requests accounted for **$0.00357750**, including the full reservation for the rate-limited call whose usage was unknown. Original-task accounting reached **$1.35418773 of $2**. The new Gemma authorization was at most $0.10; most of it remains unused. No EC2 resource, Opus run, outreach or leaderboard submission was involved.

Recompute the result without a key or model call, from `forecast-stack/forecast-core`:

```sh
node --experimental-strip-types src/lib/cli.ts benchmark-score \
  --input ../benchmarks/gemma-2025-fomc/issued.json \
  --outcomes ../benchmarks/gemma-2025-fomc/outcomes.json \
  --out /tmp/gemma-historical-scores.json
```

The output path must be new. Public records omit raw paid transcripts and account identifiers. Full traces and their hashes remain private. Protocol preparation, extraction and analysis used AI assistance. One exposed policy-year cluster cannot establish calibration, an independent confidence interval or major-decision readiness.
