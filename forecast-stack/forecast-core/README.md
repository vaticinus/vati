# Vaticinus forecast core

MIT forecasting workflow and probability engine. Node 22.18+; zero runtime dependencies. Research with public sources, run a model with your own key and an explicit budget, save revisions, resolve outcomes and compare a model against its harness. Accounts and billing remain outside this package.

**[Complete runnable workflow](WORKFLOW.md)**: installation, real data packs, evidence collection, budgeted execution, updates, scoring and frozen benchmarks. Start free with `npm ci && npm run build && node dist/cli.js demo`. Version 0.2.0 adds the installed `vati` command and reusable `/runtime`, `/evidence`, `/workflow`, `/benchmark` and `/data-packs` exports. The old provider callback below remains a compatibility path; use the new metered runtime for bounded execution.

```bash
npm test
npm run example
npm run eval:list
```

## Compute and save without a model

```typescript
import { computeForecast } from './src/lib/forecastEngine.ts';
import { freezeForecastSpec, readForecastSnapshot } from './src/lib/forecastSnapshot.ts';

const spec = {
  kind: 'conditional', partition: 'Crisis or no crisis',
  question: 'Will the project finish by the deadline?',
  resolution_date: '2035-12-31',
  dated_metric: 'Completion entered in the project register by the deadline.',
  branches: [
    { condition: 'Crisis', weight: 0.3, p_yes: 0.6 },
    { condition: 'No crisis', weight: 0.7, p_yes: 0.1 },
  ],
};
const result = computeForecast(spec); // 0.25, conditional on these assumptions
const saved = freezeForecastSpec(spec, result);
const restored = readForecastSnapshot(JSON.parse(JSON.stringify(saved)));
console.log(restored.result?.probability); // saved 0.25; no resimulation
```

The engine validates binary probabilities, exhaustive conditional partitions, Bayesian updates, normal threshold models and growth scenarios. It rejects incomplete partitions and unsupported inputs instead of inventing missing probabilities. Whether real-world branches actually are exclusive/exhaustive still needs human judgment.

## LLM harness

`src/lib/coherence.ts` exports `prepareForecastContract`, `forecastContractBlock`, and `finalizeForecastAnswer`. They extract an event contract, check typed-model consistency and apply bounded skeptical review. Extraction and semantic judgment can still be wrong; failed-review objections are explicitly unverified diagnostics. Pass an injected `ForecastCompletion(system, user, stage, maxTokens)` callback to use your own transport, budget and model. The built-in provider configuration lives in `src/lib/model.ts`; it is server-side and can load local environment files. Importing the pure engine does not require credentials.

The built-in OpenRouter default is `xiaomi/mimo-v2.6-flash`. Set `VATI_CHAT_PROVIDER=openrouter` to pin the hosted route; without an explicit provider, the existing DeepSeek → Fireworks → OpenRouter hosted order remains. Request-local personal model keys form a separate OpenRouter → DeepSeek → Fireworks chain: rejection never falls back to a hosted key. Personal model selections override workflow/stage model settings. Removing the last personal model key restores hosted routing; a search-only key does not replace model funding.

The core's default completion path is not a hard-dollar-budget executor. A caller must enforce spending. The separate `scripts/forecast_eval.mts` evaluator reserves worst-case cost and requires a positive `--max-cost` and ledger before paid evaluation. Example after explicit budget approval and key configuration:

```bash
node --experimental-strip-types scripts/forecast_eval.mts \
  --provider openrouter --model xiaomi/mimo-v2.6-flash \
  --arms direct_thinking,harness --split dev --max-cost 0.50 \
  --ledger ./budget.json --out ./results.json
```

Read the evaluator's current provider/model restrictions before spending. `--list` is free. The published dev/holdout/redteam cases are synthetic and already exposed: do not call repeated passes fresh holdout performance. Compare a frozen candidate against a direct reasoning-enabled model with the same evidence.

The source-checkout evaluator also supports MiMo 2.6 Pro and DeepSeek V4.1 Flash on OpenRouter. It pins the model, caps provider prices, disables fallback, preserves unknown-cost reservations, refuses output overwrites, and enforces one cumulative ledger across runs. These restrictions belong to the evaluator, not to the unrestricted default completion callback. `direct_budget` controls the maximum output allowance, not actual consumed compute. See the [full evaluation walkthrough](../docs/SUPERFORECASTING.md) for exact controls and a reproducible improvement workflow. Scripts and benchmark fixtures are source-checkout tools, not runtime tarball contents.

## Packaging and compatibility

`npm ci && npm pack` compiles JavaScript and TypeScript declarations into the tarball. Package exports resolve compiled ESM, so an installed package runs in plain Node without a TypeScript loader. For example: `import { computeForecast } from '@vaticinus/forecast-core/engine'`. The `/harness`, `/snapshot`, `/prompt` and `/provider-context` subpaths are also exported. Local source-relative examples still run with Node's native type stripping. No registry publication is performed by these commands.

`MANIFEST.json` records the original extraction's source hashes, not an unchangeable upstream lock. Contributors change this package directly. Root release manifests hash current files. Contract/math tests are evidence of those behaviors, not forecasting accuracy; the reviewer can miss factual and financial-semantic errors.
