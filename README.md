# Vati

**Build AI forecasters. Make their reasoning inspectable. Measure their edge.**

An open forecasting toolkit from Vaticinus: evidence, probability models, LLM review, and a record you can score against reality.

**[Try the live workbench](https://huggingface.co/spaces/vaticinus/forecast-stack)** · [Get started](#run-it-yourself) · [Evaluation guide](forecast-stack/docs/SUPERFORECASTING.md) · [Download the release](https://github.com/vaticinus/vati/releases/tag/forecast-stack-v0.1.0)

## A forecast should survive more than a good conversation

“Likely” is not enough when a decision has a deadline and a cost.

What exactly has to happen? What evidence supports the estimate? Which assumptions move the probability? And once the outcome arrives, did the forecast beat a simpler alternative?

Vati gives developers and researchers the tools to work through those questions. Use a quantitative baseline or bring an LLM. Define the event, challenge the reasoning, compute the probability from explicit assumptions, and keep the original forecast for evaluation.

```text
Define the event  →  Gather dated evidence  →  Form a forecast
                                                    ↓
Compare with a baseline  ←  Resolve and score  ←  Save the record
```

The goal is AI that earns trust through a forecasting record, not the confidence of its prose.

## What you can build with it

| Task | Tools in Vati |
|---|---|
| **Ground a forecast in evidence** | Public-data collectors across economics, energy, markets, science and physical constraints; quantitative, weather and crowd-aware baselines. |
| **Put an LLM's reasoning under review** | A TypeScript harness that extracts the event contract, reviews proposed answers and checks supported probability models. Bring your own completion function or use supported providers. |
| **Make the assumptions inspectable** | Binary judgments, conditional scenarios, Bayesian updates, normal thresholds and growth models, with saved computational snapshots. |
| **Find out whether it helped** | Forecast records, outcome scoring, direct-model comparisons, cost accounting and reference-relative evaluation. |

[Explore the forecasting toolkit](forecast-stack/README.md) · [Use the TypeScript core](forecast-stack/forecast-core/README.md) · [Understand the architecture](forecast-stack/docs/ARCHITECTURE.md)

## Try one calculation before connecting a model

Suppose a project has a 60% chance of finishing on time in one scenario and a 10% chance in another. Give those scenarios weights of 30% and 70%:

| Scenario | Scenario weight | Chance of finishing | Contribution |
|---|---:|---:|---:|
| A | 30% | 60% | 18% |
| B | 70% | 10% | 7% |
| **Total** | **100%** | | **25%** |

Change the assumptions in the **[live probability workbench](https://vaticinus-forecast-stack.hf.space)** and download the computed snapshot. You can also explore Bayesian updates and threshold models.

No login or API key needed. This demo is a calculator over stated assumptions, not an LLM making a real-world prediction.

## Run it yourself

**Start with the workbench.** Node 22.18 or newer; no npm install required:

```sh
git clone https://github.com/vaticinus/vati.git
cd vati/forecast-stack
node --experimental-strip-types space/server.mts
# Open http://localhost:7860
```

**Run an offline forecast lifecycle.** From `vati/forecast-stack/`, with Python 3.10 or newer:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python examples/quickstart.py
```

The Python example creates a synthetic forecast, seals its record, adds an outcome and scores it. It uses temporary storage and makes no paid model calls.

Ready to use your own evidence and model? Follow the [provider setup](forecast-stack/README.md#use-your-model-and-data). Python baselines and the TypeScript harness are independent components; this repository is not a one-command copy of the hosted chat service.

## Does the harness actually improve forecasts?

That is the question the project is testing, not a result we assume.

The [evaluation walkthrough](forecast-stack/docs/SUPERFORECASTING.md) separates three things: **model skill**, **harness reliability**, and **decision usefulness**. It explains how to freeze a comparison, retain failed attempts, measure proper scores and costs, and move to external benchmarks.

The current record is public:

- **[Historical replay](forecast-stack/benchmarks/historical-2025-fomc/RESULTS.md):** 32 forecasts across all eight 2025 FOMC meetings using older checkpoints. Mixed harness results, with material reasoning failures. The report documents retrospective selection and checkpoint-serving uncertainty.
- **[Prospective pilot](forecast-stack/benchmarks/prospective-2026-09-22/RESULTS.md):** future macro events, issued direct forecasts, and every failed harness attempt retained. No paired accuracy result is available.
- **[Synthetic red-team study](forecast-stack/benchmarks/2026-09-22/RESULTS.md):** controlled reasoning problems, corrections and failure analysis. These are engineering diagnostics, not a real-world leaderboard.

**No reliable harness advantage or major-decision readiness has been established.** Correct arithmetic cannot rescue bad evidence, and a model reviewer can miss a false explanation. The useful contribution is a system whose assumptions and failures can be examined and improved.

## Beyond Brier

A forecast can score well by repeating the market. Did it add information?

The independent **[Beyond Brier package](BEYOND_BRIER.md)** measures performance against a declared reference using differences of proper scores. Its original paper, figures, examples and leaderboard documentation have their own home. It is the evaluation component, not the whole Vati project.

Installing the root Python package installs Beyond Brier. Install from `forecast-stack/` for the forecasting toolkit.

## Help build the forecasting record

Bring a stronger baseline, a reproducible failure, a dated evidence source, or an independent replication. You do not need a paid API account to contribute.

[Contributing](forecast-stack/CONTRIBUTING.md) · [Open work](forecast-stack/docs/ROADMAP.md) · [Security](forecast-stack/SECURITY.md)

**Licenses:** Forecast Stack is [MIT](forecast-stack/LICENSE). Beyond Brier retains [Apache-2.0](LICENSE). Model weights and third-party data are not bundled; their own terms apply.

