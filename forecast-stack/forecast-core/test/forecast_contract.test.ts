import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runForecast, type ForecastSpec } from '../src/lib/mc.ts';
import { needleVerdict } from '../src/lib/needle.ts';

const base: ForecastSpec = { question: 'unit and horizon diagnostic', base_value: 1000, horizon_years: 1, g_mean: 1, g_sd: 0.05, threshold: 1100, threshold_dir: '>=', ci_unit: 'MW', n: 20000 };
const close = (a: number, b: number) => assert.ok(Math.abs(a - b) <= 1e-10 * Math.max(1, Math.abs(a), Math.abs(b)), `${a} != ${b}`);

test('power forecasts are invariant between equivalent MW and GW specifications', () => {
  const mw = runForecast(base);
  const gw = runForecast({...base, base_value: 1, threshold: 1.1, ci_unit: 'GW'});
  assert.equal(mw.probability, gw.probability);
  close(mw.median / 1000, gw.median);
  close(mw.ci_low / 1000, gw.ci_low);
  close(mw.ci_high / 1000, gw.ci_high);
});

test('a fractional year compounds for the requested duration', () => {
  const q = runForecast({...base, g_mean: 1.44, g_sd: 0, horizon_years: .5});
  assert.equal(q.horizon_years, .5);
  close(q.median, 1200);
  assert.equal(q.probability, 1);
  const other = runForecast({...base, g_mean: 1.44, g_sd: 0, horizon_years: .25});
  assert.notEqual(q.median, other.median);
});

test('zero horizon preserves the known present value without added future noise', () => {
  const r = runForecast({...base, horizon_years: 0, threshold: 1000});
  assert.equal(r.median, 1000);
  assert.equal(r.ci_low, 1000);
  assert.equal(r.ci_high, 1000);
  assert.equal(r.probability, 1);
});

test('fractions are invariant between proportions and percentage points', () => {
  const frac = runForecast({...base, base_value: .08, threshold: .1, ci_unit: 'fraction', g_mean: 1.2});
  const percent = runForecast({...base, base_value: 8, threshold: 10, ci_unit: '%', g_mean: 1.2});
  assert.equal(frac.probability, percent.probability);
  close(frac.median * 100, percent.median);
});

test('count forecasts preserve noise when expressed in thousands', () => {
  const count = runForecast({...base, ci_unit: 'people'});
  const thousand = runForecast({...base, base_value: 1, threshold: 1.1, ci_unit: 'thousand people'});
  assert.equal(count.probability, thousand.probability);
  close(count.median / 1000, thousand.median);
});

test('explicit incompatible units and count support on physical capacity are rejected', () => {
  assert.throws(() => runForecast({...base, base_unit: 'kg'} as ForecastSpec), /unit|dimension/i);
  assert.throws(() => runForecast({...base, support: 'count'}), /count|unit|support/i);
});

test('nonfinite, coerced, out-of-domain and unbounded simulation inputs are rejected', () => {
  for (const update of [
    {base_value: null}, {base_value: '1000'}, {horizon_years: -.5}, {horizon_years: Infinity},
    {g_mean: NaN}, {g_mean: 0}, {g_sd: -1}, {decel: Infinity},
    {threshold_dir: '~'}, {n: 0}, {n: 1.5}, {n: 1e9}, {support: 'bogus'},
  ]) assert.throws(() => runForecast({...base, ...update} as ForecastSpec), undefined, JSON.stringify(update));
});

test('arbitrary named-source prose does not admit automatic scoring', () => {
  const verdict = needleVerdict({resolution_date: '2099-12-31', dated_metric: 'a made up source that no resolver knows', threshold: 1, threshold_dir: '>=', probability: .7});
  assert.equal(verdict.scorable, false);
});

test('impossible calendar dates and invalid probabilities cannot be admitted', () => {
  const card = {resolution_date: '2099-12-31', dated_metric: 'BLS monthly price release', threshold: 1, threshold_dir: '>=', probability: .7};
  for (const update of [{resolution_date: '2099-02-30'}, {probability: 1.1}, {probability: -.1}]) {
    const verdict = needleVerdict({...card, ...update});
    assert.equal(verdict.scorable, false);
    assert.equal(verdict.structured, false);
  }
});
