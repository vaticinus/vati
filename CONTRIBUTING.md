# Contributing

Thanks for taking a look. This is a small, focused project and it intends to stay that way. The fastest way to get a change merged is to know what belongs here and what does not.

## What belongs here

This is a **ruler, not a racehorse**. It scores forecasters against a prior. It does not forecast anything itself. Good contributions:

- bug fixes, especially anything that makes a reported number wrong
- better tests, more adversarial tests, edge cases that break the math
- a new strictly proper scoring rule, or a better inference method, with a citation
- documentation fixes and clearer examples
- replications: you ran the numbers and got something different, open an issue

## What does not belong here

- a forecasting model, data feeds, or anything that helps you *be* a better forecaster. That is out of scope on purpose.
- a new metric that is not strictly proper, or a skill-score *ratio* (we aggregate the difference of two proper scores for a reason, see `edge.py`).

If you are not sure, open an issue before writing code.

## Setup

```bash
git clone <your fork>
cd beyond-brier
pip install -e ".[dev]"
pytest -q
```

To exercise the ForecastBench integration test and regenerate the figures, fetch the public data first:

```bash
python -m data.fetch
pytest -q                       # now runs the integration test too
python examples/make_figures.py
```

## Before you open a PR

- `pytest -q` is green.
- If you changed a number that appears in the README or the paper, say so in the PR and show the new output.
- New behavior comes with a test. The whole point of this project is that the numbers are trustworthy, so an untested change is a hard sell.
- Keep the diff focused. One idea per PR.

## Reporting a bug in the math

These are the most valuable reports. Include the smallest input that reproduces it, the number you got, and the number you expected with a short reason. "This edge looks too high given the CI" is a perfectly good start.

## License

By contributing you agree your work is licensed under Apache-2.0, the same as the rest of the project.
