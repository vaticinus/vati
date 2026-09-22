import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {MeteredRuntime, atomicJson, sha256} from '../../forecast-core/src/lib/runtime.ts';

const root = new URL('./', import.meta.url);
const protocol = JSON.parse(fs.readFileSync(new URL('protocol.json', root), 'utf8'));
const bytes = fs.readFileSync(new URL('manifest.json', root));
if (sha256(bytes) !== protocol.manifest_sha256 ||
    sha256(fs.readFileSync(fileURLToPath(import.meta.url))) !== protocol.runner_sha256) {
  throw new Error('Registered inputs or runner changed');
}
const manifest = JSON.parse(bytes.toString());
const sources = new Map<string, any>(manifest.sources.map((s: any) => [s.id, s]));
for (const c of manifest.cases) {
  if (c.catalog_ids.length !== 6 || c.fixed_ids.length !== 2 || new Set(c.catalog_ids).size !== 6) throw new Error('Invalid catalog');
  if (!(Date.parse(protocol.checkpoint_release) < Date.parse(c.as_of) && Date.parse(c.as_of) < Date.parse(c.resolution_at))) throw new Error('Invalid historical dates');
  for (const id of c.catalog_ids) {
    const s = sources.get(id);
    if (!s || !(s.published_at < c.as_of) || s.text.length > 1800 || sha256(s.text) !== s.text_sha256) throw new Error('Invalid evidence vintage or hash');
  }
  if (c.fixed_ids.some((id: string) => !c.catalog_ids.includes(id))) throw new Error('Fixed packet outside catalog');
}
if (!process.argv.includes('--run')) {
  console.log(JSON.stringify({paid: false, cases: manifest.cases.length, rows: manifest.cases.length * manifest.arms.length,
    provider: protocol.provider, max_total_usd: protocol.max_total_usd, max_row_usd: protocol.max_row_usd}));
  process.exit(0);
}
const ledger = process.env.STUDY_LEDGER, out = process.env.STUDY_OUT, key = process.env.OPENROUTER_API_KEY;
if (!ledger || !out || !key) throw new Error('Set STUDY_LEDGER, STUDY_OUT and OPENROUTER_API_KEY explicitly');
if (sha256(fs.readFileSync(ledger)) !== protocol.starting_ledger_sha256) throw new Error('Starting ledger changed; this registration cannot be rerun');
fs.writeFileSync(out, '{}\n', {flag: 'wx', mode: 0o600});
const report: any = {study: manifest.study, protocol_sha256: sha256(fs.readFileSync(new URL('protocol.json', root))),
  manifest_sha256: sha256(bytes), started_at: new Date().toISOString(), rows: []};
for (const [i, c] of manifest.cases.entries()) {
  const arms = [...manifest.arms.slice(i % 3), ...manifest.arms.slice(0, i % 3)];
  for (const arm of arms) report.rows.push({id: c.id, cluster: c.cluster, arm, status: 'not_issued', accounted_usd: 0});
}
atomicJson(out, report);
const request = globalThis.fetch;
globalThis.fetch = async () => {throw new Error('Unmetered network is disabled');};
const jsonReply = (raw: string) => JSON.parse(raw.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, ''));
const contract = (c: any) => ({as_of: c.as_of, question: c.question, resolution_rule: c.resolution_rule});
const packet = (ids: string[]) => ids.map(id => {
  const s = sources.get(id);
  return {id: s.id, published_at: s.published_at, title: s.title, text: s.text};
});
let stopped = false;
try {
  for (const row of report.rows) {
    if (stopped) {row.error = 'Study stopped after budget or uncertain transport failure'; continue;}
    const c = manifest.cases.find((c: any) => c.id === row.id);
    const initial = JSON.parse(fs.readFileSync(ledger, 'utf8')).spent_usd;
    const runtime = new MeteredRuntime({model: manifest.model, apiKey: key, ledger,
      rates: protocol.rates, maxCost: Math.min(protocol.max_total_usd, initial + protocol.max_row_usd),
      reasoning: false, maxOutputTokens: 1024, timeoutMs: 120000, idleTimeoutMs: 30000,
      fetch: async (url, init) => {
        if (String(url) !== 'https://openrouter.ai/api/v1/chat/completions') throw new Error('Unexpected completion endpoint');
        const body = JSON.parse(String(init?.body));
        body.provider.only = [protocol.provider];
        body.provider.quantizations = ['fp8'];
        body.temperature = 0; body.top_p = 1; body.seed = protocol.seed;
        return request(url, {...init, body: JSON.stringify(body)});
      }});
    const started = performance.now();
    row.started_at = new Date().toISOString();
    try {
      let ids = [...c.fixed_ids];
      if (row.arm === 'selected') {
        row.selection_raw = await runtime.complete(protocol.selection_system,
          JSON.stringify({...contract(c), catalog: packet(c.catalog_ids)}), `${row.id}/select`, 512);
        const selected = jsonReply(row.selection_raw);
        if (!Array.isArray(selected.ids) || selected.ids.length !== 2 || new Set(selected.ids).size !== 2 ||
            selected.ids.some((id: unknown) => typeof id !== 'string' || !c.catalog_ids.includes(id))) throw new Error('Invalid selection; no replacement or retry');
        // Pass only selected original documents, never the selector's reasoning or outcome guesses.
        ids = [...selected.ids];
      }
      ids.sort((a, b) => c.catalog_ids.indexOf(a) - c.catalog_ids.indexOf(b));
      row.selected_ids = ids;
      const user = JSON.stringify({...contract(c), evidence: packet(ids)});
      row.forecast_prompt_sha256 = sha256(protocol.forecast_system + '\n' + user);
      row.forecast_raw = await runtime.complete(protocol.forecast_system, user, `${row.id}/${row.arm}/forecast`, row.arm === 'recency_long' ? 1024 : 512);
      const answer = jsonReply(row.forecast_raw);
      if (typeof answer.p_yes !== 'number' || !Number.isFinite(answer.p_yes) || answer.p_yes < 0 || answer.p_yes > 1 || typeof answer.reason !== 'string') throw new Error('Invalid binary probability response; no retry');
      row.p_yes = answer.p_yes; row.reason = answer.reason; row.status = 'issued';
    } catch (error) {
      row.status = 'failed';
      row.error = String(error).split(key).join('[REDACTED]');
      if (runtime.terminal || (error as any)?.kind === 'budget') stopped = true;
    } finally {
      row.elapsed_ms = performance.now() - started;
      row.accounted_usd = runtime.spent - initial;
      row.traces = runtime.traces;
      stopped ||= runtime.terminal;
      runtime.close();
      atomicJson(out, report);
    }
  }
  report.finished_at = new Date().toISOString();
  report.accounted_usd = JSON.parse(fs.readFileSync(ledger, 'utf8')).spent_usd;
  atomicJson(out, report);
  console.log(JSON.stringify({planned: report.rows.length, issued: report.rows.filter((r: any) => r.status === 'issued').length,
    accounted_usd: report.accounted_usd, stopped}));
} finally {globalThis.fetch = request;}
