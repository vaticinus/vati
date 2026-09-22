import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {MeteredRuntime, openRouterModel} from '../src/lib/runtime.ts';
const dir = () => fs.mkdtempSync(path.join(os.tmpdir(), 'vati-runtime-'));
const opts = (root: string, request: typeof fetch) => ({model: 'test/model', apiKey: 'private-test-key',
  rates: {input: 1, output: 2}, maxCost: 1, ledger: path.join(root, 'budget.json'), fetch: request});
const asFetch = (f: (url: any, init?: RequestInit) => Promise<Response>) => f as typeof fetch;
function stream(chunks: unknown[]): Response {
  return new Response(chunks.map(c => 'data: ' + (typeof c === 'string' ? c : JSON.stringify(c)) + '\n\n').join(''),
    {headers: {'content-type': 'text/event-stream'}});
}
test('streamed requests retain usage and supported reasoning on review stages', async () => {
  const root = dir(); let body: any;
  const runtime = new MeteredRuntime({...opts(root, asFetch(async (_u, init) => {
    body = JSON.parse(init!.body as string);
    return stream([{choices: [{delta: {reasoning: 'thinking'}}]}, {choices: [{delta: {content: 'answer'}, finish_reason: 'stop'}]},
      {choices: [], usage: {prompt_tokens: 100, completion_tokens: 20, cost: .00012}}, '[DONE]']);
  })), reasoning: true});
  try {
    assert.equal(await runtime.complete('system', 'question', 'forecast_review', 1000), 'answer');
    assert.equal(body.reasoning.enabled, true); assert.equal(body.provider.allow_fallbacks, false);
    assert.ok(Math.abs(runtime.spent - .00014) < 1e-10); assert.equal(runtime.traces[0].status, 'complete');
    assert.ok(!fs.readFileSync(path.join(root, 'budget.json'), 'utf8').includes('private-test-key'));
  } finally { runtime.close(); fs.rmSync(root, {recursive: true}); }
});
test('body failure after HTTP 200 stops further calls and retains unknown charge across reopening', async () => {
  const root = dir(); let calls = 0;
  const runtime = new MeteredRuntime(opts(root, asFetch(async () => {
    calls++; return new Response(new ReadableStream({start(controller) { controller.error(new Error('body timed out')); }}), {status: 200});
  })));
  await assert.rejects(runtime.complete('system', 'question', 'draft', 200));
  assert.equal(runtime.terminal, true); assert.equal(runtime.traces[0].accounted_usd, runtime.traces[0].reservation_usd);
  await assert.rejects(runtime.complete('system', 'question', 'draft', 200)); assert.equal(calls, 1);
  const spent = runtime.spent; runtime.close();
  const next = new MeteredRuntime(opts(root, asFetch(async () => {throw new Error('must not run');})));
  assert.equal(next.spent, spent); next.close(); fs.rmSync(root, {recursive: true});
});
test('partial SSE without terminal marker is missing rather than issued', async () => {
  const root = dir(), runtime = new MeteredRuntime(opts(root, asFetch(async () => stream([{choices: [{delta: {content: 'partial'}}]}]))));
  try { await assert.rejects(runtime.complete('s', 'u', 'draft', 100), /before a completion marker/); assert.equal(runtime.terminal, true); }
  finally {runtime.close();fs.rmSync(root,{recursive:true});}
});
test('budget refuses before network and an active ledger has exclusive ownership', async () => {
  const root = dir(); let called = false;
  const options = {...opts(root, asFetch(async () => {called=true;throw Error('network');})), maxCost: .00001};
  const runtime = new MeteredRuntime(options);
  try {
    assert.throws(() => new MeteredRuntime(options), /EEXIST/);
    await assert.rejects(runtime.complete('s', 'u', 'draft', 100), /ceiling/);
    assert.equal(called,false); assert.equal(runtime.traces.length,0);
  } finally {runtime.close();fs.rmSync(root,{recursive:true});}
});
test('provider diagnostics survive without retaining the authorization key', async () => {
  const root = dir(), runtime = new MeteredRuntime(opts(root, asFetch(async () => new Response('bad private-test-key configuration', {status:400}))));
  try {
    await assert.rejects(runtime.complete('s','u','contract',100), /HTTP 400/);
    const data=fs.readFileSync(path.join(root,'budget.json'),'utf8'); assert.ok(data.includes('configuration')); assert.ok(!data.includes('private-test-key'));
  } finally {runtime.close();fs.rmSync(root,{recursive:true});}
});
test('catalog rejects hidden per-request charges and exposes supported reasoning', async () => {
  const response=(pricing:object)=>asFetch(async()=>Response.json({data:[{id:'model',pricing,supported_parameters:['reasoning'],context_length:32768}]}));
  assert.deepEqual(await openRouterModel('model',response({prompt:'0.000001',completion:'0.000002'})),{rates:{input:1,output:2},reasoning:true,context:32768});
  await assert.rejects(openRouterModel('model',response({prompt:'0.000001',completion:'0.000002',request:'0.1'})),/additional model charge/);
});
