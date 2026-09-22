# Contributing

This repository contains two independent packages. The guidance below applies to **Beyond Brier** (`beyond_brier`, root tests, paper and evaluation). For forecasting tools, collectors, the typed core or the workbench, use the [Forecast Stack contribution guide](forecast-stack/CONTRIBUTING.md). Keep changes and empirical claims scoped to the component they affect.

## What belongs here

Beyond Brier is a **ruler, not a racehorse**. It scores forecasters against a prior. It does not forecast anything itself. Good contributions:

- bug fixes, especially anything that makes a reported number wrong
- better tests, more adversarial tests, edge cases that break the math
- a new strictly proper scoring rule, or a better inference method, with a citation
- documentation fixes and clearer examples
- replications: you ran the numbers and got something different, open an issue

## What does not belong here

- a forecasting model or data feed inside `beyond_brier`. Propose those in the separate `forecast-stack/` component instead.
- a new metric that is not strictly proper, or a skill-score *ratio* (we aggregate the difference of two proper scores for a reason, see `edge.py`).

If you are not sure, open an issue before writing code.

## Setup

```bash
git clone <your fork>
cd vati
pip install -e ".[dev]"
pytest -q
```

To exercise the ForecastBench integration test and regenerate the figures, fetch the public data first:

```bash
python -m data.fetch
pytest -q                       # now runs the integration test too
python examples/make_figures.py
```

Root `pytest` collects Beyond Brier's `tests/` only. Forecast Stack has separate Python and Node checks; run its [release gate](forecast-stack/docs/RELEASE.md#local-gate) from `forecast-stack/`. The root CI workflow runs both components.

## Before you open a PR

- `pytest -q` is green.
- If you changed a number that appears in the README or the paper, say so in the PR and show the new output.
- New behavior comes with a test. The whole point of this project is that the numbers are trustworthy, so an untested change is a hard sell.
- Keep the diff focused. One idea per PR.

## Reporting a bug in the math

These are the most valuable reports. Include the smallest input that reproduces it, the number you got, and the number you expected with a short reason. "This edge looks too high given the CI" is a perfectly good start.

## License

Beyond Brier and shared repository files remain Apache-2.0. Contributions under `forecast-stack/` are MIT, as specified by that component's `LICENSE`. By contributing, you agree to the license of the component you change; adding a component does not relicense existing work.
