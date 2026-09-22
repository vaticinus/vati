import test from 'node:test';
import assert from 'node:assert/strict';
import { createWorkbenchServer } from '../../space/server.mts';

const key = 'sk-or-test-personal-only';
const input = {
  apiKey: key, model: 'xiaomi/mimo-v2.6-flash', arm: 'direct', maxCost: 0.1,
  question: 'Will the synthetic project finish by the deadline?', resolutionDate: '2099-12-31',
  resolutionRule: 'YES if completion is recorded by the stated deadline.',
  evidence: 'Synthetic planning assumption: one quarter of comparable projects completed.',
};
const catalog = (price = '0.000001') => new Response(JSON.stringify({ data: [{
  id: input.model, pricing: { prompt: price, completion: price }, supported_parameters: [], context_length: 100000,
}] }));
async function withServer(request: typeof fetch, use: (base: string) => Promise<void>) {
  const server = createWorkbenchServer(request);
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address !== 'string');
  try { await use(`http://127.0.0.1:${address.port}`); }
  finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
}
const post = (base: string, value: unknown) => fetch(base + '/api/forecast', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
});

test('Space requires a personal key before any provider access and never falls back after rejection', async () => {
  let paid = 0, accesses = 0;
  await withServer(async url => {
    accesses += 1;
    if (String(url).endsWith('/models')) return catalog();
    paid += 1;
    return new Response(`Rejected credential ${key}`, { status: 401 });
  }, async base => {
    assert.equal((await post(base, { ...input, apiKey: '' })).status, 400);
    assert.equal(accesses, 0);
    const response = await post(base, { ...input, arm: 'harness' });
    const result = await response.json();
    assert.equal(result.run.status, 'error');
    assert.equal(result.run.probability, null);
    assert.equal(paid, 1);
    assert.ok(result.cost.accounted_usd > 0);
    assert.equal(JSON.stringify(result).includes(key), false);
  });
});

test('Space refuses an unaffordable reservation without a paid request', async () => {
  let paid = 0;
  await withServer(async url => {
    if (String(url).endsWith('/models')) return catalog('1');
    paid += 1; throw new Error('A paid request must not happen');
  }, async base => {
    const result = await (await post(base, input)).json();
    assert.equal(result.run.status, 'error');
    assert.equal(result.run.probability, null);
    assert.equal(result.cost.accounted_usd, 0);
    assert.equal(paid, 0);
  });
});

test('Space issues a downloadable typed forecast without leaking the key', async () => {
  const spec = { kind: 'binary', p_yes: 0.25, question: input.question,
    resolution_date: input.resolutionDate, dated_metric: input.resolutionRule,
    assumptions: ['Synthetic planning assumption, not independently verified.'] };
  await withServer(async url => {
    if (String(url).endsWith('/models')) return catalog();
    return new Response(JSON.stringify({ choices: [{ message: { content: '```vaticinus-forecast\n' + JSON.stringify(spec) + '\n```' }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 100, completion_tokens: 100, cost: 0.0002 } }), { headers: { 'Content-Type': 'application/json' } });
  }, async base => {
    const result = await (await post(base, input)).json();
    assert.equal(result.run.status, 'issued');
    assert.equal(result.run.probability, 0.25);
    assert.equal(result.run.spec.computed_forecast.result.probability, 0.25);
    assert.equal(JSON.stringify(result).includes(key), false);
  });
});

test('disconnecting aborts the paid transport before any review request', { timeout: 5000 }, async () => {
  let entered!: () => void, observedAbort!: () => void, paid = 0;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const aborted = new Promise<void>(resolve => { observedAbort = resolve; });
  await withServer(async (url, options) => {
    if (String(url).endsWith('/models')) return catalog();
    paid += 1; entered();
    return new Promise<Response>((_resolve, reject) => {
      options!.signal!.addEventListener('abort', () => { observedAbort(); reject(new Error('Transport cancelled')); }, { once: true });
    });
  }, async base => {
    const controller = new AbortController();
    const response = fetch(base + '/api/forecast', { method: 'POST', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...input, arm: 'harness' }) });
    await started; controller.abort();
    await assert.rejects(response, error => error instanceof Error && error.name === 'AbortError');
    await aborted;
    assert.equal(paid, 1);
  });
});
