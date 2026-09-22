#!/usr/bin/env node
// Prospective issue-only pilot. Outcomes are never inferred or graded here.
// Post-run safety fix: the registered historical version is commit 581be0a.
// Its protocol remains immutable; changed source requires a new registration.
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {buildSystemPrompt,loadRepoEnv} from '../src/lib/model.ts';
import {prepareForecastContract,forecastContractBlock,finalizeForecastAnswer,type ForecastCompletion} from '../src/lib/coherence.ts';
import {readForecastSnapshot} from '../src/lib/forecastSnapshot.ts';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const study=path.join(root,'benchmarks/prospective-2026-09-22');
const protocol=JSON.parse(fs.readFileSync(path.join(study,'protocol.json'),'utf8'));
const cohort=JSON.parse(fs.readFileSync(path.join(study,'cohort.json'),'utf8'));
const args=process.argv.slice(2);
const arg=(name:string)=>{const i=args.indexOf(name);if(i<0||!args[i+1])throw new Error(`Missing ${name}`);return args[i+1];};
const sha=(p:string)=>createHash('sha256').update(fs.readFileSync(p)).digest('hex');
for(const [name,expected] of Object.entries(protocol.source_sha256)) if(sha(path.join(root,name))!==expected)throw new Error(`Frozen source changed: ${name}`);
for(const model of protocol.models) if(typeof protocol.reasoning_mandatory?.[model]!=='boolean')throw new Error(`Register reasoning_mandatory from provider capabilities for ${model} before inference`);
if(!args.includes('--run')) {console.log(JSON.stringify({models:protocol.models,cases:cohort.cases.length,arms:protocol.arms,limits:protocol.limits,paid:false},null,2));process.exit(0);}
const ledgerPath=path.resolve(arg('--ledger')),out=path.resolve(arg('--out'));
if(fs.existsSync(out))throw new Error('Refusing to overwrite issued forecasts');
const now=new Date();
if(now.toISOString().slice(0,10)!==cohort.issued_date)throw new Error('Issue date changed; preregister a new packet before inference');
if(cohort.cases.some((c:any)=>Date.parse(c.scheduled_release_utc)<=now.getTime()))throw new Error('An outcome release is no longer prospective');
loadRepoEnv();
const key=process.env.OPENROUTER_API_KEY;if(!key)throw new Error('OpenRouter key missing');
const lockPath=ledgerPath+'.lock';const lock=fs.openSync(lockPath,'wx');
let ledger:any;
try {
 ledger=JSON.parse(fs.readFileSync(ledgerPath,'utf8'));
 if(!Array.isArray(ledger.calls)||!Number.isFinite(ledger.spent_upper_usd)||ledger.spent_upper_usd<0||ledger.calls.some((c:any)=>!Number.isFinite(c.cost_upper_usd)||c.cost_upper_usd<0)||Math.abs(ledger.calls.reduce((s:number,c:any)=>s+c.cost_upper_usd,0)-ledger.spent_upper_usd)>1e-8)throw new Error('Invalid shared ledger');
 if(sha(ledgerPath)!==protocol.starting_ledger_sha256)throw new Error('Shared ledger changed since registration; do not reset or silently rebase it');
} catch(e){fs.closeSync(lock);fs.unlinkSync(lockPath);throw e;}
const checkpoint=()=>{fs.writeFileSync(ledgerPath+'.tmp',JSON.stringify(ledger,null,2),{mode:0o600});fs.renameSync(ledgerPath+'.tmp',ledgerPath);};
const report:any={protocol_sha256:sha(path.join(study,'protocol.json')),started_at:now.toISOString(),outcomes:'unresolved',rows:[]};
const save=()=>{fs.writeFileSync(out+'.tmp',JSON.stringify(report,null,2),{mode:0o600});fs.renameSync(out+'.tmp',out);};
const originalFetch=globalThis.fetch;
globalThis.fetch=(async()=>{throw new Error('Unmetered network request blocked');}) as typeof fetch;
let terminal=false;
const directSystem='Forecast the exact future release event using only supplied factual evidence. You may use explicitly labelled judgmental probabilistic assumptions; uncertainty is not itself a reason to withhold a forecast. Do not invent observations or claim access to later releases. Return JSON {"probability":number_or_null,"question":"exact question","resolution_date":"YYYY-MM-DD","dated_metric":"complete settling rule","explanation":"reasoning, assumptions and uncertainty"}. A probability must be finite and between zero and one. Use null only if you cannot issue an estimate; it will count as missing, not as correct.';
try {
 for(let ci=0;ci<cohort.cases.length;ci++){
  const c=cohort.cases[ci];
  const models=[...protocol.models.slice(ci%protocol.models.length),...protocol.models.slice(0,ci%protocol.models.length)];
  for(let mi=0;mi<models.length;mi++){
   const model=models[mi],rate=protocol.rates_per_million[model];
   const arms=(ci+mi)%2?[...protocol.arms].reverse():protocol.arms;
   for(const arm of arms){
    if(terminal)break;
    const row:any={id:c.id,model,arm,probability:null,status:'pending',resolution_date:c.resolution_date,release_cluster:c.release_cluster};
    const started=Date.now();let calls=0,outputAllocated=0;
    const complete:ForecastCompletion=async(system,user,stage,requested)=>{
     if(terminal)throw new Error('Pilot stopped at a transport or spending boundary');
     const max=arm==='direct'?protocol.limits.direct_output_tokens:Math.min(requested,protocol.limits.stage_output_tokens[stage]??0);
     if(!Number.isInteger(max)||max<=0||++calls>(arm==='direct'?1:6)||(outputAllocated+=max)>(arm==='direct'?protocol.limits.direct_output_tokens:protocol.limits.harness_output_tokens))throw new Error('Registered call/output ceiling exceeded');
     const messages=[{role:'system',content:system},{role:'user',content:user}];
     const bytes=Buffer.byteLength(JSON.stringify(messages),'utf8');
     if(bytes>protocol.limits.message_bytes_per_call)throw new Error('Registered input bound exceeded');
     const reserve=(bytes+2048)*rate.prompt/1e6+max*rate.completion/1e6;
     if(ledger.spent_upper_usd+reserve>protocol.cumulative_ceiling_usd){terminal=true;throw new Error('Shared dollar ceiling reached before request');}
     const body={model,messages,max_tokens:max,temperature:0,reasoning:{enabled:protocol.reasoning_mandatory[model]||stage==='draft'||stage==='direct'},provider:{allow_fallbacks:false,require_parameters:true,max_price:rate},...(['direct','forecast_contract','forecast_review','forecast_correct'].includes(stage)?{response_format:{type:'json_object'}}:{})};
     const entry:any={study:'prospective-2026-09-22',case_id:c.id,arm,stage,at:new Date().toISOString(),model,request:body,reserved_usd:reserve,cost_upper_usd:reserve};
     ledger.spent_upper_usd+=reserve;ledger.calls.push(entry);checkpoint();
     const begin=Date.now();
     let bodyRead=false;
     try{
      const response=await originalFetch('https://openrouter.ai/api/v1/chat/completions',{method:'POST',headers:{Authorization:`Bearer ${key}`,'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(90000)});
      entry.status=response.status;
      const data=await response.json() as any;entry.returned_model=data.model;entry.returned_provider=data.provider;entry.usage=data.usage;entry.response=data.choices?.[0]?.message?.content;entry.finish_reason=data.choices?.[0]?.finish_reason;
      bodyRead=true;
      if(data.error)entry.provider_error=data.error;
      const u=data.usage;
      if(u&&Number.isFinite(u.prompt_tokens)&&u.prompt_tokens>=0&&Number.isFinite(u.completion_tokens)&&u.completion_tokens>=0){
       const actual=Math.max((u.prompt_tokens*rate.prompt+u.completion_tokens*rate.completion)/1e6,Number.isFinite(u.cost)&&u.cost>=0?u.cost:0);
       ledger.spent_upper_usd+=actual-reserve;entry.cost_upper_usd=actual;entry.reported_cost_usd=Number.isFinite(u.cost)?u.cost:null;
       if(actual>reserve+1e-9)terminal=true;
      }
      if(!response.ok)throw new Error(`OpenRouter HTTP ${response.status}; no retry`);
      if(data.choices?.[0]?.finish_reason==='length')throw new Error('Output limit reached; truncated answer is missing');
      return typeof entry.response==='string'?entry.response.trim():null;
     }catch(e){entry.error=String(e);if(!bodyRead||[401,402,403].includes(entry.status))terminal=true;throw e;}finally{entry.ms=Date.now()-begin;checkpoint();}
    };
    const request=`${c.question}\nScheduled settlement date: ${c.resolution_date}. ${cohort.resolution_policy}`;
    const user=`ISSUED ${now.toISOString()}\nREQUEST\n${request}\n\nFROZEN EVIDENCE\n${c.packet}\n\n${cohort.evidence_policy}`;
    try{
     if(arm==='direct'){
      const answer=await complete(directSystem,user,'direct',protocol.limits.direct_output_tokens);row.answer=answer;
      const parsed=JSON.parse(answer??'');row.parsed=parsed;
      if(typeof parsed.probability==='number'&&Number.isFinite(parsed.probability)&&parsed.probability>=0&&parsed.probability<=1)row.probability=parsed.probability;
     }else{
      const prepared=await prepareForecastContract(request,{context:c.packet,complete,now});row.contract=prepared.contract;
      if(prepared.clarification)row.answer=prepared.clarification;
      else{
       const draft=await complete(buildSystemPrompt(now)+(prepared.contract?'\n'+forecastContractBlock(prepared.contract):''),user,'draft',protocol.limits.stage_output_tokens.draft);row.draft=draft;
       const final=await finalizeForecastAnswer(draft??'',{request,contract:prepared.contract,context:c.packet,grounding:[c.packet],complete,now});
       row.answer=final.text;row.spec=final.spec;row.issues=final.issues;
       if(final.spec)row.probability=readForecastSnapshot(final.spec).result?.probability??null;
      }
     }
     row.status=row.probability===null?'missing':'issued';
    }catch(e){row.error=String(e);row.status='error';}
    row.ms=Date.now()-started;row.calls=calls;report.rows.push(row);report.spent_upper_usd=ledger.spent_upper_usd;report.stopped=terminal;save();
    console.log(JSON.stringify({id:c.id,model,arm,status:row.status,p:row.probability,spent_upper_usd:ledger.spent_upper_usd}));
   }
   if(terminal)break;
  }
  if(terminal)break;
 }
 report.finished_at=new Date().toISOString();save();
}finally{globalThis.fetch=originalFetch;fs.closeSync(lock);fs.unlinkSync(lockPath);}
