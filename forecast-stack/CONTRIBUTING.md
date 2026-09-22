# Contributing

The unit of progress is a forecast improvement someone else can reproduce, a bug removed, a usable source, or a negative result that rules out wasted work.

## First contribution

1. Run the offline quickstart in the README and the suite for the component you want to change.
2. Choose a bounded task in [the roadmap](docs/ROADMAP.md), or open an issue describing an observed failure. Check for an existing issue before starting a large change.
3. State the consumer-visible result and how to verify it. For experiments, freeze the hypothesis, cohort, budget and comparison before running.
4. Open a small PR with reproduction commands and results. Never upload keys, private conversations, contact lists, licensed corpora or outcome-contaminated claims.

Python: `python -m pip install -e '.[dev]' && python -m pytest`.
TypeScript: `npm --prefix forecast-core test` (Node 22.18+).
Demo: `node --experimental-strip-types space/server.mts`.
Release boundary: `python scripts/release.py --check`.

No API keys, paid model calls, GPU or external data are needed for these checks. Tests must not require network access. Run an upstream collector manually only when you intend to access that source.

## Contribution tracks

- **Data:** use the existing collector format and `forecast_stack.config.DATA_DIR`. Preserve units, source URLs, missingness, source terms and publication/vintage dates where available. Never relabel an observation date as a publication date. Invoke collectors with `forecast-stack feeds run NAME` or `python -m forecast_stack.feeds.NAME`.
- **Engine:** preserve event scope, threshold equality, units and snapshot read semantics. A mathematical fix needs a counterexample that fails before the fix. Do not add a probability floor or silently normalize an incomplete partition.
- **Evaluation:** report coverage, failures, abstentions, paired baselines, cost, latency and uncertainty. Separate crowd-aware from crowd-free arms. No tuning on a revealed holdout.
- **Research:** a null result with a frozen protocol is useful. A new multi-agent layer is not a win until it beats the direct-model comparison at a stated budget.
- **Experience:** improve the keyless first-run path, accessibility, error messages, examples and translations without inventing performance claims.

## Review and credit

Review is based on evidence, not affiliation, PR size or enthusiasm. Maintainers may ask for a narrower change. Negative results, source audits and documentation fixes receive credit alongside code. Release notes name accepted contributors using the attribution they choose; request anonymity if desired. Use `Co-authored-by` only with that person's agreement.

Contributors retain copyright. Contributions use the repository's MIT terms; no copyright-assignment CLA. Do not submit code or data you lack permission to share. Adding a new release file requires an explicit entry in `release-files.json` so accidental private files are not swept into exports.

There is no funded bounty or automatic compute allowance. A bounty must specify funding, amount, acceptance criteria, deadline, eligibility, payment terms and adjudicator **before** work starts. Do not spend in expectation of reimbursement.

See [governance](GOVERNANCE.md) for the path to component stewardship. Be direct about technical disagreements and respectful toward people.
