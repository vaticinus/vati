# Architecture and separation

## One public home, two independent packages

`forecast-stack` is the contributor and source home. Python ships data adapters, baselines, scoring and records. `forecast-core` ships the TypeScript probability engine, event-contract/review harness and version 0.2 reference workflow. Its `vati` CLI composes source collection, metered streaming execution, immutable history, data packs and frozen paired evaluation; each layer is also importable. The two packages share principles and examples, not runtime state or an implied interchangeable API.

```text
Public sources or user evidence
  -> Python collectors / user-supplied packets
  -> a chosen baseline or externally supplied LLM completion
  -> event definition + probability assumptions
  -> typed computation / saved snapshot
  -> Python event ledger + separate resolution
  -> explicit scoring and coverage reporting
```

This is a set of composable tools, not an automatically wired service. Python's CLI can forecast, seal and score; its quickstart shows a complete synthetic lifecycle. The browser workbench exercises the real TypeScript engine. The hosted chat has additional orchestration and retrieval that are not bundled.

## Boundary

| Public release | Private / separate permission |
|---|---|
| Python toolkit and 64 collector modules | Operational Python monorepo and its git history |
| Typed engine, prompts, contract/review harness, synthetic eval runner | Hosted chat UI, Clerk, Stripe, customer conversations and BYOK storage |
| Offline examples, deterministic checks, explicit limitations | Production environment files, tokens, billing and cloud-account configuration |
| Collector code and source references | Collected corpus, licensed feeds, third-party benchmark dumps and private research |
| Keyless Hugging Face Space | Contact discovery, outreach, customer/recipient lists and sales operations |

Code being MIT does not make the data it fetches MIT. A separate dataset release needs row-level provenance, redistribution rights, date/vintage semantics, PII review and a dataset card. A model release needs actual weights, parent-model rights, training-data provenance and a model card. Neither is smuggled into a code release.

## Sources of truth after cutover

- Public components are maintained in this repository. Community PRs land here.
- The original typed-core extraction records its 13 source hashes in `forecast-core/MANIFEST.json`. That is provenance, not a license to overwrite later contributor changes.
- The private product may consume a versioned compiled npm tarball or reviewed changes from a tagged public release. Migrating its imports is a separate product change; this release does not silently switch the running chat.
- Do not repeatedly re-export the private tree over the public repo. If a private fix is relevant, bring it over as a focused public PR with a reproduction, then use the accepted public version.
- Root `release-files.json` is a reviewed static allowlist. Release staging never copies `.git`, environments, local data, installed dependencies or build caches. `RELEASE_MANIFEST.json` hashes the actual staged files.

## Truth boundaries

An as-of prompt cannot remove facts already in model weights. A collector's observation date cannot establish publication availability. A passing LLM review cannot establish semantic truth. A local saved snapshot cannot prove when it was first published. The public API and docs keep these limits visible rather than advertising a universal leak-free guarantee.
