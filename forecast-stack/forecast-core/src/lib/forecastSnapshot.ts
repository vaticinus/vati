import { computeForecast, validateProbabilityModel, type ForecastResult } from './forecastEngine.ts';

export const FORECAST_ENGINE_VERSION = 'typed-probability-v1';
export type ForecastSnapshot = {
  schema_version: 1;
  engine_version: string;
  computed_at: string;
  result: ForecastResult;
};
const object = (v: unknown): Record<string, unknown> | null => v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : null;
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Remove model-supplied outputs at the NEW-forecast boundary. History reads do not use this. */
export function uncomputedForecastSpec(spec: Record<string, unknown>): Record<string, unknown> {
  const {computed_forecast: _snapshot, computed_forecast_error: _error, computed_result: _computed,
    result: _result, probability: _p, median: _median, ci_low: _low, ci_high: _high,
    histogram: _histogram, n_samples: _samples, ...raw} = spec;
  return raw;
}

/** Called only when computing a NEW forecast, never during history rendering. A model's
 * claimed snapshot is replaced with the actual engine result. Serialization freezes values. */
export function freezeForecastSpec(spec: Record<string, unknown>, result?: ForecastResult, now = new Date()): Record<string, unknown> {
  const raw = uncomputedForecastSpec(spec);
  try {
    const input = validateProbabilityModel(raw);
    const computed = result ?? computeForecast(input);
    const snapshot: ForecastSnapshot = {
      schema_version: 1, engine_version: FORECAST_ENGINE_VERSION, computed_at: now.toISOString(),
      result: JSON.parse(JSON.stringify(computed)) as ForecastResult,
    };
    return {...raw, ...input, computed_forecast: snapshot};
  } catch (e) {
    return {...raw, computed_forecast_error: (e as Error).message};
  }
}

function savedNumbers(raw: Record<string, unknown>): Partial<ForecastResult> | null {
  if (!number(raw.probability) || raw.probability < 0 || raw.probability > 1) return null;
  const result: Record<string, unknown> = {probability: raw.probability};
  if (typeof raw.engine === 'string') result.engine = raw.engine;
  if (typeof raw.method === 'string') result.method = raw.method;
  for (const key of ['base_value','horizon_years','median','ci_low','ci_high','threshold','n_samples']) {
    if (number(raw[key])) result[key] = raw[key];
  }
  if (raw.threshold_dir === '>=' || raw.threshold_dir === '<=' || raw.threshold_dir === '>' || raw.threshold_dir === '<') result.threshold_dir = raw.threshold_dir;
  const h = object(raw.histogram);
  if (h && number(h.lo) && number(h.hi) && h.hi >= h.lo && number(h.peak) && h.peak > 0 &&
      Array.isArray(h.counts) && h.counts.length && h.counts.every(v => number(v) && v >= 0)) {
    result.histogram = JSON.parse(JSON.stringify(h));
  }
  return result as Partial<ForecastResult>;
}

/** Read-only history path: no simulation, network, new timestamp or engine substitution. */
export function readForecastSnapshot(spec: Record<string, unknown>): {
  result: Partial<ForecastResult> | null; status: 'saved' | 'legacy' | 'unavailable'; note: string;
} {
  const snapshot = object(spec.computed_forecast);
  const rawResult = object(snapshot?.result);
  const result = rawResult ? savedNumbers(rawResult) : null;
  if (snapshot?.schema_version === 1 && typeof snapshot.engine_version === 'string' && snapshot.engine_version &&
      typeof snapshot.computed_at === 'string' && Number.isFinite(Date.parse(snapshot.computed_at)) && result) {
    return {result, status:'saved', note:`Original result saved ${snapshot.computed_at} (${snapshot.engine_version}). Not recomputed.`};
  }
  // Older exports sometimes retained output fields without a versioned envelope. Preserve
  // those original numbers, but never fill missing fields by running today's engine.
  for (const legacy of [object(spec.computed_result), object(spec.result), spec]) {
    const old = legacy && savedNumbers(legacy);
    if (old) return {result:old, status:'legacy', note:'Original saved probability retained; computation metadata unavailable. Not recomputed.'};
  }
  return {result:null, status:'unavailable', note:'Original computed result unavailable. This saved scenario has not been recomputed.'};
}


/** Persist the same frozen values used by stream/history rendering. This does not score them. */
export function frozenForecastCardFields(spec: Record<string, unknown>) {
  const saved = readForecastSnapshot(spec);
  if (saved.status !== 'saved' || !saved.result) return null;
  const r = saved.result;
  const text = (key: string) => typeof spec[key] === 'string' ? spec[key] as string : null;
  const numeric = (key: string) => number(spec[key]) ? spec[key] as number : null;
  return {
    question: text('question') ?? '', quantity_label: text('quantity_label'), ci_unit: text('ci_unit'),
    base_value: r.base_value, horizon_years: r.horizon_years,
    g_mean: numeric('g_mean'), g_sd: numeric('g_sd'), decel: numeric('decel'),
    threshold: r.threshold, threshold_dir: r.threshold_dir, probability: r.probability,
    median: r.median, ci_low: r.ci_low, ci_high: r.ci_high,
    resolution_date: text('resolution_date'), dated_metric: text('dated_metric'),
    kill_criteria: Array.isArray(spec.kill_criteria) ? spec.kill_criteria.filter((v): v is string => typeof v === 'string') : null,
    already_priced: text('already_priced'),
  };
}
