/** Frozen input manifests and paired scoring for interchangeable models/harnesses. */
import type {ForecastCompletion} from './coherence.ts';
import {runForecast, canonical, validateQuestion, type ForecastQuestion, type Run} from './workflow.ts';
import {validateEvidence, type EvidencePacket} from './evidence.ts';
import {sha256} from './runtime.ts';
import {readFileSync} from 'node:fs';

function implementation(){
  const extension=new URL(import.meta.url).pathname.endsWith('.ts')?'.ts':'.js';
  return Object.fromEntries(['benchmark','workflow','runtime','coherence','model','evidence','forecastEngine','forecastSnapshot','forecastUnits','mc'].map(name=>[name,sha256(readFileSync(new URL('./'+name+extension,import.meta.url)))]));
}

export type BenchmarkCase = {question: ForecastQuestion; evidence: EvidencePacket; as_of: string; cluster: string};
export type BenchmarkManifest = {schema_version: 1; name: string; mode: Run['mode']; cases: BenchmarkCase[];
  models: string[]; arms: Run['arm'][]; checkpoint?: Record<string, NonNullable<Run['checkpoint']>>;
  created_at: string; implementation:Record<string,string>; primary_metric: 'paired_brier'; missingness: 'coverage_and_bounds'; sha256: string};
export type BenchmarkRow = {case_id: string; cluster: string; model: string; arm: Run['arm']; status: Run['status'] | 'not_attempted';
  probability: number | null; elapsed_ms: number; cost_usd: number | null; run?: Run; error?: string};
export type BenchmarkReport = {schema_version: 1; manifest: BenchmarkManifest; started_at: string; finished_at?: string; rows: BenchmarkRow[]};

export function freezeBenchmark(input: Omit<BenchmarkManifest, 'schema_version' | 'created_at' | 'sha256' | 'primary_metric' | 'missingness' | 'implementation'>): BenchmarkManifest {
  if (!input.name || !input.cases.length || !input.models.length || !input.arms.length) throw new Error('Benchmark requires named cases, models and arms');
  if(!['live','historical_replay','fixture_replay'].includes(input.mode))throw new Error('Invalid benchmark mode');
  if (new Set(input.models).size !== input.models.length || new Set(input.arms).size !== input.arms.length || input.arms.some(x => !['direct','harness'].includes(x))) throw new Error('Duplicate models/arms or invalid arm');
  const ids = new Set<string>();
  for (const c of input.cases) {
    validateQuestion(c.question);
    if (!c.cluster || !Number.isFinite(Date.parse(c.as_of)) || c.question.resolution_date <= c.as_of.slice(0,10)) throw new Error('Invalid case cluster/time');
    if (ids.has(c.question.id)) throw new Error('Duplicate benchmark question ID'); ids.add(c.question.id);
    if (c.evidence.sha256 !== sha256(JSON.stringify(c.evidence.sources)) || !c.evidence.sources.length) throw new Error('Invalid frozen evidence packet');
    for (const s of c.evidence.sources) validateEvidence(s,c.as_of,input.mode==='historical_replay');
    if (input.mode==='historical_replay') for (const model of input.models) {
      const cp=input.checkpoint?.[model];
      if (!cp?.revision || !cp.provenance || !Number.isFinite(Date.parse(cp.released_at)) || Date.parse(cp.released_at)>=Date.parse(c.as_of)) throw new Error('Every historical model requires checkpoint provenance before every issue date');
    }
  }
  const raw={...structuredClone(input),implementation:implementation(),schema_version:1 as const,created_at:new Date().toISOString(),primary_metric:'paired_brier' as const,missingness:'coverage_and_bounds' as const};
  return {...raw,sha256:sha256(canonical(raw))};
}
export function verifyBenchmark(manifest:BenchmarkManifest):void {
  const {sha256:expected,...raw}=manifest;
  if(expected!==sha256(canonical(raw)))throw new Error('Benchmark manifest changed after freezing');
}
export async function runBenchmark(manifest:BenchmarkManifest,adapter:(model:string)=>Promise<{
  complete:ForecastCompletion;spent:()=>number;terminal:()=>boolean;close:()=>void}>,save:(report:BenchmarkReport)=>void):Promise<BenchmarkReport>{
  verifyBenchmark(manifest);
  if(canonical(manifest.implementation)!==canonical(implementation()))throw new Error('Execution source changed since registration; freeze a new study');
  const report:BenchmarkReport={schema_version:1,manifest,started_at:new Date().toISOString(),rows:[]};
  for(const c of manifest.cases)for(const model of manifest.models)for(const arm of manifest.arms)
    report.rows.push({case_id:c.question.id,cluster:c.cluster,model,arm,status:'not_attempted',probability:null,elapsed_ms:0,cost_usd:null});
  save(report);
  let stop=false;
  for(const model of manifest.models){
    if(stop)break;
    let runtime:Awaited<ReturnType<typeof adapter>>;
    try{runtime=await adapter(model);}catch(error){for(const r of report.rows.filter(r=>r.model===model))r.error=String(error);save(report);continue;}
    try{
      for(let ci=0;ci<manifest.cases.length&&!stop;ci++){
        const c=manifest.cases[ci],arms=ci%2?[...manifest.arms].reverse():manifest.arms;
        for(const arm of arms){
          if(stop)break;
          const row=report.rows.find(r=>r.case_id===c.question.id&&r.model===model&&r.arm===arm)!;
          const before=runtime.spent(),started=Date.now();
          try{
            const run=await runForecast(c.question,c.evidence,runtime.complete,{model,arm,mode:manifest.mode,asOf:c.as_of,checkpoint:manifest.checkpoint?.[model]});
            Object.assign(row,{run,status:run.status,probability:run.probability});
          }catch(error){row.status='error';row.error=String(error);}
          row.cost_usd=runtime.spent()-before;row.elapsed_ms=Date.now()-started;stop=runtime.terminal();save(report);
        }
      }
    }finally{runtime.close();}
  }
  report.finished_at=new Date().toISOString();save(report);return report;
}
function mean(values:number[]):number|null{return values.length?values.reduce((a,b)=>a+b,0)/values.length:null;}
/** Cluster bootstrap is descriptive on exposed fixtures; it cannot create independent events. */
function pairedInterval(rows:{cluster:string;delta:number}[]):[number,number]|null{
  const clusters=[...new Set(rows.map(r=>r.cluster))];if(clusters.length<5)return null;
  const groups=clusters.map(id=>rows.filter(r=>r.cluster===id).map(r=>r.delta));
  let seed=48271;const random=()=>{seed=(seed*16807)%2147483647;return seed/2147483647;};
  const samples:number[]=[];
  for(let i=0;i<2000;i++){const sampled:number[]=[];for(let j=0;j<groups.length;j++)sampled.push(...groups[Math.floor(random()*groups.length)]);samples.push(mean(sampled)!);}
  samples.sort((a,b)=>a-b);return [samples[50],samples[1949]];
}
export function scoreBenchmark(report:BenchmarkReport,outcomes:Record<string,0|1>){
  verifyBenchmark(report.manifest);
  for(const [id,y]of Object.entries(outcomes))if(!report.manifest.cases.some(c=>c.question.id===id)||typeof y!=='number'||![0,1].includes(y))throw new Error('Outcomes must name registered cases and contain only 0/1');
  const keys=new Set<string>();
  for(const row of report.rows){
    const c=report.manifest.cases.find(c=>c.question.id===row.case_id);
    if(!c||c.cluster!==row.cluster||!report.manifest.models.includes(row.model)||!report.manifest.arms.includes(row.arm))throw new Error('Unregistered benchmark row');
    if(!['issued','abstained','rejected','error','not_attempted'].includes(row.status)||!Number.isFinite(row.elapsed_ms)||row.elapsed_ms<0||row.cost_usd!==null&&(!Number.isFinite(row.cost_usd)||row.cost_usd<0))throw new Error('Invalid benchmark status/cost/time');
    if(row.status==='issued' ? typeof row.probability!=='number'||!Number.isFinite(row.probability)||row.probability<0||row.probability>1 : row.probability!==null)throw new Error('Invalid benchmark probability/status');
    const k=canonical([row.case_id,row.model,row.arm]);if(keys.has(k))throw new Error('Duplicate benchmark row');keys.add(k);
  }
  const expected=report.manifest.cases.length*report.manifest.models.length*report.manifest.arms.length;
  if(report.rows.length!==expected)throw new Error('Benchmark report omits registered attempts');
  const groups=[];
  for(const model of report.manifest.models)for(const arm of report.manifest.arms){
    const rows=report.rows.filter(r=>r.model===model&&r.arm===arm),resolved=rows.filter(r=>Object.hasOwn(outcomes,r.case_id));
    const issued=resolved.filter(r=>r.status==='issued'&&typeof r.probability==='number'&&Number.isFinite(r.probability)&&r.probability>=0&&r.probability<=1);
    const losses=issued.map(r=>(r.probability!-outcomes[r.case_id])**2),sum=losses.reduce((a,b)=>a+b,0),missing=resolved.length-issued.length;
    const baselines=issued.flatMap(r=>{const c=report.manifest.cases.find(c=>c.question.id===r.case_id)!;return c.question.baseline?[(c.question.baseline.probability-outcomes[r.case_id])**2]:[];});
    const logloss=issued.map(r=>-Math.log(outcomes[r.case_id]?r.probability!:1-r.probability!));
    const calibration=Array.from({length:10},(_,i)=>{
      const bin=issued.filter(r=>Math.min(9,Math.floor(r.probability!*10))===i);
      return {lower:i/10,upper:(i+1)/10,count:bin.length,mean_probability:mean(bin.map(r=>r.probability!)),observed_frequency:mean(bin.map(r=>outcomes[r.case_id]))};
    });
    groups.push({model,arm,registered:rows.length,attempted:rows.filter(r=>r.status!=='not_attempted').length,resolved:resolved.length,issued:issued.length,missing,
      issued_only_brier:mean(losses),matched_baseline_brier:baselines.length===issued.length?mean(baselines):null,
      issued_only_log_loss:logloss.some(x=>!Number.isFinite(x))?'infinite':mean(logloss),calibration,
      status_counts:Object.fromEntries(['issued','abstained','rejected','error','not_attempted'].map(s=>[s,rows.filter(r=>r.status===s).length])),
      all_case_brier_bounds:resolved.length?[sum/resolved.length,(sum+missing)/resolved.length]:null,
      elapsed_ms:rows.reduce((s,r)=>s+r.elapsed_ms,0),accounted_cost_usd:rows.reduce((s,r)=>s+(r.cost_usd??0),0)});
  }
  const paired=report.manifest.models.map(model=>{
    const differences:{cluster:string;delta:number}[]=[];
    for(const c of report.manifest.cases){
      if(!Object.hasOwn(outcomes,c.question.id))continue;
      const direct=report.rows.find(r=>r.case_id===c.question.id&&r.model===model&&r.arm==='direct');
      const harness=report.rows.find(r=>r.case_id===c.question.id&&r.model===model&&r.arm==='harness');
      if(direct?.status==='issued'&&harness?.status==='issued'&&direct.probability!==null&&harness.probability!==null)
        differences.push({cluster:c.cluster,delta:(harness.probability-outcomes[c.question.id])**2-(direct.probability-outcomes[c.question.id])**2});
    }
    return {model,pairs:differences.length,clusters:new Set(differences.map(r=>r.cluster)).size,harness_minus_direct_brier:mean(differences.map(r=>r.delta)),cluster_bootstrap_95:pairedInterval(differences)};
  });
  return {manifest_sha256:report.manifest.sha256,mode:report.manifest.mode,groups,paired,
    interpretation:'Lower Brier is better. Negative paired difference favors the harness. Exposed fixtures are engineering diagnostics; unmatched missing forecasts cannot establish an accuracy win. Intervals require at least five event clusters and remain uncertain for small samples.'};
}
