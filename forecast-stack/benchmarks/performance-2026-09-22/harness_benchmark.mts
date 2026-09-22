/** Synthetic arithmetic, information isolation and scoring throughput; no model or network calls. */
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {packet} from '../../forecast-core/src/lib/evidence.ts';
import {sha256} from '../../forecast-core/src/lib/runtime.ts';
import type {BenchmarkReport} from '../../forecast-core/src/lib/benchmark.ts';
const args=process.argv.slice(2);
const arg=(name:string)=>{const i=args.indexOf(name);return i<0?undefined:args[i+1];};
const out=arg('--out'),casesFile=arg('--cases'),prefix=arg('--prefix')??'';
if(!out||!casesFile)throw Error('Usage: node --experimental-strip-types harness_benchmark.mts --cases cases.json --out result.json [--prefix .before-]');
if(fs.existsSync(out))throw Error('Refusing to overwrite a result');
const root=new URL('../../forecast-core/src/lib/',import.meta.url);
const engine=await import(new URL(prefix+'forecastEngine.ts',root).href);
const benchmark=await import(new URL(prefix+'benchmark.ts',root).href);
const workflow=await import(new URL(prefix+'workflow.ts',root).href);
const input=JSON.parse(fs.readFileSync(casesFile,'utf8'));
let numericPass=0,errors=0,maxError=0;
for(const row of input.cases){
  try{
    const actual=engine.computeForecast(row.spec).probability;
    if(!Number.isFinite(actual)){errors++;continue;}
    const error=Math.abs(actual-row.expected);maxError=Math.max(maxError,error);
    if(error<=input.tolerance)numericPass++;
  }catch{errors++;}
}
const text='Stipulated synthetic observation: event probability is 40%. Not real-world evidence.';
const evidence=packet([{id:'fixture',url:'https://example.org/fixture',title:'Stipulated observation',text,published_at:'2020-01-01T00:00:00Z',fetched_at:'2020-01-01T00:00:00Z',publication_basis:'user_attested',kind:'fixture',content_sha256:sha256(text),snapshot_sha256:sha256(text)}]);
const question={id:'synthetic',question:'Will the synthetic event occur?',resolution_date:'2030-02-01',dated_metric:'YES exactly when the stipulated event occurs.',event_type:'occurrence' as const,conditions:[],baseline:{probability:.5,description:'Evaluation comparator'}};
let isolationPass=0,issued=0;
for(const arm of ['direct','harness'] as const){
  const transcripts:string[][]=[];
  for(const probability of [.01,.99]){
    const requests:string[]=[];
    const run=await workflow.runForecast({...question,baseline:{probability,description:`PRIVATE_SCORING_${probability}`}},evidence,
      async(system:string,user:string,stage:string)=>{
        requests.push(system+'\n'+user);
        return stage==='forecast_review'?JSON.stringify({valid:true,issues:[],no_point:false}):
          `The stipulated probability is 40%.\n\n\`\`\`vaticinus-forecast\n${JSON.stringify({kind:'binary',p_yes:.4,question:question.question,resolution_date:question.resolution_date,dated_metric:question.dated_metric})}\n\`\`\``;
      },{model:'offline-synthetic',arm,mode:'fixture_replay',asOf:'2020-01-02T00:00:00Z'});
    if(run.status==='issued'&&run.probability===.4)issued++;
    transcripts.push(requests);
  }
  if(JSON.stringify(transcripts[0])===JSON.stringify(transcripts[1])&&transcripts[0].every(r=>!r.includes('PRIVATE_SCORING_')))isolationPass++;
}
const throughput=[];
for(const n of [1000,5000]){
  const cases=Array.from({length:n},(_,i)=>({question:{...question,id:`case-${i}`},evidence,as_of:'2020-01-02T00:00:00Z',cluster:`cluster-${i%100}`}));
  const manifest=benchmark.freezeBenchmark({name:'Synthetic scoring throughput',mode:'fixture_replay',models:['fixture'],arms:['direct','harness'],cases});
  const report:BenchmarkReport={schema_version:1,manifest,started_at:'2020-01-02T00:00:00Z',rows:[]};
  const outcomes:Record<string,0|1>={};
  for(let i=0;i<n;i++){
    const c=cases[i];outcomes[c.question.id]=i%3===0?1:0;
    for(const arm of manifest.arms)report.rows.push({case_id:c.question.id,cluster:c.cluster,model:'fixture',arm,status:i%37===0?'error':'issued',probability:i%37===0?null:arm==='direct'?.2+(i%6)*.1:.25+(i%4)*.1,elapsed_ms:10,cost_usd:0});
  }
  benchmark.scoreBenchmark(report,outcomes);
  const times:number[]=[];let scores;
  for(let repeat=0;repeat<5;repeat++){const start=performance.now();scores=benchmark.scoreBenchmark(report,outcomes);times.push(performance.now()-start);}
  times.sort((a,b)=>a-b);
  const {manifest_sha256:_,...numeric}=scores!;
  throughput.push({cases:n,rows:report.rows.length,clusters:100,median_ms:times[2],min_ms:times[0],max_ms:times[4],scores:numeric});
}
const result={synthetic:true,model_calls:0,cost_usd:0,node:process.version,architecture:process.arch,
  sources:Object.fromEntries(['forecastEngine','benchmark','workflow'].map(name=>[name,createHash('sha256').update(fs.readFileSync(new URL(prefix+name+'.ts',root))).digest('hex')])),
  numeric:{cases:input.cases.length,passed:numericPass,errors,max_absolute_error:maxError,tolerance:input.tolerance},
  baseline_isolation:{arms:2,passed:isolationPass,issued_fixture_runs:issued},throughput};
fs.writeFileSync(out,JSON.stringify(result,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({numeric:result.numeric,baseline_isolation:result.baseline_isolation,throughput:throughput.map(({cases,median_ms})=>({cases,median_ms}))}));
