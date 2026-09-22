import { runForecast, validateForecastSpec, type ForecastResult as GrowthResult, type ForecastSpec } from "./mc.ts";

export type Direction = ">=" | ">" | "<=" | "<";
export type ConditionalBranch = { condition: string; weight: number; p_yes: number; rationale?: string };
export type ProbabilityModel =
  | { kind: "binary"; p_yes: number }
  | { kind: "conditional"; partition: string; branches: ConditionalBranch[] }
  | ({ kind: "bayes"; prior: number; observation: string } &
      ({ likelihood_yes: number; likelihood_no: number } | { likelihood_ratio: number }))
  | { kind: "normal"; mean: number; sd: number; threshold: number; threshold_dir: Direction; ci_unit: string; lower?: number; upper?: number }
  | (ForecastSpec & { kind: "growth" });
export type ForecastResult = Partial<Omit<GrowthResult, "ok" | "engine" | "probability" | "threshold_dir">> & {
  ok: true;
  engine: "binary_judgment" | "conditional_probability" | "bayesian_update" | "normal_distribution" | "monte_carlo_fermi";
  probability: number;
  method: string;
  threshold_dir?: Direction;
};

function finite(v: unknown, name: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) throw new Error(`${name} must be a finite number`);
  return v;
}
function probability(v: unknown, name: string): number {
  const p = finite(v, name);
  if (p < 0 || p > 1) throw new Error(`${name} must be between 0 and 1`);
  return p;
}
function text(v: unknown, name: string): string {
  if (typeof v !== "string" || !v.trim()) throw new Error(`${name} must be a nonempty string`);
  return v.trim();
}
export function forecastObject(v: unknown): Record<string, unknown> {
  if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("forecast must be an object");
  return v as Record<string, unknown>;
}

/** Select a mathematical family, never convert an event into a growing probability proxy.
 * Unlabelled growth inputs remain readable for existing saved quantitative scenarios. */
export function validateProbabilityModel(value: unknown): ProbabilityModel {
  const s = forecastObject(value);
  switch (s.kind) {
    case "binary":
      return { kind: "binary", p_yes: probability(s.p_yes, "p_yes") };
    case "conditional": {
      const partition = text(s.partition, "partition");
      if (!Array.isArray(s.branches) || s.branches.length < 2 || s.branches.length > 12) {
        throw new Error("conditional model requires 2–12 mutually exclusive, exhaustive branches");
      }
      const labels = new Set<string>();
      const branches = s.branches.map((value, i) => {
        const b = forecastObject(value);
        const condition = text(b.condition, `branches[${i}].condition`);
        const key = condition.toLowerCase().replace(/\s+/g, " ");
        if (labels.has(key)) throw new Error("conditional branches repeat the same condition");
        labels.add(key);
        return { condition, weight: probability(b.weight, `branches[${i}].weight`),
          p_yes: probability(b.p_yes, `branches[${i}].p_yes`),
          ...(typeof b.rationale === "string" ? { rationale: b.rationale } : {}) };
      });
      if (Math.abs(branches.reduce((sum, b) => sum + b.weight, 0) - 1) > 1e-8) {
        throw new Error("conditional branch weights must sum to 1; do not normalize an incomplete partition");
      }
      return { kind: "conditional", partition, branches };
    }
    case "bayes": {
      if (s.likelihood_ratio !== undefined) {
        if (s.likelihood_yes !== undefined || s.likelihood_no !== undefined) throw new Error("supply likelihood_ratio or the two likelihoods, not both");
        const prior = probability(s.prior, "prior"), likelihood_ratio = finite(s.likelihood_ratio, "likelihood_ratio");
        if (likelihood_ratio < 0) throw new Error("likelihood_ratio must be nonnegative");
        if (prior === 1 && likelihood_ratio === 0) throw new Error("the supplied observation has zero probability under the certain hypothesis");
        return { kind: "bayes", prior, likelihood_ratio, observation: text(s.observation, "observation") };
      }
      const model = { kind: "bayes" as const, prior: probability(s.prior, "prior"),
        likelihood_yes: probability(s.likelihood_yes, "likelihood_yes"),
        likelihood_no: probability(s.likelihood_no, "likelihood_no"), observation: text(s.observation, "observation") };
      if ((model.prior === 0 || model.likelihood_yes === 0) && (model.prior === 1 || model.likelihood_no === 0)) {
        throw new Error("the supplied observation has zero probability under both hypotheses");
      }
      return model;
    }
    case "normal": {
      const mean = finite(s.mean, "mean"), sd = finite(s.sd, "sd");
      const threshold = finite(s.threshold, "threshold");
      if (sd < 0) throw new Error("sd must be nonnegative and expressed in the outcome's units");
      const dir = s.threshold_dir;
      if (dir !== ">=" && dir !== ">" && dir !== "<=" && dir !== "<") throw new Error("threshold_dir must be >=, >, <=, or <");
      const lower = s.lower === undefined ? undefined : finite(s.lower, "lower");
      const upper = s.upper === undefined ? undefined : finite(s.upper, "upper");
      if (lower !== undefined && upper !== undefined && lower >= upper) throw new Error("lower must be less than upper");
      if (sd === 0 && ((lower !== undefined && mean < lower) || (upper !== undefined && mean > upper))) {
        throw new Error("deterministic outcome is outside the declared support");
      }
      return { kind: "normal", mean, sd, threshold, threshold_dir: dir, ci_unit: text(s.ci_unit, "ci_unit"),
        ...(lower === undefined ? {} : { lower }), ...(upper === undefined ? {} : { upper }) };
    }
    case undefined:
    case "growth":
      return { ...validateForecastSpec(s as unknown as ForecastSpec), kind: "growth" };
    default:
      throw new Error(`unsupported forecast kind: ${String(s.kind)}`);
  }
}

// Standard normal upper tail; evaluating the small tail directly avoids 1-CDF cancellation.
function normalTail(z: number): number {
  if (z === Infinity) return 0;
  if (z === -Infinity) return 1;
  if (z === 0) return 0.5;
  const a = Math.abs(z), t = 1 / (1 + 0.2316419 * a);
  const small = Math.exp(-a * a / 2) / Math.sqrt(2 * Math.PI) * t *
    (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  return z >= 0 ? small : 1 - small;
}
function normalMass(lo: number, hi: number): number {
  if (hi <= lo) return 0;
  return lo >= 0 ? normalTail(lo) - normalTail(hi) : normalTail(-hi) - normalTail(-lo);
}

/** Compute exactly what the selected model says. No uncertainty floor, extremizing,
 * hidden market blend, probability target, or modification of the event. */
export function computeForecast(value: unknown): ForecastResult {
  const m = validateProbabilityModel(value);
  if (m.kind === "growth") return { ...runForecast(m), threshold_dir: m.threshold_dir as Direction,
    method: "Growth scenario under stated assumptions; not empirically calibrated." };
  if (m.kind === "binary") return { ok: true, engine: "binary_judgment", probability: m.p_yes,
    method: "Judgmental event probability. No simulated quantity or statistical confidence interval." };
  if (m.kind === "conditional") return { ok: true, engine: "conditional_probability",
    probability: Math.max(0, Math.min(1, m.branches.reduce((sum, b) => sum + b.weight * b.p_yes, 0))),
    method: "Total probability across the stated partition. Branch weights and conditional estimates are assumptions, not independent votes." };
  if (m.kind === "bayes") {
    // Log odds preserve possible evidence when prior × likelihood underflows.
    const ratio = "likelihood_ratio" in m ? m.likelihood_ratio : m.likelihood_yes / m.likelihood_no;
    const logLikelihood = "likelihood_ratio" in m || (ratio > 0 && Number.isFinite(ratio)) ? Math.log(ratio) :
      Math.log(m.likelihood_yes) - Math.log(m.likelihood_no);
    const logOdds = Math.log(m.prior) - Math.log1p(-m.prior) + logLikelihood;
    const small = Math.exp(-Math.abs(logOdds));
    return { ok: true, engine: "bayesian_update", probability: logOdds >= 0 ? 1 / (1 + small) : small / (1 + small),
      method: "Bayes' rule for one joint observation. Repeated reports of that observation are not additional evidence." };
  }
  const shared = { ok: true as const, engine: "normal_distribution" as const, threshold: m.threshold,
    threshold_dir: m.threshold_dir, method: "Outcome distribution under the stated mean and standard deviation. The 80% predictive interval is not a confidence interval on the event probability." };
  if (m.sd === 0) {
    const yes = m.threshold_dir === ">" ? m.mean > m.threshold : m.threshold_dir === ">=" ? m.mean >= m.threshold :
      m.threshold_dir === "<" ? m.mean < m.threshold : m.mean <= m.threshold;
    return { ...shared, probability: Number(yes), median: m.mean, ci_low: m.mean, ci_high: m.mean };
  }
  const lo = m.lower === undefined ? -Infinity : (m.lower - m.mean) / m.sd;
  const hi = m.upper === undefined ? Infinity : (m.upper - m.mean) / m.sd;
  const total = normalMass(lo, hi);
  if (!(total > 1e-14)) throw new Error("normal distribution has negligible mass within its declared support");
  const z = (m.threshold - m.mean) / m.sd;
  const below = normalMass(lo, Math.min(hi, z)) / total;
  const above = normalMass(Math.max(lo, z), hi) / total;
  const quantile = (p: number) => {
    let left = Math.max(-40, lo), right = Math.min(40, hi);
    for (let i = 0; i < 70; i++) {
      const mid = (left + right) / 2;
      if (normalMass(lo, mid) / total < p) left = mid; else right = mid;
    }
    return m.mean + m.sd * (left + right) / 2;
  };
  const lower = quantile(.001), upper = quantile(.999), width = (upper - lower) / 28;
  if (![lower, upper, width].every(Number.isFinite) || width <= 0) throw new Error("normal outcome distribution exceeds numeric precision");
  const counts = Array.from({ length: 28 }, (_, i) => normalMass((lower + i * width - m.mean) / m.sd,
    (lower + (i + 1) * width - m.mean) / m.sd) / total);
  return { ...shared, probability: Math.max(0, Math.min(1, m.threshold_dir.startsWith(">") ? above : below)),
    median: quantile(.5), ci_low: quantile(.1), ci_high: quantile(.9),
    histogram: { lo: lower, hi: upper, counts, peak: Math.max(...counts) } };
}

export function formatProbability(p: number): string {
  if (p === 0) return "0%";
  if (p === 1) return "100%";
  if (p < .001) return "<0.1%";
  if (p > .999) return ">99.9%";
  return `${Number((p * 100).toFixed(1))}%`;
}
