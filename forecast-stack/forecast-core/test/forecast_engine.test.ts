import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeForecast } from '../src/lib/forecastEngine.ts';
import { freezeForecastSpec, readForecastSnapshot } from '../src/lib/forecastSnapshot.ts';

const close = (a: number, b: number, tolerance = 1e-7) => assert.ok(Math.abs(a - b) < tolerance, `${a} != ${b}`);

test('a rare first-arrival event retains its probability without a growth base or invented interval', () => {
  const result = readForecastSnapshot(freezeForecastSpec({kind:'binary', p_yes:.007, question:'Will the first event occur?'})).result!;
  assert.equal(result.probability, .007);
  assert.equal(result.median, undefined);
  assert.equal(result.ci_low, undefined);
  assert.equal(result.histogram, undefined);
});

test('conditional state weights integrate event probabilities rather than multiplying correlated marginals', () => {
  const spec = {kind:'conditional', partition:'Crisis or no crisis', branches:[
    {condition:'Crisis', weight:.3, p_yes:.6}, {condition:'No crisis', weight:.7, p_yes:.1},
  ]};
  close(computeForecast(spec).probability, .25);
  assert.throws(() => computeForecast({...spec, branches:[spec.branches[0], {...spec.branches[1], weight:.5}]}), /sum/);
  assert.throws(() => computeForecast({...spec, branches:[spec.branches[0], {...spec.branches[0], weight:.7}]}), /repeat/);
});

test('Bayes uses the false-positive rate and rejects an impossible observation', () => {
  close(computeForecast({kind:'bayes', prior:.01, likelihood_yes:.8, likelihood_no:.1, observation:'positive'}).probability, .008 / .107);
  assert.throws(() => computeForecast({kind:'bayes', prior:.2, likelihood_yes:0, likelihood_no:0, observation:'impossible'}), /zero probability/);
});

test('a likelihood ratio updates odds once without inventing absolute likelihoods', () => {
  const model = {kind:'bayes', prior:.2, likelihood_ratio:3, observation:'One dossier, four syndicated copies'};
  close(computeForecast(model).probability, 3/7);
  assert.throws(() => computeForecast({...model, likelihood_yes:.8, likelihood_no:.1}), /not both/);
  assert.throws(() => computeForecast({...model, prior:1, likelihood_ratio:0}), /zero probability/);
});

test('numeric models allow negative outcomes and conserve complementary threshold probability', () => {
  const s = {kind:'normal', mean:-2, sd:1, threshold:-3, threshold_dir:'<=', ci_unit:'percentage points'};
  const below = computeForecast(s), above = computeForecast({...s, threshold_dir:'>'});
  close(below.probability, .1586552539);
  close(below.probability + above.probability, 1);
  assert.ok(below.ci_high! < 0);
});

test('strict and inclusive thresholds differ at a deterministic boundary', () => {
  const s = {kind:'normal', mean:3, sd:0, threshold:3, ci_unit:'percent'};
  assert.equal(computeForecast({...s, threshold_dir:'>='}).probability, 1);
  assert.equal(computeForecast({...s, threshold_dir:'>'}).probability, 0);
  assert.equal(computeForecast({...s, threshold_dir:'<='}).probability, 1);
  assert.equal(computeForecast({...s, threshold_dir:'<'}).probability, 0);
});

test('bounded distributions stay within support and equivalent units give equivalent probabilities', () => {
  const s = {kind:'normal', mean:.8, sd:.3, lower:0, upper:1, threshold:.9, threshold_dir:'>=', ci_unit:'fraction'};
  const fraction = computeForecast(s);
  const percent = computeForecast({...s, mean:80, sd:30, upper:100, threshold:90, ci_unit:'percent'});
  close(fraction.probability, percent.probability);
  close(fraction.ci_low! * 100, percent.ci_low!);
  assert.ok(fraction.ci_low! >= 0 && fraction.ci_high! <= 1);
  assert.equal(computeForecast({...s, threshold:1.1}).probability, 0);
});

test('normal thresholds are monotone and narrow supported uncertainty is not widened', () => {
  const s = {kind:'normal', mean:110, sd:1, threshold:105, threshold_dir:'>=', ci_unit:'MW'};
  const result = computeForecast(s);
  assert.ok(result.probability > .99999);
  assert.ok(computeForecast({...s, threshold:111}).probability < result.probability);
  assert.ok(result.ci_high! - result.ci_low! < 3);
});

test('invalid probability inputs cannot masquerade as computable forecasts', () => {
  for (const p of [-.1, 1.1, NaN, Infinity, '0.5']) assert.throws(() => computeForecast({kind:'binary', p_yes:p}));
  assert.throws(() => computeForecast({kind:'normal', mean:0, sd:-1, threshold:1, threshold_dir:'>=', ci_unit:'MW'}));
  assert.throws(() => computeForecast({kind:'normal', mean:0, sd:1, lower:2, upper:1, threshold:1, threshold_dir:'>=', ci_unit:'MW'}));
});
