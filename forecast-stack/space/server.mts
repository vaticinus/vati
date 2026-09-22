import { createServer, type IncomingMessage } from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { computeForecast, forecastObject } from '../forecast-core/src/lib/forecastEngine.ts';
import { freezeForecastSpec } from '../forecast-core/src/lib/forecastSnapshot.ts';
import { MeteredRuntime, openRouterModel, sha256 } from '../forecast-core/src/lib/runtime.ts';
import { runForecast, validateQuestion, type ForecastQuestion } from '../forecast-core/src/lib/workflow.ts';
import { packet, type Evidence } from '../forecast-core/src/lib/evidence.ts';
import { OPENROUTER_DEFAULT_MODEL } from '../forecast-core/src/lib/byokContext.ts';

const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
  ['/style.css', ['style.css', 'text/css; charset=utf-8']],
]);
const choices = [
  { id: OPENROUTER_DEFAULT_MODEL, name: 'MiMo 2.6 Flash' },
  { id: 'deepseek/deepseek-v4.1-flash', name: 'DeepSeek 4.1 Flash' },
  { id: 'google/gemini-3.8-flash', name: 'Gemini 3.8 Flash' },
];
class InputError extends Error {
  readonly status: number;
  constructor(message: string, status = 400) { super(message); this.status = status; }
}
async function body(req: IncomingMessage): Promise<Record<string, any>> {
  if (!req.headers['content-type']?.startsWith('application/json')) throw new InputError('Send application/json.', 415);
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 32768) throw new InputError('Keep the request below 32 KiB.', 413);
    chunks.push(chunk);
  }
  try {
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    return value;
  } catch { throw new InputError('Enter valid JSON.'); }
}
function text(value: unknown, label: string, max: number): string {
  if (typeof value !== 'string' || !value.trim() || value.length > max) throw new InputError(`${label} is required (up to ${max} characters).`);
  return value.trim();
}

/** Personal keys only. Injected transport is for offline acceptance, never a client-selected endpoint. */
export function createWorkbenchServer(request: typeof fetch = fetch) {
  let active = 0;
  let catalog: Promise<Array<{ id: string; name: string; rates: { input: number; output: number }; reasoning: boolean; context: number }>> | undefined;
  let catalogAt = 0;
  const models = () => {
    if (!catalog || Date.now() - catalogAt > 300000) {
      catalogAt = Date.now();
      catalog = (async () => {
        const response = await request('https://openrouter.ai/api/v1/models', { signal: AbortSignal.timeout(20000) });
        if (!response.ok) throw new Error('Model catalog unavailable. Try again later.');
        const raw = await response.text();
        if (raw.length > 8000000) throw new Error('Model catalog exceeded its size limit.');
        const discovered = await Promise.all(choices.map(async choice => {
          try { return { ...choice, ...await openRouterModel(choice.id, async () => new Response(raw, { status: 200 })) }; }
          catch { return null; }
        }));
        return discovered.filter((value): value is NonNullable<typeof value> => value !== null);
      })().catch(error => { catalog = undefined; throw error; });
    }
    return catalog;
  };
  const server = createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https://vaticinus.com; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'");
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Cache-Control', 'no-store');
    const send = (status: number, value: unknown) => {
      if (res.destroyed) return;
      res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(value));
    };
    try {
      const asset = assets.get(req.url ?? '');
      if (req.method === 'GET' && asset) {
        const content = await readFile(new URL(asset[0], import.meta.url));
        res.writeHead(200, { 'Content-Type': asset[1] }); res.end(content); return;
      }
      if (req.method === 'GET' && req.url === '/api/models') { send(200, { models: await models() }); return; }
      if (req.method !== 'POST' || !['/api/compute', '/api/forecast'].includes(req.url ?? '')) { send(404, { error: 'Not found' }); return; }
      // No cross-origin API use, no cookies, no caller-selected provider URL.
      const origin = req.headers.origin;
      if (origin && new URL(origin).host !== req.headers.host) throw new InputError('Use this Space to submit a forecast.', 403);
      const input = await body(req);
      if (req.url === '/api/compute') {
        const spec = forecastObject(input);
        if (!['binary', 'conditional', 'bayes', 'normal'].includes(String(spec.kind))) throw new InputError('Choose a binary, conditional, Bayesian or normal model.');
        const result = computeForecast(spec);
        send(200, { result, snapshot: freezeForecastSpec(spec, result) }); return;
      }
      const apiKey = text(input.apiKey, 'Your OpenRouter key', 512);
      if (!apiKey.startsWith('sk-or-')) throw new InputError('Use an OpenRouter API key. No hosted key is available.');
      const q: ForecastQuestion = {
        id: randomUUID(), question: text(input.question, 'Question', 1000),
        resolution_date: text(input.resolutionDate, 'Resolution date', 10),
        dated_metric: text(input.resolutionRule, 'Resolution rule', 2000),
        event_type: 'occurrence', conditions: [],
      };
      validateQuestion(q);
      const now = new Date();
      if (q.resolution_date <= now.toISOString().slice(0, 10)) throw new InputError('Choose a future resolution date.');
      const evidenceText = text(input.evidence, 'Evidence or assumptions', 12000);
      if (JSON.stringify(q).includes(apiKey) || evidenceText.includes(apiKey)) throw new InputError('Remove your API key from the question and evidence.');
      const arm = input.arm;
      if (arm !== 'direct' && arm !== 'harness') throw new InputError('Choose direct or reviewed forecasting.');
      const maxCost = input.maxCost;
      if (typeof maxCost !== 'number' || !Number.isFinite(maxCost) || maxCost < 0.01 || maxCost > 0.5) throw new InputError('Set a per-forecast ceiling between $0.01 and $0.50.');
      if (active >= 4) throw new InputError('The shared workbench is busy. No model call was made; try again shortly.', 429);
      active += 1;
      let directory: string | undefined;
      let runtime: MeteredRuntime | undefined;
      const cancelled = new AbortController();
      res.on('close', () => { if (!res.writableEnded) cancelled.abort(); });
      try {
        const model = (await models()).find(value => value.id === input.model);
        if (!model) throw new InputError('Choose an available model from the list.');
        if (cancelled.signal.aborted) return;
        const captured = new Date().toISOString();
        const source: Evidence = {
          id: 'user-context', url: 'urn:vaticinus:user-provided-context', title: 'User-provided evidence and assumptions; not independently verified',
          text: evidenceText, fetched_at: captured, published_at: null, publication_basis: 'unknown',
          content_sha256: sha256(evidenceText), snapshot_sha256: sha256(evidenceText), kind: 'page',
        };
        directory = await mkdtemp(join(tmpdir(), 'vati-space-'));
        const lifetime = AbortSignal.timeout(240000);
        runtime = new MeteredRuntime({
          apiKey, model: model.id, rates: model.rates, reasoning: model.reasoning, maxCost,
          maxOutputTokens: 8400, timeoutMs: 180000, idleTimeoutMs: 45000, ledger: join(directory, 'budget.json'),
          fetch: (url, options) => request(url, { ...options, signal: AbortSignal.any([cancelled.signal, lifetime, ...(options?.signal ? [options.signal] : [])]) }),
        });
        const run = await runForecast(q, packet([source]), runtime.complete, { model: model.id, arm, asOf: captured });
        const cost = { accounted_usd: runtime.spent, ceiling_usd: maxCost, calls: runtime.traces.length,
          unknown_charge_calls: runtime.traces.filter(trace => !trace.usage).length };
        // Provider diagnostics may include request content. Never reflect the authorization secret.
        const safe = JSON.stringify({ run, cost }).split(apiKey).join('[REDACTED]');
        send(200, JSON.parse(safe));
      } finally {
        runtime?.close();
        if (directory) await rm(directory, { recursive: true, force: true });
        active -= 1;
      }
    } catch (error) {
      send(error instanceof InputError ? error.status : 400, { error: error instanceof Error ? error.message : 'The request could not be completed.' });
    }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  return server;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  createWorkbenchServer().listen(Number(process.env.PORT ?? 7860), process.env.HOST ?? '0.0.0.0', () => {
    console.log('Vaticinus workbench ready; personal OpenRouter keys only.');
  });
}
