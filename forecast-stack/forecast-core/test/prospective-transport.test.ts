import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

function runPilot(scenario:'timeout'|'mandatory',mandatory:boolean|undefined) {
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
    const contract={forecast:true,event_type:'quantity_threshold',question:'Will the first official release tomorrow report at least 3%?',resolution_date:tomorrow.slice(0,10),dated_metric:'First official release only; later revisions excluded.',numeric_clause:{threshold:3,threshold_dir:'>=',ci_unit:'percent'},conditions:['first release only'],cruxes:[],queries:[]};
    fs.writeFileSync(path.join(root,'contract.json'),JSON.stringify(contract));
    fs.writeFileSync(path.join(study,'cohort.json'),JSON.stringify({issued_date:new Date().toISOString().slice(0,10),resolution_policy:contract.dated_metric,evidence_policy:'Synthetic offline fixture.',cases:['first','second'].map(id=>({id,question:contract.question,scheduled_release_utc:tomorrow,resolution_date:contract.resolution_date,packet:'Unknown future outcome. Judgmental normal assumptions: mean 2.6%, standard deviation 0.5 percentage points.'}))}));
    fs.writeFileSync(path.join(study,'protocol.json'),JSON.stringify({source_sha256:{[runner]:hash(fs.readFileSync(path.join(root,runner),'utf8'))},starting_ledger_sha256:hash(ledger),cumulative_ceiling_usd:1,models:['offline'],reasoning_mandatory:{offline:mandatory},arms:[scenario==='timeout'?'direct':'harness'],rates_per_million:{offline:{prompt:1,completion:1}},limits:{direct_output_tokens:10240,harness_output_tokens:10240,stage_output_tokens:{forecast_contract:1024,draft:4096,forecast_review:1024,forecast_correct:2048},message_bytes_per_call:65536}}));
    const program=`
      import fs from 'node:fs';
      let requests=0,error=null;
      const contract=JSON.parse(fs.readFileSync('contract.json','utf8'));
      const spec={...contract,kind:'normal',mean:2.6,sd:.5,threshold:3,threshold_dir:'>=',ci_unit:'percent'};
      const explanation='Judgmental normal assumptions from the packet; only the first official release counts.';
      const fence=String.fromCharCode(96).repeat(3);
      const answers=[JSON.stringify(contract),explanation+'\\n'+fence+'vaticinus-forecast\\n'+JSON.stringify({...spec,threshold:2})+'\\n'+fence,JSON.stringify({answer:explanation,spec}),JSON.stringify({valid:true,issues:[]})];
      globalThis.fetch=async(_url,init)=>{
        const index=requests++;
        if(process.env.SCENARIO==='timeout')return new Response(new ReadableStream({pull(controller){controller.error(new DOMException('simulated body timeout','TimeoutError'));}}),{status:200});
        if(JSON.parse(init.body).reasoning?.enabled===false)return Response.json({error:{code:400,message:'Reasoning is mandatory'}},{status:400});
        return Response.json({choices:[{message:{content:answers[index%answers.length]},finish_reason:'stop'}],usage:{prompt_tokens:1,completion_tokens:1,cost:0}});
      };
      process.argv=['node',process.env.RUNNER,'--run','--ledger','budget.json','--out','report.json'];
      // Runtime-selected temporary module; fetch must be intercepted before its top-level execution.
      try{await import(process.env.RUNNER);}catch(e){error=String(e);process.exitCode=1;}
      console.log('OBSERVED:'+JSON.stringify({requests,error,report:fs.existsSync('report.json')?JSON.parse(fs.readFileSync('report.json','utf8')):null,ledger:JSON.parse(fs.readFileSync('budget.json','utf8')),lock:fs.existsSync('budget.json.lock')}));
    `;
    const child=spawnSync(process.execPath,['--experimental-strip-types','--input-type=module','-e',program],{cwd:root,encoding:'utf8',timeout:20000,env:{...process.env,OPENROUTER_API_KEY:'offline-not-a-real-key',RUNNER:path.join(root,runner),SCENARIO:scenario}});
    const line=child.stdout.split('\n').find(line=>line.startsWith('OBSERVED:'));
    assert.ok(line,child.stderr);
    return {exit:child.status,...JSON.parse(line.slice(9))};
  }finally{fs.rmSync(root,{recursive:true,force:true});}
}

test('a response-body timeout after HTTP 200 stops subsequent paid attempts and retains the reservation',()=>{
  const observed=runPilot('timeout',false);
  assert.equal(observed.exit,0,observed.error);
  assert.equal(observed.requests,1,'no second paid request after a body transport failure');
  assert.equal(observed.report.stopped,true);
  assert.equal(observed.report.rows.length,1);
  assert.equal(observed.report.rows[0].status,'error');
  assert.ok(observed.ledger.spent_upper_usd>0,'ambiguous charge remains reserved');
  assert.equal(observed.lock,false);
});

test('a mandatory-reasoning provider can complete the harness including event correction',()=>{
  const observed=runPilot('mandatory',true);
  assert.equal(observed.exit,0,observed.error);
  for(const row of observed.report.rows){
    assert.equal(row.status,'issued',row.error);
    assert.ok(row.probability>.21&&row.probability<.22,'probability is computed for the corrected 3% event');
  }
  assert.equal(observed.report.rows.length,2);
});

test('unregistered reasoning capabilities cannot authorize a paid request',()=>{
  const observed=runPilot('mandatory',undefined);
  assert.equal(observed.exit,1);
  assert.equal(observed.requests,0);
  assert.equal(observed.ledger.spent_upper_usd,0);
  assert.equal(observed.lock,false);
});
