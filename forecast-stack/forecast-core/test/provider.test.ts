import { test } from 'node:test';
import assert from 'node:assert/strict';
import { withByok } from '../src/lib/byokContext.ts';
import { prepareForecastContract } from '../src/lib/coherence.ts';

test('a rejected personal model cannot charge the host while preparing a forecast', async () => {
  const previousEnv = { ...process.env };
  const originalFetch = globalThis.fetch;
  process.env.DEEPSEEK_API_KEY = 'test-hosted-deepseek';
  process.env.OPENROUTER_API_KEY = 'test-hosted-openrouter';
  process.env.FIREWORKS_API_KEY = 'test-hosted-fireworks';
  delete process.env.VATI_CHAT_PROVIDER;
  let hostedRequests = 0;
  globalThis.fetch = async (_url, init) => {
    if (new Headers(init?.headers).get('Authorization') !== 'Bearer test-personal-key') hostedRequests++;
    return new Response('rejected', { status: 401 });
  };
  try {
    const result = await withByok({ deepseek: { key: 'test-personal-key' } }, () =>
      prepareForecastContract('Will the specified event occur by December 31, 2027?'));
    assert.equal(result.contract, null);
    assert.equal(hostedRequests, 0);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) if (!(key in previousEnv)) delete process.env[key];
    Object.assign(process.env, previousEnv);
  }
});
