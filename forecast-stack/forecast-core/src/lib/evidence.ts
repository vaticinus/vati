/** Source collection shared by the CLI, experiments and downstream applications. */
import {lookup} from 'node:dns/promises';
import {isIP} from 'node:net';
import {sha256} from './runtime.ts';

export type Evidence = {
  id: string; url: string; title: string; text: string; fetched_at: string;
  published_at: string | null; publication_basis: 'source_metadata' | 'user_attested' | 'unknown';
  content_sha256: string; snapshot_sha256: string;
  kind: 'page' | 'structured' | 'search_snippet' | 'fixture';
};
export type SourceInput = {url: string; title?: string; published_at?: string};
export type ResearchOptions = {
  sources?: SourceInput[]; queries?: string[]; search?: {endpoint: string; apiKey?: string; kind: 'searxng' | 'brave'};
  maxSources?: number; maxCharsPerSource?: number; asOf?: string; historical?: boolean;
  fetch?: typeof fetch; resolve?: typeof lookup;
};
export type EvidencePacket = {schema_version: 1; collected_at: string; sources: Evidence[]; failures: {url: string; error: string}[]; sha256: string};

function forbiddenIP(ip: string): boolean {
  const s = ip.toLowerCase().replace(/^::ffff:/, '');
  if (s.includes(':')) return s === '::' || s === '::1' || /^f[cd]|^fe[89ab]|^ff/.test(s);
  const n = s.split('.').map(Number);
  return n[0] === 0 || n[0] === 10 || n[0] === 127 || n[0] >= 224 ||
    (n[0] === 169 && n[1] === 254) || (n[0] === 172 && n[1] >= 16 && n[1] <= 31) ||
    (n[0] === 192 && n[1] === 168) || (n[0] === 100 && n[1] >= 64 && n[1] <= 127);
}
export async function publicSourceUrl(value: string, resolve: typeof lookup = lookup): Promise<URL> {
  const url = new URL(value);
  const host = url.hostname.replace(/^\[|\]$/g, '');
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      /(^|\.)(localhost|local|internal)$/.test(host) || (url.port && !['80', '443'].includes(url.port)))
    throw new Error('Evidence URL must be a public HTTP(S) source without credentials');
  const addresses = isIP(host) ? [{address: host}] : await resolve(host, {all: true});
  if (!addresses.length || addresses.some(item => forbiddenIP(item.address))) throw new Error('Private/reserved evidence address rejected');
  return url;
}
export async function fetchSourceBytes(value: string, options: Pick<ResearchOptions, 'fetch' | 'resolve'> = {}): Promise<{url: string; bytes: Buffer; contentType: string}> {
  let url = await publicSourceUrl(value, options.resolve);
  for (let redirects = 0; redirects <= 4; redirects++) {
    const response = await (options.fetch ?? fetch)(url, {redirect: 'manual', signal: AbortSignal.timeout(25000),
      headers: {'User-Agent': 'Vaticinus-Open-Forecast/0.2 (+https://github.com/vaticinus/vati)', Accept: 'text/html,application/json,text/plain,application/xml'}});
    if (response.status >= 300 && response.status < 400 && response.headers.get('location')) {
      await response.body?.cancel(); url = await publicSourceUrl(new URL(response.headers.get('location')!, url).href, options.resolve); continue;
    }
    if (!response.ok) { await response.body?.cancel(); throw new Error(`Source HTTP ${response.status}`); }
    const type = response.headers.get('content-type') ?? '';
    if (/pdf|image|video|audio|octet-stream/.test(type)) { await response.body?.cancel(); throw new Error('Use a text/JSON/XML source or provide a dated extracted packet'); }
    if (!response.body) throw new Error('Source returned no body');
    const reader = response.body.getReader(), chunks: Uint8Array[] = []; let size = 0;
    try {
      for (;;) { const {done, value: chunk} = await reader.read(); if (done) break;
        size += chunk.length; if (size > 2_000_000) throw new Error('Source exceeds 2 MB limit'); chunks.push(chunk); }
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
    return {url: url.href, bytes: Buffer.concat(chunks), contentType: type};
  }
  throw new Error('Too many source redirects');
}
function unescapeText(text: string): string {
  return text.replace(/&#(\d+);/g, (_, n) => Number(n) <= 0x10ffff ? String.fromCodePoint(Number(n)) : '')
    .replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&apos;|&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ');
}
export function extractPage(text: string): {text: string; title: string; published_at: string | null} {
  const title = unescapeText(/<title[^>]*>([\s\S]*?)<\/title>/i.exec(text)?.[1] ?? '').trim();
  let published_at: string | null = null;
  // Only explicit publication metadata, never Last-Modified or the retrieval clock.
  for (const tag of text.match(/<meta\b[^>]*>/gi) ?? []) {
    if (!/(?:property|name)\s*=\s*["'](?:article:published_time|datePublished|citation_publication_date)["']/i.test(tag)) continue;
    const raw = /content\s*=\s*["']([^"']+)["']/i.exec(tag)?.[1];
    if (raw && Number.isFinite(Date.parse(raw))) published_at = new Date(raw).toISOString();
  }
  const clean = unescapeText(text.replace(/<(script|style|nav|footer)\b[^>]*>[\s\S]*?<\/\1>/gi, '')
    .replace(/<\/(p|div|h[1-6]|li|tr)>/gi, '\n').replace(/<[^>]+>/g, ' ').replace(/[\t ]+/g, ' ').replace(/\n\s*\n/g, '\n')).trim();
  return {title, text: clean, published_at};
}
export function validateEvidence(source: Evidence, asOf: string, historical = false): void {
  if (!source.url || !source.text || !source.title || !Number.isFinite(Date.parse(source.fetched_at))) throw new Error('Incomplete evidence source');
  if (!Number.isFinite(Date.parse(asOf)) || Date.parse(source.fetched_at) > Date.parse(asOf)) throw new Error('Evidence capture is after the information cutoff');
  if (source.content_sha256 !== sha256(source.text)) throw new Error(`Evidence text hash mismatch: ${source.id}`);
  if (!/^[a-f0-9]{64}$/.test(source.snapshot_sha256)) throw new Error('Evidence needs a raw snapshot hash');
  if (source.published_at && (!Number.isFinite(Date.parse(source.published_at)) || Date.parse(source.published_at) > Date.parse(asOf)))
    throw new Error(`Source publication is invalid or after the information cutoff: ${source.id}`);
  if (historical && (!source.published_at || Date.parse(source.fetched_at) > Date.parse(asOf)))
    throw new Error('Historical replay requires a dated snapshot captured by the issue cutoff; a date on a current page is insufficient');
}
export function packet(sources: Evidence[], failures: EvidencePacket['failures'] = []): EvidencePacket {
  return {schema_version: 1, collected_at: new Date().toISOString(), sources, failures,
    sha256: sha256(JSON.stringify(sources))};
}
/** Query the caller's selected search service. Search results remain discovery, not fetched evidence. */
export async function discover(queries: string[], config: NonNullable<ResearchOptions['search']>, request = fetch): Promise<SourceInput[]> {
  const base = new URL(config.endpoint);
  if (base.protocol !== 'https:' && !(base.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname))) throw new Error('Search endpoint must use HTTPS or an explicit local service');
  const found: SourceInput[] = [];
  for (const query of queries.slice(0, 3)) {
    const url = new URL(base); url.searchParams.set('q', query);
    if (config.kind === 'searxng') url.searchParams.set('format', 'json');
    else url.searchParams.set('count', '5');
    const response = await request(url, {signal: AbortSignal.timeout(20000), headers: config.kind === 'brave' ? {'X-Subscription-Token': config.apiKey ?? ''} : {}});
    if (!response.ok) throw new Error(`Search HTTP ${response.status}`);
    const data = await response.json() as any;
    const rows = config.kind === 'brave' ? data.web?.results : data.results;
    for (const row of (Array.isArray(rows) ? rows : []).slice(0, 5)) {
      if (typeof row.url === 'string' && !found.some(s => s.url === row.url)) found.push({url: row.url, title: row.title});
    }
  }
  return found;
}
export async function collectEvidence(options: ResearchOptions): Promise<EvidencePacket> {
  if (options.historical) throw new Error('Live retrieval cannot reconstruct a historical packet; load archived snapshots instead');
  const failures: EvidencePacket['failures'] = [], candidates = [...(options.sources ?? [])];
  if (options.search && options.queries?.length) {
    try { candidates.push(...await discover(options.queries, options.search, options.fetch)); }
    catch (e) { failures.push({url: options.search.endpoint, error: String(e).replace(options.search.apiKey ?? '\0', '[REDACTED]')}); }
  }
  const seen = new Set<string>(), sources: Evidence[] = [];
  for (const input of candidates.slice(0, 30)) {
    if (seen.has(input.url) || sources.length >= (options.maxSources ?? 6)) continue;
    seen.add(input.url);
    try {
      const response = await fetchSourceBytes(input.url, options), raw = response.bytes.toString('utf8');
      const extracted = response.contentType.includes('html') ? extractPage(raw) : {text: raw, title: input.title ?? new URL(input.url).hostname, published_at: null};
      const text = extracted.text.slice(0, options.maxCharsPerSource ?? 12000);
      if (text.length < 40) throw new Error('Source has insufficient readable text');
      const published_at = input.published_at ?? extracted.published_at;
      const source: Evidence = {id: sha256(response.url + '\n' + text).slice(0, 20), url: response.url,
        title: input.title ?? (extracted.title || new URL(response.url).hostname), text,
        fetched_at: new Date().toISOString(), published_at,
        publication_basis: input.published_at ? 'user_attested' : published_at ? 'source_metadata' : 'unknown',
        content_sha256: sha256(text), snapshot_sha256: sha256(response.bytes), kind: 'page'};
      validateEvidence(source, options.asOf ?? new Date().toISOString());
      if (!sources.some(s => s.content_sha256 === source.content_sha256)) sources.push(source);
    } catch (e) { failures.push({url: input.url, error: String(e)}); }
  }
  return packet(sources, failures);
}
export function formatEvidence(value: EvidencePacket): string {
  return value.sources.map(s => `[${s.id}] ${s.title}\nURL: ${s.url}\nPublished: ${s.published_at ?? 'unknown; do not assume a publication date'} (${s.publication_basis})\nCaptured: ${s.fetched_at}\n${s.text}`).join('\n\n');
}
