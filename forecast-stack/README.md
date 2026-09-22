# forecast-stack

**Build forecasts people can inspect, reproduce, and score.**

An open toolkit from Vaticinus, maintained in [`vaticinus/vati`](https://github.com/vaticinus/vati/tree/main/forecast-stack): public-data collectors, Python baselines, a typed probability engine, an event-contract LLM review harness, and tamper-evident forecast records. Bring your own model. Keep your data. Fork this component, self-host it, or use it commercially under MIT.

The goal is better forecasts at a measured cost, not more agent calls. We have **not established an accuracy advantage over strong direct-model or human baselines**. Help test that claim, including by disproving it.

## Run the complete forecasting workflow

The [reference CLI](forecast-core/WORKFLOW.md) now connects public-source research, personal-key model execution, the review harness, immutable forecast revisions, resolution and scoring. It includes frozen model × harness comparisons and replayable NWS, BLS and USGS data packs. Start with its offline demo before spending model credits. The code is MIT; source data retains its own terms.

## Start without keys

Python 3.10+; no runtime dependencies:

```bash
git clone https://github.com/vaticinus/vati
cd vati/forecast-stack
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python examples/quickstart.py
python -m pytest
```

The quickstart uses a **synthetic offline provider**, writes only to a temporary directory, and shows forecast → seal → resolve → score. Its probabilities are demonstration values, not predictions about the world.

Node 22.18+; no npm install needed:

```bash
npm --prefix forecast-core test
npm --prefix forecast-core run example
npm --prefix forecast-core run eval:list   # lists synthetic reasoning cases; no model calls
```

Try the same engine in a browser:

```bash
node --experimental-strip-types space/server.mts
# http://localhost:7860
```

Edit a conditional partition, apply Bayes' rule, or change a numeric threshold. The demo computes the result and downloads the actual saved snapshot. It uses no LLM, keys, external requests, or persistent user storage. [Hugging Face deployment](docs/RELEASE.md) uses this exact app.

## What is in the repo

| Path | What you get |
|---|---|
| `forecast_stack/feeds/` | 64 collector modules spanning macro, energy, science, markets and physical constraints |
| `forecast_stack/model/` | Quantitative, weather, crowd-aware and LLM baselines; inspect assumptions before use |
| `forecast_stack/eval/` | Brier scoring, coverage accounting, recall probes and evaluation utilities |
| `forecast_stack/harness/` | Typed records, question/evidence schemas, hash-chain verification and linked outcome scoring |
| `forecast-core/` | TypeScript binary, conditional, Bayesian, normal and growth models; contract extraction, skeptical review, saved snapshots and budgeted evaluator |
| `space/` | Keyless probability workbench, deployable as a Hugging Face Docker Space |
| `examples/` | Runnable offline Python lifecycle and TypeScript calculation examples |
| `scripts/release.py` | Explicit-allowlist release staging, credential guards and SHA-256 manifests |

Python and TypeScript are independent packages in one community repo, not two implementations with identical behavior. Python's LLM forecaster is a simple baseline, not the hosted chat. The TypeScript core carries the chat's mathematical and event-contract machinery, not its auth, billing, retrieval service or UI. [Architecture and boundaries](docs/ARCHITECTURE.md).

## Use your model and data

```bash
FORECAST_STACK_MOCK=1 forecast-stack providers
forecast-stack feeds list
# Network request to the named public source; may be large or rate-limited:
FORECAST_STACK_DATA=./data forecast-stack feeds run ecb_fx

# Paid model calls: configure a key and a currently served model explicitly.
export FORECAST_STACK_PROVIDER=deepseek
export FORECAST_STACK_MODEL=your-provider-model-id
forecast-stack forecast --question 'Will the stated event occur?' \
  --asof 2026-09-21 --resolution-criteria 'Your complete dated settling rule' \
  --context evidence.txt --n 1
```

See `.env.example` for provider settings. Python supports OpenAI-compatible endpoints and Anthropic. The TypeScript harness supports its own provider chain or an injected completion function; see [its README](forecast-core/README.md). Credentials stay server-side. Never put keys into a demo URL or browser bundle.

Collectors use the current working directory's `data/`, overridden by `FORECAST_STACK_DATA`. They are network adapters, not a bundled dataset. No claim is made that all 64 upstream endpoints are currently available, still keyless, or licensed for redistribution. Observation dates are not publication dates; revised series require vintages for historical evaluation.

## What the checks do not prove

- A recall probe reveals **observed knowledge**, not a model's maximum training cutoff. Failure to recall cannot clear a backtest. `outcome_after_checkpoint` checks dates against independently documented checkpoint provenance; it does not audit evidence leakage.
- A local hash chain detects changes relative to a retained head. An author can rebuild it and edit local or git timestamps. Publish the head to an independent witness before outcomes to support a timing claim.
- A typed probability is mathematically consistent with its inputs. The inputs can still be wrong. An LLM reviewer can approve a false explanation.
- Synthetic reasoning checks are not resolved-event forecasting scores. Historical numbers without their release-scoped inputs and protocol are not advertised as reproduced results here.
- The Python baseline clips probabilities and blends samples; sample disagreement is not calibrated uncertainty. The typed core instead preserves supported extremes. Neither is certified financial advice or a demonstrated trading edge.

## Measure and improve

Follow the [superforecasting evaluation walkthrough](docs/SUPERFORECASTING.md): separate model quality from harness integrity, run a capped same-model comparison, inspect decision errors, freeze a transfer cohort, and move to prospective scoring. It includes the current ForecastBench and Metaculus integration routes, proper-score and uncertainty guidance, and the limitations of historical tests with old checkpoints.

The [local study protocol](benchmarks/2026-09-22/protocol.json) defines the acceptance gates before evaluation. A synthetic pass rate is not a future-event accuracy score. An engineering release can be useful before a superforecaster claim is justified.

Read the [September 2026 experiment and failure audit](benchmarks/2026-09-22/RESULTS.md). Released numerical rows can be rescored without keys. The results distinguish checked delivery from correct explanations, and neither from prospective forecasting skill.

## Build with us

Start with [CONTRIBUTING](CONTRIBUTING.md) and the [contributor opportunities](docs/ROADMAP.md). Useful work includes a reproducible bug, a source-vintage audit, a stronger baseline, a clearer demo, or an honest negative result. You do not need a paid API account to contribute.

Contributors keep copyright, receive named release credit, and can propose component stewardship through reviewed work. There is no copyright-assignment CLA and no paid feature required to reproduce the offline path. Paid bounties or compute grants exist **only when an issue names an approved budget and terms**; none are funded by this release. [Governance](GOVERNANCE.md) · [Conduct](CODE_OF_CONDUCT.md) · [Security](SECURITY.md).

## Release status

Source lives in the existing Vaticinus repository alongside the independent Apache-2.0 Beyond Brier package. Package-registry and Hugging Face publication are separate steps. The [release runbook](docs/RELEASE.md) defines the component export boundary, verification gates and first forward evaluation. [Changelog](CHANGELOG.md). Cite the version or commit you actually used via `CITATION.cff`.

## License

MIT for this component's code and original documentation, not a relicensing of the parent repository's Beyond Brier package. Third-party data, model weights and provider outputs remain under their own terms. This release includes derived synthetic numerical measurements, but no raw provider response dumps, operational datasets or model weights.
