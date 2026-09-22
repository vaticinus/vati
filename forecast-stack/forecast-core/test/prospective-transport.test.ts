import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

test('a response-body timeout after HTTP 200 stops subsequent paid attempts and retains the reservation',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'prospective-transport-'));
  try{
    const source=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
    fs.mkdirSync(path.join(root,'forecast-core/scripts'),{recursive:true});
    fs.cpSync(path.join(source,'src'),path.join(root,'forecast-core/src'),{recursive:true});
    const runner='forecast-core/scripts/prospective_eval.mts';
    fs.copyFileSync(path.join(source,'scripts/prospective_eval.mts'),path.join(root,runner));
    const study=path.join(root,'benchmarks/prospective-2026-09-22');fs.mkdirSync(study,{recursive:true});
    const hash=(value:string)=>createHash('sha256').update(value).digest('hex');
    const ledger=JSON.stringify({spent_upper_usd:0,calls:[]});fs.writeFileSync(path.join(root,'budget.json'),ledger);
    const tomorrow=new Date(Date.now()+86400000).toISOString();
    fs.writeFileSync(path.join(study,'cohort.json'),JSON.stringify({issued_date:new Date().toISOString().slice(0,10),resolution_policy:'First observed outcome only.',evidence_policy:'Synthetic offline fixture.',cases:['first','second'].map(id=>({id,question:'Will the event occur tomorrow?',scheduled_release_utc:tomorrow,resolution_date:tomorrow.slice(0,10),packet:'Unknown future outcome.'}))}));
    fs.writeFileSync(path.join(study,'protocol.json'),JSON.stringify({source_sha256:{[runner]:hash(fs.readFileSync(path.join(root,runner),'utf8'))},starting_ledger_sha256:hash(ledger),cumulative_ceiling_usd:1,models:['offline'],arms:['direct'],rates_per_million:{offline:{prompt:1,completion:1}},limits:{direct_output_tokens:10,message_bytes_per_call:65536}}));
    const program=`
      import fs from 'node:fs';
      let requests=0;
      globalThis.fetch=async()=>{requests++;return new Response(new ReadableStream({pull(controller){controller.error(new DOMException('simulated body timeout','TimeoutError'));}}),{status:200});};
      process.argv=['node',process.env.RUNNER,'--run','--ledger','budget.json','--out','report.json'];
      // Runtime-selected temporary module; fetch must be intercepted before its top-level execution.
      await import(process.env.RUNNER);
      console.log('OBSERVED:'+JSON.stringify({requests,report:JSON.parse(fs.readFileSync('report.json','utf8')),ledger:JSON.parse(fs.readFileSync('budget.json','utf8')),lock:fs.existsSync('budget.json.lock')}));
    `;
    const child=spawnSync(process.execPath,['--experimental-strip-types','--input-type=module','-e',program],{cwd:root,encoding:'utf8',timeout:20000,env:{...process.env,OPENROUTER_API_KEY:'offline-not-a-real-key',RUNNER:path.join(root,runner)}});
    assert.equal(child.status,0,child.stderr);
    const observed=JSON.parse(child.stdout.split('\n').find(line=>line.startsWith('OBSERVED:'))!.slice(9));
    assert.equal(observed.requests,1,'no second paid request after a body transport failure');
    assert.equal(observed.report.stopped,true);
    assert.equal(observed.report.rows.length,1);
    assert.equal(observed.report.rows[0].status,'error');
    assert.ok(observed.ledger.spent_upper_usd>0,'ambiguous charge remains reserved');
    assert.equal(observed.lock,false);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
