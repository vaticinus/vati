import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { computeForecast, forecastObject } from '../forecast-core/src/lib/forecastEngine.ts';
import { freezeForecastSpec } from '../forecast-core/src/lib/forecastSnapshot.ts';

const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
  ['/style.css', ['style.css', 'text/css; charset=utf-8']],
]);
const server = createServer(async (req, res) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'");
  res.setHeader('Cache-Control', 'no-store');
  const send = (status: number, value: unknown) => {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(value));
  };
  try {
    const asset = assets.get(req.url ?? '');
    if (req.method === 'GET' && asset) {
      const content = await readFile(new URL(asset[0], import.meta.url));
      res.writeHead(200, { 'Content-Type': asset[1] });
      res.end(content);
      return;
    }
    if (req.method !== 'POST' || req.url !== '/api/compute') { send(404, { error: 'Not found' }); return; }
    if (!req.headers['content-type']?.startsWith('application/json')) { send(415, { error: 'Send application/json' }); return; }
    const chunks: Buffer[] = [];
    let size = 0;
    for await (const chunk of req) {
      size += chunk.length;
      if (size > 32768) { send(413, { error: 'Use a model smaller than 32 KiB' }); return; }
      chunks.push(chunk);
    }
    const spec = forecastObject(JSON.parse(Buffer.concat(chunks).toString('utf8')));
    // This shared public demo offers bounded analytical models, not arbitrary simulations.
    if (!['binary', 'conditional', 'bayes', 'normal'].includes(String(spec.kind))) {
      send(400, { error: 'Choose binary, conditional, bayes or normal. Growth simulations are available locally in forecast-core.' }); return;
    }
    const result = computeForecast(spec);
    send(200, { result, snapshot: freezeForecastSpec(spec, result) });
  } catch (error) {
    send(400, { error: error instanceof Error ? error.message : 'Invalid model' });
  }
});
server.requestTimeout = 15000;
server.headersTimeout = 10000;
server.listen(Number(process.env.PORT ?? 7860), process.env.HOST ?? '0.0.0.0', () => {
  console.log(`Forecast workbench listening on port ${process.env.PORT ?? 7860}; no LLM or persistent storage.`);
});
