# Unreleased

- Fixed Bayesian likelihood underflow without probability floors: 1,398/1,398 high-precision synthetic cases pass, versus 1,339 before. Impossible observations still fail; old saved forecasts remain unchanged.
- Separated scoring-only question baselines from inference. Dated baseline evidence remains available when explicitly supplied in the evidence packet.
- Indexed benchmark rows and resampled cluster sufficient statistics. The 10,000-row synthetic scorer ran 11.5 times faster in the recorded measurement; all score fields agreed within 8.2e-16.
- Published a zero-cost, 131-forecast historical calibration comparison with source/event exclusion and full numerical inputs. The residual candidate failed its promotion criteria; calibration defaults are unchanged. See [measurements and limits](docs/SUPERFORECASTING.md#measured-improvement-and-rejected-calibration-22-september).
- Published a $0.010183, 54-forecast evidence-selection diagnostic. Selection lowered point Brier but failed uncertainty and naive-baseline checks; its packets matched a free topic/recency filter in all 18 cases. Preserved original-release outcomes, exact prompts, semantic failures and offline scoring. No production forecasting policy changed. See [results and limits](docs/SUPERFORECASTING.md#evidence-selection-pilot-lower-point-score-failed-success-gate).

# 0.2.0 reference workflow (2026-09-22)

- Added installable `vati` CLI, metered streaming execution, evidence collection, immutable lifecycle, frozen model/harness evaluation, real government-data starter packs and installed-artifact acceptance. Existing registered studies remain unchanged. See `forecast-core/WORKFLOW.md`. Python package remains 0.1.0.
- Reworked the public Space as a Vaticinus forecasting workbench with guided scenario inputs, OpenRouter personal-key forecasting, per-attempt spending limits, cancellation and downloadable success/failure records. Reuses the metered runtime; no hosted-key fallback or automatic retries. Added HTTP regressions for credential rejection, pre-request budget refusal and typed issuance.
- Rewrote the repository landing page around the forecasting mission, linked the live workbench, and moved the original Beyond Brier study documentation to `BEYOND_BRIER.md`. Its Python package metadata points to that document; code and licenses are unchanged.
- Repaired forecast packaging after the Gemma historical diagnostic: typed generic JSON blocks enter the same validation path, complete corrected forecast blocks are revalidated and reviewed, and mixed-format duplicate candidates fail closed. No kind, probability or event is inferred to rescue an output. The registered Gemma result remains unchanged; saved-response replay is not a new accuracy score.
- Published the registered [Gemma 27B comparison](benchmarks/gemma-27b-2025-fomc/RESULTS.md): 11/12 forecasts issued; paired Brier difference −0.0195 on five pairs, with one missing harness forecast and only one event cluster. Semantic failures survive review; no major-decision readiness claim follows.
- Fixed Markdown URL-label citation extraction: `[URL](URL)` no longer creates a spurious unprovided URL, while an unprovided destination behind a supplied-looking label still fails admission. Saved-response replay reaches semantic review but cannot issue without it. The registered 27B outcomes and scores remain unchanged.

# Changelog

## 0.1.0 release candidate

- Unified public source home for the Python toolkit and extracted TypeScript forecast core.
- Integrated as `forecast-stack/` in the existing `vaticinus/vati` repository, preserving Beyond Brier's code, public history and Apache-2.0 license. Shared CI and community templates live at the repository root; component exports remain allowlisted.
- Added a keyless probability workbench and Hugging Face Docker Space staging.
- Added contributor, governance, conduct, security, citation and release guidance; explicit source allowlists and release manifests.
- Repaired collector CLI invocation of module entry points and shared configurable data paths for installed packages.
- Removed unsafe `leak_free` admission based on recall lower bounds. `outcome_after_checkpoint` now checks documented checkpoint dates only, rejects invalid dates, and does not claim a complete leakage audit.
- Preserved complete resolution criteria and supplied evidence in the Python baseline prompt.
- Replaced the destructive quickstart ledger reset with a temporary synthetic forecast → resolution → score lifecycle.
- Corrected claims that local hash chains or git dates provide independent timestamp proof.
- Closed malformed/nested forecast-card and underspecified card-free approval paths; retained reviewer issues and labeled failed-review diagnostics as unverified.
- Clarified parameter provenance, stipulated assumptions and hypothetical-event routing without claiming semantic infallibility.
- Hardened the source-checkout evaluator's cumulative spending ledger, unknown-charge reservations, output preservation, model/route restrictions and token-ceiling accounting.
- Repaired current-format, source-aware scoring, missing-category reporting, calibration-bin accounting, clustered uncertainty and development-only method selection; removed diagnostic promotion claims.
- Added a model/harness superforecasting walkthrough, locally registered protocols, all complete-run numerical measurements, frozen transfer comparisons and explicit semantic/fixture failure audits. No positive harness accuracy increment or 9/10 major-decision readiness was established.
- Isolated personal model keys from hosted funding, made MiMo 2.6 Flash the OpenRouter default, and preserved personal model selection across stages. A rejected-key regression reproduces three hosted attempts before the fix and none afterward. This post-study change is supplied as a reversible patch so the frozen experimental source remains recoverable.
- Published a preregistered three-event prospective pilot of MiMo v2.6 Pro, Gemini 3.8 Flash and DeepSeek v4.1 Flash: nine direct forecasts issued, nine harness failures retained, outcomes unresolved. The report discloses a transport-stop protocol deviation; it establishes no model winner or harness benefit.
- Fixed the pilot runner's failure to stop after a response-body timeout following HTTP 200 headers. The offline regression fails before and passes after the repair. Original registration bytes remain immutable; changed source cannot execute against the old source pin.
- Require registered per-model reasoning capability flags before prospective inference, preserve mandatory reasoning across every harness stage, and retain provider error payloads in the private ledger. Offline regressions cover successful event correction through a mandatory-reasoning provider boundary and refusal to spend with missing capability metadata. No live forecasting results were replaced or upgraded.
- Published all 32 release-separated historical FOMC forecasts and a standalone scorer. Llama's numerical harness gain is confined to one semantically contradictory answer; Qwen's harness worsens Brier. Checkpoint-serving and retrospective-selection limitations remain explicit. Added registered historical issue-date guards and omission of unsupported reasoning parameters without relaxing prospective date checks.

This candidate does not establish real-world forecasting superiority. GitHub source publication, package registries, model/dataset releases and the Hugging Face Space are separate actions; preparing this source does not imply those other releases occurred.
