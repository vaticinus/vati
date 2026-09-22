import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const runner = fileURLToPath(new URL("../scripts/forecast_eval.mts", import.meta.url));

test("an ambiguous transport charge survives restart and prevents another request", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "forecast-budget-"));
  try {
    const ledger = path.join(dir, "budget.json");
    const calls = path.join(dir, "requests.txt");
    const loader = path.join(dir, "transport.mjs");
    fs.writeFileSync(loader, `import fs from 'node:fs';
      globalThis.fetch=async()=>{fs.appendFileSync(${JSON.stringify(calls)},'request\\n');throw new Error('ambiguous transport failure');};`);
    const run = (cap: number, output: string) => spawnSync(process.execPath, [
      "--import", loader, "--experimental-strip-types", runner,
      "--provider", "openrouter", "--model", "xiaomi/mimo-v2.6-pro",
      "--arms", "direct_thinking", "--case", "state-mixture",
      "--max-cost", String(cap), "--ledger", ledger, "--out", path.join(dir, output),
    ], { env: { ...process.env, OPENROUTER_API_KEY: "offline-test-key" }, encoding: "utf8" });
    const first = run(0.03, "first.json");
    assert.equal(first.status, 0, first.stderr);
    const charged = JSON.parse(fs.readFileSync(ledger, "utf8"));
    assert.ok(charged.spent_upper_usd > 0);
    assert.equal(charged.spent_upper_usd, charged.calls[0].reserved_usd);
    assert.equal(charged.calls[0].cost_upper_usd, charged.calls[0].reserved_usd);
    const second = run(charged.spent_upper_usd + 0.000001, "second.json");
    assert.notEqual(second.status, 0);
    assert.equal(fs.readFileSync(calls, "utf8"), "request\n");
    assert.deepEqual(JSON.parse(fs.readFileSync(ledger, "utf8")), charged);
    assert.equal(JSON.parse(fs.readFileSync(path.join(dir, "second.json"), "utf8")).complete, false);
    assert.equal(fs.existsSync(ledger + ".lock"), false);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test("malformed budget state cannot authorize a paid request", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "forecast-budget-invalid-"));
  try {
    const ledger = path.join(dir, "budget.json");
    fs.writeFileSync(ledger, JSON.stringify({ spent_upper_usd: 0, calls: [{ cost_upper_usd: 1 }] }));
    const result = spawnSync(process.execPath, ["--experimental-strip-types", runner,
      "--provider", "openrouter", "--case", "state-mixture", "--max-cost", "0.01",
      "--ledger", ledger, "--out", path.join(dir, "result.json")],
      { env: { ...process.env, OPENROUTER_API_KEY: "offline-test-key" }, encoding: "utf8" });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /Invalid budget ledger/);
    assert.equal(fs.existsSync(ledger + ".lock"), false);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
