# Release and next run

## What ships

**GitHub:** the allowlisted `forecast-stack/` component in the existing [`vaticinus/vati`](https://github.com/vaticinus/vati) repository: Python package, typed core, tests, examples, community policies, roadmap, release tooling and workbench. No private monorepo history or private data. Beyond Brier remains at the repository root under Apache-2.0; this component is MIT.

**Hugging Face:** the [Vaticinus Forecast Workbench](https://huggingface.co/spaces/vaticinus/forecast-stack), assembled from the same core and workbench. It has a keyless scenario lab and a personal-key OpenRouter forecasting workflow on CPU Basic. No model weights or hosted API keys are deployed. GitHub remains the source of truth; update the Space from a reviewed release.

**Package artifacts:** a Python wheel/sdist and compiled npm tarball. Build and install-test locally. Publishing to PyPI/npm is a separate action requiring namespace ownership and release credentials; this run does not assume either.

The [evaluation walkthrough](SUPERFORECASTING.md) and allowlisted synthetic benchmark artifacts ship with the source. Raw provider responses, paid-request ledgers, private operational forecast records and trained weights do not. Reproducing numerical analysis from released rows is different from independently reproducing stochastic provider generations.

## Reference workflow 0.2.0

The Node package now includes the compiled `vati` CLI and all ten exports. Its version is 0.2.0; the independently packaged Python API remains 0.1.0. Build with `npm --prefix forecast-core run test:install` and replay the government snapshots with `node forecast-core/examples/replay-packs.mjs`. The install acceptance uses a new directory and local tarball, with no registry or model calls. Version this release as `forecast-core-v0.2.0`; attach the npm tarball and its SHA-256 digest. Do not claim npm publication merely because the source and GitHub asset are public.

## Local gate

From `vati/forecast-stack/` in the public source checkout:

```bash
python -m pip install -e '.[dev]' build
python -m pytest
python examples/quickstart.py
npm --prefix forecast-core ci
npm --prefix forecast-core test
npm --prefix forecast-core run build
npm --prefix forecast-core run example
npm --prefix forecast-core run eval:list
python scripts/release.py --check
python -m build
(cd forecast-core && npm pack)
```

Verify the wheel and npm tarball from a clean directory, outside the source tree. Import each exported npm subpath. Exercise the demo at desktop/mobile sizes, its error state and saved snapshot. The parent repository's `.github/workflows/ci.yml` checks Beyond Brier on Python 3.9–3.12, this component on Python 3.10/3.13, Node 22 and a Docker build. Run the root project's `pytest` separately in its own environment. A green local check is not evidence CI has already run on GitHub.

## Stage separate release folders

Destinations must be new directories outside this repository:

```bash
python scripts/release.py --target github --out /tmp/forecast-stack-public
python scripts/release.py --target space --out /tmp/forecast-stack-space
```

The exporter scans the static allowlists, rejects symlinks/private paths and writes a SHA-256 `RELEASE_MANIFEST.json`. The `github` target is a **component snapshot**, not a replacement for the parent repository. Shared CI, issue forms and the PR template live at the parent root and are reviewed there. Review the exact files and run independent secret scanning. The patterns are not a full secret or licensing audit. Do not use `git push --mirror`, copy the private `.git`, or publish the operational monorepo.

## Publish after owner review

1. Review `forecast-stack/` and any shared-root changes against the existing `vaticinus/vati` history. Confirm MIT rights for the component, attribution, release contents and security-reporting channel. Preserve Beyond Brier's code, paper, data and Apache-2.0 license.
2. Commit on a branch descended from that repository; do **not** create a replacement repository, initialize new root history, or force-push. Integrate the component's CI and community templates at the parent root. Protect main and require CI/review as appropriate for the maintainer workflow. Tag `forecast-stack-v0.1.0` only after remote CI and a clean-clone smoke pass.
3. Attach the wheel, sdist, npm tarball and component source manifest to that GitHub release. Check package contents, not just source contents. Publish registries only after account/scope ownership is confirmed. Never put tokens in commands committed to the repo.
4. Create a public **Docker Space on CPU Basic** in the chosen Hugging Face owner. As observed on September 22, 2026, Hugging Face requires a PRO subscription even for Docker Spaces on CPU Basic; do not purchase one or upgrade hardware without owner approval. Upload only the staged Space directory, including its root README metadata, Dockerfile, core and `space/`. Keep `short_description` within Hugging Face's 60-character limit. The container listens on 7860 as UID 1000. Set no keys and no upgraded hardware. Verify the live calculation, invalid-input rejection and download in the iframe; record explicitly when browser verification is deferred.
5. Keep repository URLs pointed at `vaticinus/vati`, with component paths where appropriate. Open reviewed issue briefs and post the walkthrough only after copy/recipient approval where outreach is involved.

No automatic publish workflow is installed. CI on pull requests has read-only repository permissions and receives no production model or cloud secrets.

## The next forecasting run

**Question:** Does the harness improve forecast quality enough to justify its additional cost over a direct reasoning-enabled model with identical information?

The [walkthrough](SUPERFORECASTING.md) separates closed-world diagnostics from the prospective evidence required for this claim. Apply its model, harness and decision gates; do not promote a model from a synthetic score alone.

1. **First, $0 release acceptance.** Run the commands above and a fresh-clone walkthrough. This is engineering validation, not a forecast experiment.
2. **Then register a prospective pilot before any model call.** Select a fixed cohort of genuinely open, short-horizon questions with unambiguous initial-release settling rules and permitted source use. Save every question, resolution date, outcome source, as-of cutoff, source publication dates and exclusions. Record the selection rule, not only chosen questions.
3. **Freeze the comparison.** One named model/version in both arms; same evidence packet and as-of time. Arm A: direct reasoning-enabled model. Arm B: contract → generation → bounded review → typed snapshot. Add a declared naive/reference baseline. Keep crowd-aware evidence separate. Publish source/config/cohort hashes before outcomes using an independent timestamp witness.
4. **Get a fresh spending approval.** Name provider, model, question count, maximum calls/tokens and worst-case dollar ceiling. In the current pinned evaluator, a direct arm makes one request; the harness can make six including a second contract extraction, two reviews and a correction. The full output ceiling is 23,800 with standard review or 33,800 with thinking review. Recompute these bounds if the call graph changes. No Opus or unapproved paid work. Stop on billing/auth failures. Do not reuse an old budget approval.
5. **Run once; retain every outcome.** Save prompts, responses, typed outputs, timestamps, latency, usage, cost, failures and abstentions. Never replace provider failures with fabricated 50% forecasts. Treat model/provider failures separately from genuine abstention. Do not tune on this cohort after exposure.
6. **Score at resolution.** Primary: paired Brier on the declared binary cohort with an explicit preregistered missingness policy. Also report paired coverage, abstentions, errors, semantic violations, cost/forecast and latency. Report confidence intervals clustered by underlying event/date where appropriate. A small pilot cannot establish broad superiority or calibration.
7. **Promotion rule:** claim improvement only if the preregistered score/coverage/semantic/cost gates pass. A synthetic arithmetic pass, larger agent count or nicer answer is not promotion. A null result is a release artifact, not something to conceal.

Publishable datasets and weights come later only if they exist, are redistributable and have honest cards. Do not manufacture a Hugging Face model release to look bigger.
