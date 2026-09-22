// Scenario simulation under explicit annual growth assumptions. These draws describe the
// specified model; sample count is not evidence of real-world forecast calibration.
import { forecastUnit, convertForecastValue } from "./forecastUnits.ts";

export type ForecastResult = {
  ok: true;
  engine: "monte_carlo_fermi";
  probability: number;
  median: number;
  ci_low: number;
  ci_high: number;
  threshold: number;
  threshold_dir: string;
  horizon_years: number;
  base_value: number;
  n_samples: number;
  histogram: { lo: number; hi: number; counts: number[]; peak: number };
};

export type Support = "count" | "fraction" | "positive" | "real";

export type ForecastSpec = {
  question?: string;
  base_value: number;
  horizon_years: number;
  g_mean?: number;
  g_sd?: number;
  decel?: number;
  threshold: number;
  threshold_dir?: string;
  ci_unit?: string;
  support?: Support;
  base_unit?: string;
  threshold_unit?: string;
  seed?: number;
  n?: number;
};

const MAX_HORIZON = 100;
const MAX_SAMPLES = 200_000;

function finite(value: unknown, name: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${name} must be a finite number`);
  return value;
}

/** Validate before provider work and normalize inputs into the declared output unit. */
export function validateForecastSpec(spec: ForecastSpec): ForecastSpec {
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) throw new Error("forecast specification must be an object");
  for (const name of ["ci_unit", "base_unit", "threshold_unit"] as const) {
    if (spec[name] !== undefined && typeof spec[name] !== "string") throw new Error(`${name} must be a unit string`);
  }
  const unit = spec.ci_unit ?? "";
  const base = convertForecastValue(finite(spec.base_value, "base_value"), spec.base_unit ?? unit, unit);
  const threshold = convertForecastValue(finite(spec.threshold, "threshold"), spec.threshold_unit ?? unit, unit);
  const horizon = finite(spec.horizon_years, "horizon_years");
  const mean = spec.g_mean === undefined ? 1 : finite(spec.g_mean, "g_mean");
  const sd = spec.g_sd === undefined ? .1 : finite(spec.g_sd, "g_sd");
  const decel = spec.decel === undefined ? 0 : finite(spec.decel, "decel");
  const n = spec.n === undefined ? 80_000 : finite(spec.n, "n");
  if (horizon < 0 || horizon > MAX_HORIZON) throw new Error(`horizon_years must be between 0 and ${MAX_HORIZON}`);
  if (mean <= 0 || sd < 0 || decel < 0) throw new Error("g_mean must be positive; g_sd and decel must be nonnegative");
  if (mean - decel * Math.max(0, Math.ceil(horizon) - 1) <= 0) throw new Error("annual growth multiplier must remain positive over the horizon");
  if (!Number.isInteger(n) || n < 1 || n > MAX_SAMPLES) throw new Error(`n must be an integer between 1 and ${MAX_SAMPLES}`);
  if (spec.threshold_dir !== undefined && spec.threshold_dir !== ">=" && spec.threshold_dir !== "<=") throw new Error("threshold_dir must be >= or <=");
  if (spec.seed !== undefined && (!Number.isInteger(spec.seed) || spec.seed < 0 || spec.seed > 0xffffffff)) throw new Error("seed must be an unsigned 32-bit integer");
  if (spec.question !== undefined && typeof spec.question !== "string") throw new Error("question must be a string");
  const u = forecastUnit(unit);
  const support = spec.support ?? u.kind;
  if (!["count", "fraction", "positive"].includes(support)) throw new Error("unsupported support: this growth model requires a count, fraction, or positive quantity");
  if (support === "count" && u.kind !== "count" && unit) throw new Error("count support requires a discrete count unit");
  if (support === "fraction" && u.kind !== "fraction" && unit) throw new Error("fraction support requires fraction or percent units");
  if (u.kind === "fraction" && support !== "fraction") throw new Error("fraction units require bounded fraction support");
  if (base < 0 || threshold < 0) throw new Error("base_value and threshold must be nonnegative for this support");
  if (support === "fraction" && (base * u.scale > 1 || threshold * u.scale > 1)) throw new Error("fraction base_value and threshold must lie within their unit's 0–1 range");
  if (![base * u.scale, threshold * u.scale].every(Number.isFinite)) throw new Error("canonical unit conversion overflow");
  return {...spec, base_value: base, threshold, base_unit: unit, threshold_unit: unit, horizon_years: horizon,
    g_mean: mean, g_sd: sd, decel, n, support, threshold_dir: spec.threshold_dir ?? ">="};
}

/** Deterministic 32-bit seed from the question (matches the intent of the Python sha256 seed). */
export function seedFromQuestion(s: string): number {
  let h = 2166136261 >>> 0; // FNV-1a
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h >>> 0;
}

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Standard-normal sampler (Box-Muller) over a uniform RNG. */
function makeGauss(rand: () => number) {
  return (mean: number, sd: number) => {
    let u = 0;
    let v = 0;
    while (u === 0) u = rand();
    while (v === 0) v = rand();
    const z = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    return mean + sd * z;
  };
}

function histogram(sorted: number[], bins = 28) {
  const n = sorted.length;
  const lo = sorted[Math.floor(0.02 * n)];
  const hi = sorted[Math.floor(0.98 * n)];
  if (hi <= lo) return {lo, hi: lo, counts: [n], peak: n};
  const width = (hi - lo) / bins;
  const counts = new Array(bins).fill(0);
  for (const value of sorted) {
    if (value < lo || value > hi) continue;
    const idx = Math.min(bins - 1, Math.floor((value - lo) / width));
    counts[idx] += 1;
  }
  const peak = Math.max(...counts) || 1;
  return { lo, hi, counts, peak };
}

export function runForecast(input: ForecastSpec): ForecastResult {
  const spec = validateForecastSpec(input);
  const baseVal = spec.base_value;
  const horizonYears = spec.horizon_years;
  const gMean = spec.g_mean!;
  const gSd = spec.g_sd!;
  const decel = spec.decel!;
  const threshold = spec.threshold;
  const direction = spec.threshold_dir!;
  const n = spec.n!;
  // A default seed independent of display units makes equivalent representations comparable.
  const seed = spec.seed ?? seedFromQuestion("forecast");
  const support = spec.support!;
  const scale = forecastUnit(spec.ci_unit ?? "").scale;
  const canonicalBase = baseVal * scale;
  const rand = mulberry32(seed);
  const gauss = makeGauss(rand);
  // Lognormal annual multipliers match the supplied mean and standard deviation. Partial
  // years scale log drift by dt and log variance by dt. Deceleration changes each year's
  // mean; it cannot make the multiplier negative. This also supports declines below 15%.
  const steps: {drift: number; sigma: number}[] = [];
  for (let year = 0; year < Math.ceil(horizonYears); year++) {
    const dt = Math.min(1, horizonYears - year);
    const mean = gMean - decel * year;
    const variance = Math.log1p((gSd / mean) ** 2);
    const drift = (Math.log(mean) - variance / 2) * dt;
    const sigma = Math.sqrt(variance * dt);
    if (!Number.isFinite(drift) || !Number.isFinite(sigma)) throw new Error("growth parameters exceed the supported numeric range");
    steps.push({drift, sigma});
  }
  const out = new Array<number>(n);
  const canonicalThreshold = threshold * scale;
  let beyond = 0;
  for (let i = 0; i < n; i++) {
    let logGrowth = 0;
    for (const step of steps) logGrowth += step.sigma ? gauss(step.drift, step.sigma) : step.drift;
    let v = canonicalBase;
    if (horizonYears > 0) {
      if (support === "fraction") {
        if (v > 0 && v < 1 && logGrowth !== 0) {
          const logOdds = Math.log(v / (1 - v)) + logGrowth;
          v = logOdds >= 0 ? 1 / (1 + Math.exp(-logOdds)) : Math.exp(logOdds) / (1 + Math.exp(logOdds));
        }
      } else {
        const growth = Math.exp(logGrowth);
        v = canonicalBase === 0 ? 0 : Number.isFinite(growth)
          ? canonicalBase * growth : Math.exp(Math.log(canonicalBase) + logGrowth);
        if (support === "count") {
          // Count noise is in individual units, never in thousands or physical capacity units.
          v = Math.max(0, gauss(v, Math.sqrt(v * Math.min(horizonYears, 1))));
        }
      }
    }
    const displayed = horizonYears === 0 ? baseVal : v / scale;
    if (!Number.isFinite(displayed)) throw new Error("forecast samples exceed the supported numeric range");
    out[i] = displayed;
    // Compare in canonical units, before display conversion can round a boundary value.
    if (direction === "<=" ? v <= canonicalThreshold : v >= canonicalThreshold) beyond++;
  }
  out.sort((a, b) => a - b);
  const pct = (p: number) => out[Math.floor(p * out.length)];

  return {
    ok: true,
    engine: "monte_carlo_fermi",
    probability: Math.round((beyond / out.length) * 1000) / 1000,
    median: pct(0.5),
    ci_low: pct(0.1),
    ci_high: pct(0.9),
    threshold,
    threshold_dir: direction,
    horizon_years: horizonYears,
    base_value: baseVal,
    n_samples: out.length,
    histogram: histogram(out),
  };
}
