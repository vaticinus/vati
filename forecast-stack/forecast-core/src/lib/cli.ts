#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {MeteredRuntime,openRouterModel,atomicJson,sha256} from './runtime.ts';
import {collectEvidence,packet,type EvidencePacket} from './evidence.ts';
import {collectDataPack} from './dataPacks.ts';
import {runForecast,ForecastHistory,questionHash,validateQuestion,type ForecastQuestion,type Run,type Resolution} from './workflow.ts';
import {freezeBenchmark,runBenchmark,scoreBenchmark,type BenchmarkManifest,type BenchmarkReport} from './benchmark.ts';
import type {ForecastCompletion} from './coherence.ts';

const HELP=`Vati open forecasting workflow (Node 22.18+)

  vati demo --state ./demo                   Complete offline lifecycle; no keys or network
  vati data-fetch --input pack-config.json --out ./source-pack
  vati research --source URL --out packet.json
  vati forecast --question question.json --packet packet.json --model MODEL --max-cost USD
  vati forecast --question question.json --source URL --model MODEL --max-cost USD
  vati refresh --state ./forecasts --model MODEL --max-cost USD [--interval-hours 24]
  vati resolve --state ./forecasts --resolution resolution.json
  vati score --state ./forecasts [--policy first|latest]
  vati verify --state ./forecasts
  vati benchmark-freeze --input benchmark.json --out frozen.json
  vati benchmark-run --input frozen.json --out results.json --max-cost USD
  vati benchmark-score --input results.json --outcomes outcomes.json --out scores.json

Paid model execution requires OPENROUTER_API_KEY and a positive --max-cost.
The ceiling is cumulative across the persistent --ledger (default STATE/budget.json).
Forecasts and failures append to STATE/history.jsonl; every run is retained.
Use --endpoint with VATI_MODEL_API_KEY and explicit --input-rate/--output-rate (USD/M)
for a compatible provider. Provider keys are never written to history.
Search is optional: --search-endpoint URL --search-kind searxng|brave;
Brave uses VATI_SEARCH_API_KEY and may have its own charges outside the model budget.
Without a search service, supply --source URLs or a previously saved --packet.
Historical replay requires --as-of, --mode historical_replay and --checkpoint JSON.
All generated filenames must be new. No forecasts are submitted to third parties.`;
const strings=['state','out','input','outcomes','question','packet','model','max-cost','ledger','endpoint','input-rate','output-rate','search-endpoint','search-kind','policy','resolution','as-of','mode','checkpoint','interval-hours','supersedes'];
export async function main(argv=process.argv.slice(2)):Promise<number>{
  const parsed=parseArgs({args:argv,allowPositionals:true,strict:true,options:{help:{type:'boolean'},source:{type:'string',multiple:true},...Object.fromEntries(strings.map(s=>[s,{type:'string' as const}]))}});
  const command=parsed.positionals[0],v=parsed.values as Record<string,any>;
  if(v.help||!command){console.log(HELP);return 0;}
  const required=(name:string)=>{const x=v[name];if(typeof x!=='string'||!x)throw new Error(`Missing --${name}`);return x;};
  const json=(name:string)=>JSON.parse(fs.readFileSync(required(name),'utf8'));
  const state=path.resolve(v.state??'./forecasts'),history=()=>new ForecastHistory(state);
  const writeNew=(filename:string,value:unknown)=>{fs.mkdirSync(path.dirname(path.resolve(filename)),{recursive:true});fs.writeFileSync(filename,JSON.stringify(value,null,2)+'\n',{flag:'wx',mode:0o600});};
  const runtime=async(model:string)=>{
    const maxCost=Number(required('max-cost'));
    if(!Number.isFinite(maxCost)||maxCost<=0)throw new Error('--max-cost must be positive');
    const endpoint=v.endpoint as string|undefined;
    const key=endpoint?process.env.VATI_MODEL_API_KEY:process.env.OPENROUTER_API_KEY;
    if(!key)throw new Error(endpoint?'Set VATI_MODEL_API_KEY':'Set OPENROUTER_API_KEY');
    const config=endpoint?{rates:{input:Number(required('input-rate')),output:Number(required('output-rate'))},reasoning:false}:await openRouterModel(model);
    return new MeteredRuntime({model,apiKey:key,endpoint,maxCost,ledger:path.resolve(v.ledger??path.join(state,'budget.json')),...config});
  };
  const research=async(question?:ForecastQuestion)=>{
    if(v.packet)return json('packet') as EvidencePacket;
    const search=v['search-endpoint']?{endpoint:v['search-endpoint'],kind:v['search-kind']??'searxng',apiKey:process.env.VATI_SEARCH_API_KEY}:undefined;
    if(search&&!['searxng','brave'].includes(search.kind))throw new Error('Unknown search kind');
    if(!(v.source?.length||search))throw new Error('Supply --source, --packet or a search endpoint for actual research');
    return collectEvidence({sources:(v.source??[]).map((url:string)=>({url})),queries:question?[question.question,`${question.question} primary resolution source`]:[],search});
  };
  if(command==='data-fetch'){
    const directory=path.resolve(required('out'));if(fs.existsSync(directory))throw new Error('Data pack output directory must be new');
    const result=await collectDataPack(json('input'));
    fs.mkdirSync(directory,{recursive:true});
    for(const [name,value]of Object.entries({'pack.json':result,'question.json':result.question,'packet.json':result.evidence}))writeNew(path.join(directory,name),value);
    console.log(JSON.stringify({directory,sources:result.snapshots.length,baseline:result.question.baseline,limitations:result.metadata.limitations},null,2));return 0;
  }
  if(command==='research'){const p=await research();writeNew(required('out'),p);console.log(JSON.stringify({sources:p.sources.length,failures:p.failures}));return p.sources.length?0:1;}
  if(command==='forecast'){
    const q=json('question') as ForecastQuestion;
    validateQuestion(q);
    if(v.mode&&!['live','historical_replay'].includes(v.mode))throw new Error('CLI forecast mode must be live or historical_replay');
    if(v.mode==='historical_replay'&&!v.packet)throw new Error('Historical replay requires a previously archived packet');
    const p=await research(q),r=await runtime(required('model'));
    try{
      const run=await runForecast(q,p,r.complete,{model:required('model'),mode:v.mode,asOf:v['as-of'],supersedes:v.supersedes,checkpoint:v.checkpoint?json('checkpoint'):undefined});
      history().add(run);if(v.out)writeNew(v.out,run);
      console.log(JSON.stringify({id:run.id,status:run.status,probability:run.probability,issues:run.issues,spent_usd:r.spent,history:path.join(state,'history.jsonl')},null,2));
      return run.status==='error'||run.status==='rejected'?1:0;
    }finally{r.close();}
  }
  if(command==='refresh'){
    const h=history(),model=required('model'),hours=Number(v['interval-hours']??24);
    if(!Number.isFinite(hours)||hours<=0)throw new Error('Refresh interval must be positive');
    const events=h.read(),resolved=new Set(events.filter(e=>e.type==='resolution').map(e=>(e.payload as Resolution).question_id)),latest=new Map<string,Run>();
    for(const e of events.filter(e=>e.type==='forecast')){const r=e.payload as Run;if(r.model===model&&r.mode==='live'&&!resolved.has(r.question.id))latest.set(r.question.id+'\0'+r.arm,r);}
    const due=[...latest.values()].filter(r=>Date.parse(r.created_at)+hours*3600000<=Date.now()&&r.question.resolution_date>new Date().toISOString().slice(0,10));
    if(!due.length){console.log('No unresolved questions are due.');return 0;}
    const r=await runtime(model);let failures=0;
    try{for(const previous of due){if(r.terminal)break;
      const p=await collectEvidence({sources:previous.evidence.sources.map(s=>({url:s.url,title:s.title}))});
      fs.appendFileSync(path.join(state,'refresh-attempts.jsonl'),JSON.stringify({created_at:new Date().toISOString(),question_id:previous.question.id,supersedes:previous.id,source_count:p.sources.length,failures:p.failures})+'\n',{mode:0o600});
      if(!p.sources.length){console.error(JSON.stringify({question_id:previous.question.id,status:'research_failed',failures:p.failures}));failures++;continue;}
      const run=await runForecast(previous.question,p,r.complete,{model,arm:previous.arm,supersedes:previous.id});h.add(run);
      console.log(JSON.stringify({id:run.id,supersedes:previous.id,status:run.status,probability:run.probability}));if(run.status==='error')failures++;
    }}finally{r.close();}return failures?1:0;
  }
  if(command==='resolve'){const h=history();h.resolve(json('resolution'));console.log('Resolution appended; previous forecasts retained.');return 0;}
  if(command==='verify'){const rows=history().read();console.log(JSON.stringify({events:rows.length,head:rows.at(-1)?.hash??null,timing_proof:'local integrity only; publish the head to an independent witness'}));return 0;}
  if(command==='score'){
    if(v.policy&&!['first','latest'].includes(v.policy))throw new Error('Policy must be first or latest');
    const result=history().score(v.policy??'first');if(v.out)writeNew(v.out,result);else console.log(JSON.stringify(result,null,2));return 0;
  }
  if(command==='benchmark-freeze'){const manifest=freezeBenchmark(json('input'));writeNew(required('out'),manifest);console.log(manifest.sha256);return 0;}
  if(command==='benchmark-run'){
    const out=required('out');writeNew(out,{status:'starting'});
    const report=await runBenchmark(json('input') as BenchmarkManifest,async model=>{const r=await runtime(model);return {complete:r.complete,spent:()=>r.spent,terminal:()=>r.terminal,close:()=>r.close()};},report=>atomicJson(out,report));
    console.log(JSON.stringify({rows:report.rows.length,issued:report.rows.filter(r=>r.status==='issued').length,out}));return report.rows.some(r=>r.status==='error'||r.status==='not_attempted')?1:0;
  }
  if(command==='benchmark-score'){const result=scoreBenchmark(json('input') as BenchmarkReport,json('outcomes'));writeNew(required('out'),result);return 0;}
  if(command==='demo'){
    const h=history();if(h.read().length)throw new Error('Demo needs a new --state directory');
    const q:ForecastQuestion={id:'synthetic-project',question:'Will the synthetic project finish by 2024-12-31?',resolution_date:'2024-12-31',dated_metric:'Synthetic example: the supplied completion register lists the project as complete by the deadline.',event_type:'occurrence',conditions:[],baseline:{probability:.5,description:'Uninformed synthetic baseline'}};
    const text='Synthetic assumptions only: crisis probability 30%; completion chance 60% in a crisis and 10% otherwise. The implied completion probability is 25%.';
    const p=packet([{id:'synthetic',url:'https://example.org/synthetic-project',title:'Synthetic project assumptions',text,published_at:'2024-01-01T00:00:00Z',fetched_at:'2024-01-01T00:00:00Z',publication_basis:'user_attested',kind:'fixture',content_sha256:sha256(text),snapshot_sha256:sha256(text)}]);
    const complete:ForecastCompletion=async(_s,_u,stage)=>stage==='forecast_review'?JSON.stringify({valid:true,issues:[],no_point:false}):
      `The synthetic assumptions imply a 25% chance of completion: 0.3 × 0.6 + 0.7 × 0.1. This tests the software, not real-world forecasting skill.\n\n\`\`\`vaticinus-forecast\n${JSON.stringify({kind:'conditional',question:q.question,resolution_date:q.resolution_date,dated_metric:q.dated_metric,partition:'Crisis or no crisis',branches:[{condition:'Crisis',weight:.3,p_yes:.6},{condition:'No crisis',weight:.7,p_yes:.1}]})}\n\`\`\``;
    const first=await runForecast(q,p,complete,{model:'offline-fixture',mode:'fixture_replay',asOf:'2024-01-02T00:00:00Z'});h.add(first);
    const second=await runForecast(q,p,complete,{model:'offline-fixture',mode:'fixture_replay',asOf:'2024-01-03T00:00:00Z',supersedes:first.id});h.add(second);
    const observation='Synthetic completion register: project completed on 2024-12-15.';
    h.resolve({question_id:q.id,question_hash:questionHash(q),outcome:1,observed_at:'2024-12-31T23:59:59Z',recorded_at:new Date().toISOString(),source:{url:'https://example.org/synthetic-register',text:observation,sha256:sha256(observation)},note:'Synthetic fixture outcome; not an actual forecast.'});
    console.log(JSON.stringify({synthetic:true,paid:false,network:false,first:first.id,revision:second.id,score:h.score(),state},null,2));return 0;
  }
  throw new Error(`Unknown command: ${command}\n${HELP}`);
}
if(process.argv[1]&&fs.realpathSync(process.argv[1])===fs.realpathSync(fileURLToPath(import.meta.url)))main().then(code=>{process.exitCode=code;}).catch(error=>{console.error(String(error));process.exitCode=1;});
