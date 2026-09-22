// The BYOK request context and shared vocabulary. Deliberately dependency-free (no db, no crypto)
// so modules on the test path (model.ts, websearch.ts, research.ts) can import it under Node's
// --experimental-strip-types without pulling the database or Clerk into the graph.
import { AsyncLocalStorage } from "node:async_hooks";

export type ByokProvider = "deepseek" | "fireworks" | "openrouter" | "tinyfish";

export type ByokEntry = { key: string; model?: string };
export type ByokKeys = Partial<Record<ByokProvider, ByokEntry>>;

export const OPENROUTER_DEFAULT_MODEL = "xiaomi/mimo-v2.6-flash";

export const BYOK_PROVIDERS: Record<
  ByokProvider,
  { label: string; hint: string; covers: "model" | "model+research" | "search"; defaultModel?: string }
> = {
  openrouter: {
    label: "OpenRouter",
    hint: "Start here: MiMo 2.6 Flash is the low-cost default. Your key also pays for Sonar when a workflow uses it.",
    covers: "model+research",
    defaultModel: OPENROUTER_DEFAULT_MODEL,
  },
  deepseek: {
    label: "DeepSeek",
    hint: "A native DeepSeek key. Used after OpenRouter if both personal keys are saved.",
    covers: "model",
  },
  fireworks: {
    label: "Fireworks",
    hint: "A Fireworks key. Last personal model fallback; never a fallback to our balance.",
    covers: "model",
  },
  tinyfish: {
    label: "TinyFish",
    hint: "Optional personal search key. Covers web search and page fetch, not model generation.",
    covers: "search",
  },
};

const store = new AsyncLocalStorage<ByokKeys>();

/** Run `fn` with the caller's BYOK keys visible to provider/search resolution. */
export function withByok<T>(keys: ByokKeys, fn: () => T): T {
  return store.run(keys, fn);
}

/** The BYOK keys for the current request, or {} outside a withByok scope. */
export function currentByok(): ByokKeys {
  return store.getStore() ?? {};
}

/** True when the user brought a model-capable key (not search-only). */
export function hasModelKey(keys: ByokKeys): boolean {
  return Boolean(keys.deepseek?.key || keys.fireworks?.key || keys.openrouter?.key);
}

/** True when the user brought any key at all. */
export function hasAnyKey(keys: ByokKeys): boolean {
  return Object.values(keys).some((v) => Boolean(v?.key));
}

// These tiers waive app credits with a personal model key; the provider still bills the user.
// Sonar is skipped unless that user also supplies an OpenRouter key. Shared free search remains
// available. External-data tiers and the separately hosted board retain their app credit cost.
const MODEL_ONLY_TIERS: Record<string, true> = { council: true, deep: true, scan: true };

export function byokWaivesCredits(tier: string, keys: ByokKeys): boolean {
  return Object.hasOwn(MODEL_ONLY_TIERS, tier) && hasModelKey(keys);
}
