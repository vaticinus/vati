# Contributor opportunities

## Goal

Make an open forecast stack worth using without Vaticinus hosting, and improve decision-relevant forecasts on a prospective, externally witnessed record. Adoption is useful when other people run, inspect and improve the code. Stars are not a forecasting metric.

## Ready-to-open issues

These are local issue briefs, not claims that GitHub issues or funded bounties already exist.

| Task | Track / size | Acceptance evidence |
|---|---|---|
| Audit one collector's publication/vintage semantics | data / small | Named source, terms URL, observation-vs-publication distinction, a dated example and one failure boundary; do not relabel timestamps |
| Add a no-key source parser fixture | data / small | Minimal redistributable fixture or synthetic equivalent; missing/revised/invalid rows behave correctly without live network |
| Reproduce one event-scope failure | core / small | Exact request, declared settling rule, minimal counterexample, and a regression that fails on the defect |
| Improve keyboard and screen-reader use of the workbench | experience / small | Demonstrated focus/error/result-announcement flow at desktop and mobile; no paid backend |
| Add one supported provider transport example | integration / medium | Public API docs, explicit model and budget, clean failure behavior, no credential logging and an offline example |
| Compare a simple baseline with the full review harness | evaluation / medium | Predeclared same-model/same-packet cohort, failures retained, cost/latency and blind semantic audit; report nulls |
| Publish a source-vintage-ready dataset proposal | data / medium | Rights and provenance per row, no private records, explicit cutoff semantics; release only after review |
| Run a forward forecast cohort | evaluation / advanced | Witnessed pre-outcome manifest, fixed resolution rules, direct and naive baselines, no selective deletion, paired uncertainty at resolution |

Pick a bounded component. A good first PR removes one reproducible failure or makes one usable source trustworthy; it does not introduce a new agent framework.

For the model/harness evaluation sequence, use the [walkthrough](SUPERFORECASTING.md) and [registered gate rubric](../benchmarks/2026-09-22/protocol.json). Prioritize a witnessed, source-vintage-clean comparison with a strong direct model over additional orchestration. Treat calibration, evidence selection and decision payoffs as separate ablations. A positive point estimate without a paired uncertainty interval and untouched replication is not promotion.

## Contributor offer

**Available in the release:** permissive commercial reuse, local execution, all core prompts and calculations, no paid account for development, named attribution, accepted negative results, public technical decisions and component-steward nominations.

**Proposed only, not funded:** compute grants for pre-registered experiments, fixed-scope bounties, paid source maintenance and sponsored replication. Activate any of these only after publishing a named budget and rules. No contribution should be solicited with an implied payout.

Do not pay for stars, manipulate rankings, or reward PR volume. If funding becomes available, reward reproducibility, source quality, useful maintenance and prospective improvements, including well-run falsifications.

## Launch sequence

1. Publish the reviewed GitHub source and keyless Space. Make the first successful calculation possible without a key. Enable issues, Discussions and private security reporting.
2. Open 6–8 bounded issue briefs from the table, label by track and difficulty, and name an initial reviewer. Do not flood the backlog with unowned work.
3. Publish a short walkthrough showing one real calculation, one rejected invalid model, and one saved snapshot. State the limits next to the result.
4. Invite users to reproduce and break the stack. External posts, messages and outreach require separately reviewed copy and recipient approval; this document sends nothing.
5. Review the first contribution path: fresh-clone success, reproducible issue reports, accepted contributions and review latency. No invented adoption targets or guaranteed virality.
6. Publish the first prospective comparison, including failures and nulls. Only then make an accuracy claim that matches the evidence.

The differentiation is the useful artifact and the inspectable record, not a claim that more agents must be smarter.
