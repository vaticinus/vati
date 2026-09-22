/** Metered, provider-neutral completion transport. No environment loading or funding fallback. */
import fs from 'node:fs';
import path from 'node:path';
import {createHash, randomUUID} from 'node:crypto';
import type {ForecastCompletion} from './coherence.ts';

export type Rates = {input: number; output: number}; // USD per million tokens
export type CompletionTrace = {
  id: string; model: string; stage: string; started_at: string; finished_at?: string;
  status: 'reserved' | 'complete' | 'error'; input: {system: string; user: string};
  max_tokens: number; reservation_usd: number; accounted_usd: number;
  response?: string; error?: string; http_status?: number; finish_reason?: string;
  usage?: {prompt_tokens: number; completion_tokens: number; cost?: number};
};
type Ledger = {schema_version: 1; spent_usd: number; calls: CompletionTrace[]};
export class RuntimeError extends Error {
  readonly kind: 'budget' | 'transport' | 'provider' | 'output' | 'configuration';
  constructor(message: string, kind: RuntimeError['kind']) {
    super(message); this.name = 'RuntimeError'; this.kind = kind;
  }
}
export const sha256 = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n >= 0;
export function atomicJson(filename: string, value: unknown): void {
  fs.mkdirSync(path.dirname(path.resolve(filename)), {recursive: true});
  const temp = `${filename}.${randomUUID()}.tmp`;
  try { fs.writeFileSync(temp, JSON.stringify(value, null, 2) + '\n', {mode: 0o600, flag: 'wx'}); fs.renameSync(temp, filename); }
  finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
}

export type RuntimeOptions = {
  model: string; apiKey: string; endpoint?: string; rates: Rates;
  maxCost: number; ledger: string; timeoutMs?: number; idleTimeoutMs?: number;
  reasoning?: boolean; maxOutputTokens?: number;
  /** Dependency injection for offline tests or an explicitly configured local provider. */
  fetch?: typeof fetch;
};

/** Read-only discovery. Price caps remain explicit; discovery is not spending authorization. */
export async function openRouterModel(model: string, request = fetch): Promise<{rates: Rates; reasoning: boolean; context: number}> {
  const response = await request('https://openrouter.ai/api/v1/models', {signal: AbortSignal.timeout(20000)});
  if (!response.ok) throw new RuntimeError(`Model catalog HTTP ${response.status}`, 'configuration');
  const data = await response.json() as {data?: Array<Record<string, any>>};
  const item = data.data?.find(x => x.id === model);
  if (!item) throw new RuntimeError(`Model is absent from the provider catalog: ${model}`, 'configuration');
  const input = Number(item.pricing?.prompt) * 1e6, output = Number(item.pricing?.completion) * 1e6;
  if (!finite(input) || !finite(output)) throw new RuntimeError('Model prices are unavailable', 'configuration');
  // This executor meters text tokens only; do not silently accept per-request/tool/image fees.
  for (const [name, amount] of Object.entries(item.pricing ?? {})) {
    if (!['prompt', 'completion', 'input_cache_read', 'input_cache_write', 'discount'].includes(name) && Number(amount) > 0)
      throw new RuntimeError(`Unsupported additional model charge: ${name}`, 'configuration');
  }
  return {rates: {input, output}, reasoning: item.supported_parameters?.includes('reasoning') === true,
    context: Number(item.context_length) || 0};
}

export class MeteredRuntime {
  private readonly options: RuntimeOptions;
  private ledger: Ledger;
  private lock: number;
  private closed = false;
  private busy = false;
  private stopped = false;
  private readonly request: typeof fetch;
  private readonly url: URL;
  readonly traceStart: number;
  constructor(options: RuntimeOptions) {
    this.options = options;
    if (!options.model.trim() || !options.apiKey.trim()) throw new RuntimeError('A model and personal API key are required', 'configuration');
    if (!finite(options.maxCost) || options.maxCost <= 0 || !finite(options.rates.input) || !finite(options.rates.output))
      throw new RuntimeError('Set a positive cumulative spending ceiling and nonnegative token rates', 'configuration');
    this.url = new URL(options.endpoint ?? 'https://openrouter.ai/api/v1/chat/completions');
    if (this.url.username || this.url.password || (this.url.protocol !== 'https:' && !(this.url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(this.url.hostname))))
      throw new RuntimeError('Provider URL must use HTTPS (HTTP allowed only for an explicit local provider)', 'configuration');
    this.request = options.fetch ?? fetch;
    fs.mkdirSync(path.dirname(path.resolve(options.ledger)), {recursive: true});
    this.lock = fs.openSync(options.ledger + '.lock', 'wx', 0o600);
    try {
      this.ledger = fs.existsSync(options.ledger) ? JSON.parse(fs.readFileSync(options.ledger, 'utf8')) : {schema_version: 1, spent_usd: 0, calls: []};
      const l = this.ledger;
      if (l.schema_version !== 1 || !finite(l.spent_usd) || !Array.isArray(l.calls) || l.calls.some(c => !finite(c.accounted_usd)) ||
          Math.abs(l.calls.reduce((sum, c) => sum + c.accounted_usd, 0) - l.spent_usd) > 1e-8)
        throw new RuntimeError('Invalid budget ledger; do not reset it to recover allowance', 'configuration');
      this.traceStart = l.calls.length;
      this.save();
    } catch (error) { fs.closeSync(this.lock); fs.unlinkSync(options.ledger + '.lock'); throw error; }
  }
  private save() { atomicJson(this.options.ledger, this.ledger); }
  get spent(): number { return this.ledger.spent_usd; }
  get traces(): CompletionTrace[] { return structuredClone(this.ledger.calls.slice(this.traceStart)); }
  get terminal(): boolean { return this.stopped; }
  close(): void {
    if (this.busy) throw new RuntimeError('Cannot close during an active completion', 'configuration');
    if (!this.closed) { this.closed = true; fs.closeSync(this.lock); fs.unlinkSync(this.options.ledger + '.lock'); }
  }
  readonly complete: ForecastCompletion = async (system, user, stage, requested) => {
    if (this.closed || this.busy || this.stopped) throw new RuntimeError('Runtime closed, busy, or stopped after an uncertain charge', 'configuration');
    // Reasoning consumes the same output allowance; a tiny review cap can starve the answer.
    const max = Math.min(this.options.reasoning ? Math.max(requested,8400) : requested, this.options.maxOutputTokens ?? 8400);
    if (!Number.isInteger(max) || max <= 0) throw new RuntimeError('Invalid output-token ceiling', 'configuration');
    const messages = [{role: 'system', content: system}, {role: 'user', content: user}];
    const bytes = Buffer.byteLength(JSON.stringify(messages), 'utf8');
    if (bytes > 300000) throw new RuntimeError('Input exceeds the executor message limit', 'configuration');
    // A byte bound plus framing allowance deliberately over-reserves ordinary text tokenization.
    const reserve = ((bytes + 4096) * this.options.rates.input + max * this.options.rates.output) / 1e6;
    if (this.spent + reserve > this.options.maxCost + 1e-12) throw new RuntimeError('Cumulative spending ceiling reached before request', 'budget');
    const trace: CompletionTrace = {id: randomUUID(), model: this.options.model, stage, started_at: new Date().toISOString(),
      input: {system, user}, max_tokens: max, reservation_usd: reserve, accounted_usd: reserve, status: 'reserved'};
    this.ledger.calls.push(trace); this.ledger.spent_usd += reserve; this.save();
    this.busy = true;
    const controller = new AbortController();
    const totalTimer = setTimeout(() => controller.abort(new Error('Total completion timeout')), this.options.timeoutMs ?? 240000);
    let idleTimer: ReturnType<typeof setTimeout>;
    const touch = () => { clearTimeout(idleTimer); idleTimer = setTimeout(() => controller.abort(new Error('Completion stream idle timeout')), this.options.idleTimeoutMs ?? 60000); };
    touch();
    let bodyComplete = false;
    let reportedUsage: any;
    let output = '';
    const consume = (data: any) => {
      if (data.error) throw new RuntimeError(`Provider error: ${JSON.stringify(data.error).slice(0, 1600)}`, 'provider');
      if (data.usage) reportedUsage = data.usage;
      const choice = data.choices?.[0];
      if (typeof choice?.delta?.content === 'string') output += choice.delta.content;
      if (typeof choice?.message?.content === 'string') output = choice.message.content;
      if (choice?.finish_reason) trace.finish_reason = choice.finish_reason;
      if (output.length > 2_000_000) throw new RuntimeError('Completion exceeded response size limit', 'output');
    };
    try {
      const body: Record<string, unknown> = {model: this.options.model, messages, max_tokens: max, stream: true, stream_options: {include_usage: true}};
      if (this.url.hostname === 'openrouter.ai') {
        body.provider = {allow_fallbacks: false, require_parameters: true,
          max_price: {prompt: this.options.rates.input, completion: this.options.rates.output}};
        // Never disable a model's required reasoning on contract/review stages.
        if (this.options.reasoning) body.reasoning = {enabled: true};
      }
      const response = await this.request(this.url, {method: 'POST', signal: controller.signal,
        headers: {Authorization: `Bearer ${this.options.apiKey}`, 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      trace.http_status = response.status;
      touch();
      const readBounded=async()=>{
        if(!response.body)throw new RuntimeError('Provider returned no body','transport');
        const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
        try{for(;;){const {done,value}=await reader.read();if(done)break;touch();size+=value.length;if(size>2_000_000)throw new RuntimeError('Provider body too large','transport');chunks.push(value);}}
        finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
        return Buffer.concat(chunks).toString('utf8');
      };
      if (!response.ok) {
        const text = (await readBounded()).slice(0, 3000); bodyComplete = true;
        throw new RuntimeError(`Provider HTTP ${response.status}: ${text}`, 'provider');
      }
      if (response.headers.get('content-type')?.includes('text/event-stream')) {
        if (!response.body) throw new RuntimeError('Provider returned no response stream', 'transport');
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let buffer = '', doneMarker = false;
        const processEvent = (event: string) => {
          const payload = event.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
          if (!payload) return;
          if (payload === '[DONE]') { doneMarker = true; return; }
          consume(JSON.parse(payload));
        };
        try {
          for (;;) {
            const {done, value} = await reader.read();
            if (done) break;
            touch(); buffer = (buffer + decoder.decode(value, {stream: true})).replace(/\r\n/g, '\n');
            if (buffer.length > 2_000_000) throw new RuntimeError('Provider stream event too large', 'transport');
            let boundary: number;
            while ((boundary = buffer.indexOf('\n\n')) >= 0) { processEvent(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); }
          }
          buffer += decoder.decode(); if (buffer.trim()) processEvent(buffer.trim());
          bodyComplete = doneMarker || Boolean(trace.finish_reason);
          if (!bodyComplete) throw new RuntimeError('Provider stream ended before a completion marker', 'transport');
        } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      } else { consume(JSON.parse(await readBounded())); bodyComplete = true; }
      trace.response = output;
      if (trace.finish_reason === 'length' || trace.finish_reason === 'content_filter')
        throw new RuntimeError(`Unusable completion: ${trace.finish_reason}`, 'output');
      if (!output.trim()) throw new RuntimeError('Provider produced no answer text', 'output');
      trace.status = 'complete';
      return output.trim();
    } catch (error) {
      trace.status = 'error'; trace.response = output;
      trace.error = String(error).split(this.options.apiKey).join('[REDACTED]');
      // Unknown/partial charges must remain reserved; stop rather than multiply them.
      if (!bodyComplete || [401, 402, 403, 429].includes(trace.http_status ?? 0)) this.stopped = true;
      throw new RuntimeError(trace.error, error instanceof RuntimeError ? error.kind : 'transport');
    } finally {
      clearTimeout(totalTimer); clearTimeout(idleTimer!);
      if (reportedUsage && finite(reportedUsage.prompt_tokens) && finite(reportedUsage.completion_tokens)) {
        trace.usage = {prompt_tokens: reportedUsage.prompt_tokens, completion_tokens: reportedUsage.completion_tokens,
          ...(finite(reportedUsage.cost) ? {cost: reportedUsage.cost} : {})};
        const accounted = Math.max((reportedUsage.prompt_tokens * this.options.rates.input + reportedUsage.completion_tokens * this.options.rates.output) / 1e6,
          finite(reportedUsage.cost) ? reportedUsage.cost : 0);
        // Only release a reservation after a complete response with actual usage.
        if (bodyComplete) { this.ledger.spent_usd += accounted - trace.accounted_usd; trace.accounted_usd = accounted; }
        else if (accounted > trace.accounted_usd) { this.ledger.spent_usd += accounted - trace.accounted_usd; trace.accounted_usd = accounted; }
        if (accounted > reserve + 1e-9) this.stopped = true;
      }
      trace.finished_at = new Date().toISOString(); this.busy = false; this.save();
    }
  };
}
