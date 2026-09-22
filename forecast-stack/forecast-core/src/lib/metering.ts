// Token cost metering. The app previously had NO accounting of what a turn actually cost, which is
// why "what does a query cost" could only be answered from estimates. This module prices a
// provider's reported `usage` against the live rate card and emits one structured log line per
// call, so real spend is observable from the Worker logs. Pure and dependency-free so the rate
// math is unit-tested without the network.

export type Usage = {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  prompt_tokens_details?: { cached_tokens?: number };
};

type Rate = { in: number; out: number; cached: number }; // USD per 1M tokens, off-peak
type ModelRates = { peak: Rate; offPeak: Rate };

// USD per 1M tokens. DeepSeek keys are cache-miss input / output / cache-hit input. Peak is exactly
// double off-peak and applies 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday (DeepSeek's rate card).
const RATES: Record<string, ModelRates> = {
  // DeepSeek V4.1-Flash (served as deepseek-flash; legacy v4-flash ids route here).
  "deepseek-flash": { offPeak: { in: 0.15, out: 0.6, cached: 0.003 }, peak: { in: 0.3, out: 1.2, cached: 0.006 } },
  "deepseek-v4-flash": { offPeak: { in: 0.15, out: 0.6, cached: 0.003 }, peak: { in: 0.3, out: 1.2, cached: 0.006 } },
  "deepseek-v4-flash-0731": { offPeak: { in: 0.15, out: 0.6, cached: 0.003 }, peak: { in: 0.3, out: 1.2, cached: 0.006 } },
  "deepseek-v4.1-flash": { offPeak: { in: 0.15, out: 0.6, cached: 0.003 }, peak: { in: 0.3, out: 1.2, cached: 0.006 } },
  // DeepSeek V4-Pro (being phased out; still billed at the higher pro rates).
  "deepseek-v4-pro": { offPeak: { in: 0.66, out: 1.98, cached: 0.022 }, peak: { in: 1.32, out: 3.96, cached: 0.044 } },
  "deepseek-v4-pro-0813": { offPeak: { in: 0.66, out: 1.98, cached: 0.022 }, peak: { in: 1.32, out: 3.96, cached: 0.044 } },
  // Fireworks-served weights (serverless list price).
  "fireworks-deepseek-v4p1-flash": { offPeak: { in: 0.22, out: 0.66, cached: 0.007 }, peak: { in: 0.22, out: 0.66, cached: 0.007 } },
  // A conservative floor for unknown ids so we never log $0 for a real call.
  default: { offPeak: { in: 0.22, out: 0.66, cached: 0.007 }, peak: { in: 0.22, out: 0.66, cached: 0.007 } },
};

export function providerName(url: string): string {
  if (url.includes("fireworks")) return "fireworks";
  if (url.includes("deepseek")) return "deepseek";
  if (url.includes("openrouter")) return "openrouter";
  if (url.includes("minimax")) return "minimax";
  return "unknown";
}

/** DeepSeek's peak windows: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri. Other providers are flat,
 *  but pricing them at peak is harmless (the rate table is identical). */
export function isDeepSeekPeak(now: Date): boolean {
  const day = now.getUTCDay(); // 0=Sun .. 6=Sat
  if (day === 0 || day === 6) return false;
  const h = now.getUTCHours() + now.getUTCMinutes() / 60;
  return (h >= 1 && h < 4) || (h >= 6 && h < 10);
}

/** Normalize a provider model id to its rate key. Fireworks paths carry an `accounts/...` prefix. */
export function rateKeyFor(model: string): string {
  const m = (model || "").toLowerCase();
  if (m.includes("fireworks") || m.includes("accounts/")) {
    if (m.includes("v4p1-flash") || m.includes("v4.1-flash")) return "fireworks-deepseek-v4p1-flash";
    return "default";
  }
  for (const key of Object.keys(RATES)) {
    if (key !== "default" && m.includes(key)) return key;
  }
  if (m.includes("v4.1-flash")) return "deepseek-v4.1-flash";
  return "default";
}

/** Price one call's reported usage in USD. Unknown models fall back to the conservative default. */
export function estimateCostUsd(model: string, usage: Usage | undefined, now: Date = new Date()): number {
  const key = rateKeyFor(model);
  const table = RATES[key] ?? RATES.default;
  const rate = isDeepSeekPeak(now) ? table.peak : table.offPeak;
  const cached = usage?.prompt_tokens_details?.cached_tokens ?? 0;
  const prompt = Math.max(0, usage?.prompt_tokens ?? 0);
  const uncached = Math.max(0, prompt - cached);
  const completion = Math.max(0, usage?.completion_tokens ?? 0);
  return (uncached * rate.in + cached * rate.cached + completion * rate.out) / 1_000_000;
}

export type UsageLog = {
  at: string;
  stage: string;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  est_cost_usd: number;
};

/** Build the structured usage record (separated from the console write so it is unit-testable). */
export function usageRecord(stage: string, provider: string, model: string, usage: Usage | undefined, now: Date = new Date()): UsageLog {
  return {
    at: now.toISOString(),
    stage,
    provider,
    model,
    prompt_tokens: usage?.prompt_tokens ?? 0,
    completion_tokens: usage?.completion_tokens ?? 0,
    cached_tokens: usage?.prompt_tokens_details?.cached_tokens ?? 0,
    est_cost_usd: Math.round(estimateCostUsd(model, usage, now) * 1e6) / 1e6,
  };
}

/** Emit one metering line. Never throws and never blocks the answer. */
export function logUsage(stage: string, provider: string, model: string, usage: Usage | undefined, now: Date = new Date()): UsageLog {
  const rec = usageRecord(stage, provider, model, usage, now);
  try {
    console.log(`[usage] ${JSON.stringify(rec)}`);
  } catch {
    /* logging is best-effort */
  }
  return rec;
}
