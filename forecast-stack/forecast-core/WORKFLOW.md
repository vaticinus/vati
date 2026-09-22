# Run an open forecast

This reference workflow includes research, a personal-key model client, the existing event-preserving harness, immutable revisions, resolution and scoring. It has zero runtime dependencies. It is not evidence of forecasting superiority. The semantic reviewer can approve false or contradictory prose; inspect the supplied evidence and typed result.

## Install and try without spending

Use Node 22.18 or newer. Until a registry release is verified, install from this repository or a reviewed release tarball:

```sh
git clone https://github.com/vaticinus/vati
cd vati/forecast-stack/forecast-core
npm ci
npm run build
node dist/cli.js demo --state ./demo
node dist/cli.js verify --state ./demo
node dist/cli.js score --state ./demo
```

The demo is synthetic. It runs the actual harness, saves a forecast and revision, records a fixture outcome and computes the Brier loss. It makes no network/model calls. A second run refuses to overwrite the history. `npm pack` produces the compiled package; `npm install /absolute/path/to/vaticinus-forecast-core-0.2.0.tgz` installs the `vati` command. `npm run test:install` checks every exported module and the installed command in an isolated directory.

## Collect evidence and issue a real forecast

Create a question with a future deadline and exact settlement rule. See `examples/question.json`; its dates are examples to replace before use. The rule and baseline become immutable once the question ID is used. Use a new ID if the rule changes.

```sh
vati research --source https://your-primary-source.example/report --out packet.json
export OPENROUTER_API_KEY='your-personal-key'
vati forecast --question question.json --packet packet.json \
  --model your-current-openrouter-model-id --max-cost 0.25 --state ./forecasts
```

The example URL and model identifier are placeholders, not working sources or recommendations. For a working keyless source collection, use a data pack below. `forecast` also accepts repeated `--source` arguments, or `--search-kind searxng --search-endpoint YOUR_SEARCH_JSON_ENDPOINT`. Brave requires `--search-kind brave` and `VATI_SEARCH_API_KEY`. A search service's charges are separate from the model budget. Source snippets are discovery hints; the collector fetches the linked pages as evidence and retains failures.

An issued run saves the probability, explanation, typed calculation, exact question, evidence, hashes, actual creation time and issue cutoff. Errors, rejections and abstentions remain records with a null probability. They are not silently changed into 50% forecasts. The direct comparator uses the same prompt and evidence with mechanical validation; the harness adds model review and at most one correction.

The CLI reads only explicitly named environment variables, never a local `.env` or hosted funding fallback. For a compatible endpoint, set `VATI_MODEL_API_KEY` and pass `--endpoint`, `--input-rate` and `--output-rate` in USD per million tokens. HTTPS is required except for explicitly configured localhost providers. Remote model IDs are aliases, not proof of served weights.

### Budget and failure behavior

`--max-cost` is a cumulative ceiling against `STATE/budget.json`, or an explicit `--ledger`. Every call reserves a conservative input byte bound plus framing and the maximum output allowance before sending. Verified usage can lower the reservation; unknown or partial charges retain it. The runtime stops after uncertain transport charges, auth/billing/rate-limit failures, or usage exceeding the reservation. No automatic retries. Do not reset a ledger to recover allowance.

The ledger is exclusively locked during execution. A crash can leave a `.lock`; confirm no process owns it before removing only that stale lock. Keep the ledger itself. Traces contain prompts, answer text, usage and diagnostics, but not the authorization header. Treat traces as potentially sensitive because your question or sources may be private.

Default time limits are 240 seconds total and 60 seconds without a stream chunk. Reasoning chunks keep the stream alive; supported OpenRouter reasoning stays enabled during review. Reasoning-enabled calls receive up to 8,400 output tokens even when a review requests less, to leave room for reasoning and the answer. Other calls follow the stage limit. A complete harness run uses at most four model requests; a direct run uses one. These are output ceilings, not matched consumed compute.

OpenRouter discovery checks current prices and rejects additional positive non-token charges. Routing forbids fallback and caps token prices. For a custom endpoint, the supplied prices and endpoint's token-limit behavior are the operator's responsibility. These reservations are conservative client accounting, not a provider-enforced account spending limit. Use provider-side limits as well when needed.

## Three public-data starters

Write a config like one of these, with dates that are still in the future:

```json
{"kind":"weather","station":"KJFK","target_date":"2026-09-24","threshold_c":25}
```

```json
{"kind":"macro","series":"LNS14000000","target_month":"2026-09","resolution_date":"2026-10-15","threshold":4.5,"unit":"percent"}
```

```json
{"kind":"world","start":"2026-09-24T00:00:00Z","end":"2026-10-01T00:00:00Z","minimum_magnitude":6}
```

```sh
vati data-fetch --input config.json --out ./source-pack
vati forecast --question ./source-pack/question.json --packet ./source-pack/packet.json \
  --model your-current-openrouter-model-id --max-cost 0.25 --state ./forecasts
```

The first command uses free public endpoints and saves raw responses plus the derived question and evidence. The second spends model credits. The raw responses are in `pack.json`; packaged source fixtures split them into separately hashed `snapshot-N.json` files. `examples/replay-packs.mjs` replays those real fixtures through the adapters without network or models.

| Pack | Baseline and rule | Limits and source terms |
|---|---|---|
| NWS station temperature | Smoothed frequency of threshold exceedance over eligible days in the previous 10 UTC days. Each day needs observations in 18 distinct hours. Resolve on observed station temperature, with the stated coverage and capture rule. | Short-window comparator, not an official probability forecast. Observations can arrive late; empty days are excluded. [NWS API terms/documentation](https://www.weather.gov/documentation/services-web-api): free open data with rate limits. |
| BLS monthly series | Smoothed frequency over available values among the latest 24 reference months. Missing months and footnotes are retained. Resolve the target period in a snapshot captured on the specified UTC date. | Current vintage, not first-release history. Confirm units and schedule. A missing value remains unresolved. [BLS API](https://www.bls.gov/developers/api_signature_v2.htm), [public-domain information](https://www.bls.gov/bls/linksite.htm). |
| USGS magnitude threshold | Homogeneous Poisson frequency from 90 preceding days; at least one matching earthquake in a specified future window. Settle on the catalog snapshot 7–8 days after that window. | Events cluster and magnitudes change. This is a natural world-event example, not a geopolitical model or warning system. [Catalog API](https://earthquake.usgs.gov/fdsnws/event/1/), [USGS terms](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits). |

These source terms apply to their data; the code's MIT license does not relicense third-party material. Our fixtures contain government API records, not images or third-party articles. Their collection times are recorded in each pack. No source is labeled published at its retrieval time. Measurement time, reference month and latest catalog update are not publication dates. The live packs have unknown publication timestamps and cannot satisfy the historical replay archive gate.

## Update, resolve, score

```sh
vati refresh --state ./forecasts --model your-current-openrouter-model-id \
  --max-cost 0.25 --interval-hours 24
vati resolve --state ./forecasts --resolution resolution.json
vati score --state ./forecasts --policy first --out scores.json
```

`refresh` is one finite batch of due, unresolved, live questions for that model. It re-fetches the previous source URLs and links each new run to its predecessor. It does not install a scheduler, send alerts or place trades. Research failures are retained in `refresh-attempts.jsonl`. Evidence stored in structured packs should be recollected with their adapter before issuing a manual revision; generic refresh only revisits URLs.

A resolution JSON contains `question_id`, `question_hash`, `outcome` (0/1), `observed_at`, `recorded_at`, `note`, and `source: {url,text,sha256}`. Numeric questions also need `value` and `unit`; the exact registered comparator is enforced. Obtain the question hash with the exported `questionHash` function. The resolver verifies integrity and comparator consistency, not whether a cited publisher really said the submitted text. The operator must check the source and the full settling rule. Do not score missing data as NO.

History is append-only with a hash chain and an exclusive write lock. Export or publish the head to an independent timestamp witness before outcomes to support a public timing claim; a local hash alone proves no publication time. Revisions supersede, never overwrite. Choose `first` or `latest` before inspecting outcomes. The score output retains unresolved, failed and missing predictions and separates model, arm and replay mode.

## Register and run a model × harness comparison

Build an input with `name`, `mode`, `models`, `arms: ["direct","harness"]`, and `cases`. Each case contains a question, complete evidence packet, `as_of` timestamp and underlying-event `cluster`. Keep resolutions in a separate file that is never loaded by inference.

```sh
vati benchmark-freeze --input study.json --out frozen.json
vati benchmark-run --input frozen.json --out issued.json --max-cost 0.50 --ledger ./study-budget.json
vati benchmark-score --input issued.json --outcomes outcomes.json --out scores.json
```

Freezing validates and hashes the cohort, evidence and current executable modules. Changed code requires a new registration. Publish the manifest with your selection rule, provider configuration, budget, model revision evidence, baselines and promotion criterion before inference. A manifest is a content seal, not an independent timestamp. Do not include API keys. Preserve the CLI invocation and ledger with the output; inspect trace privacy before publishing.

The runner allocates every registered cell before execution, alternates arm order by case, records elapsed time/accounted cost, and stops globally when the runtime reports an uncertain-charge failure. Remaining cells stay `not_attempted`. Scores include issued-only Brier/log loss, calibration bins with sample sizes, matched baselines, every status count, missing-loss bounds, and paired harness-minus-direct Brier. The cluster bootstrap requires at least five event clusters; it is not a guarantee of independence or adequate power. Incorrect certainty has infinite log loss. An issued-only win with selective missingness is insufficient for promotion.

For historical replay, use `mode: "historical_replay"` and a `checkpoint` entry per model with `revision`, `released_at` and `provenance`. Release must predate every simulated issue date. Each evidence snapshot must have a publication date and capture time no later than that issue date. These assertions still need independent verification: an operator can forge metadata, and a hosted alias cannot attest its weights. The existing published FOMC experiment has explicitly weaker archived-source guarantees; this runner does not retrofit or overwrite that study.

Exposed fixtures are development material. Keep event clusters out of both development and holdout sets, register the holdout before tuning, and use fresh short-horizon prospective questions for promotion. The old-checkpoint studies and prior pilots remain versioned in `../benchmarks/`; later software fixes do not change their outcomes.

## Embedding and scope

Compiled exports: `/engine`, `/harness`, `/snapshot`, `/prompt`, `/provider-context`, `/runtime`, `/evidence`, `/workflow`, `/benchmark`, `/data-packs`. `MeteredRuntime.complete` supplies the injected completion interface; `runForecast` accepts it without importing a funding fallback. The legacy `/prompt` provider path remains for compatibility and is not the metered CLI.

The URL collector rejects obvious private addresses and rechecks redirects. It is intended for a trusted local operator, not as a hardened multi-tenant URL-fetching service; DNS rebinding and infrastructure egress policy need separate controls before hosting arbitrary users. There are no accounts, private workspaces, uptime promises or automatic public submissions in this package. Managed operations can be sold around the same open code without making these capabilities secret.
