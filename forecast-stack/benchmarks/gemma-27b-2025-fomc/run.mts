import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {MeteredRuntime,atomicJson,sha256} from '../../forecast-core/src/lib/runtime.ts';
import {runBenchmark} from '../../forecast-core/src/lib/benchmark.ts';
const root=new URL('./',import.meta.url);
const protocol=JSON.parse(fs.readFileSync(new URL('protocol.json',root),'utf8'));
const bytes=fs.readFileSync(new URL('manifest.json',root));
if(sha256(bytes)!==protocol.manifest_sha256||sha256(fs.readFileSync(fileURLToPath(import.meta.url)))!==protocol.runner_sha256)throw new Error('Frozen experiment changed');
const manifest=JSON.parse(bytes.toString());
if(!process.argv.includes('--run')){console.log(JSON.stringify({models:manifest.models,rows:manifest.cases.length*manifest.arms.length,provider:protocol.provider,paid:false}));process.exit(0);}
const ledger=process.env.STUDY_LEDGER,out=process.env.STUDY_OUT,key=process.env.OPENROUTER_API_KEY;
if(!ledger||!out||!key)throw new Error('Set STUDY_LEDGER, STUDY_OUT and OPENROUTER_API_KEY');
if(sha256(fs.readFileSync(ledger))!==protocol.starting_runtime_ledger_sha256)throw new Error('Ledger changed after registration');
fs.writeFileSync(out,'{}\n',{flag:'wx',mode:0o600});
const request=globalThis.fetch;
globalThis.fetch=async()=>{throw new Error('Unmetered network blocked');};
try{
 const report=await runBenchmark(manifest,async model=>{
  const runtime=new MeteredRuntime({model,apiKey:key,ledger,maxCost:protocol.gemma_cumulative_ceiling_usd,rates:protocol.rates,reasoning:false,
   fetch:async(url,init)=>{
    if(String(url)!=='https://openrouter.ai/api/v1/chat/completions')throw new Error('Unexpected inference endpoint');
    const body=JSON.parse(String(init?.body));
    body.provider.only=[protocol.provider];body.temperature=0;
    return request(url,{...init,body:JSON.stringify(body)});
   }});
  return {complete:runtime.complete,spent:()=>runtime.spent,terminal:()=>runtime.terminal,close:()=>runtime.close()};
 },report=>atomicJson(out,report));
 console.log(JSON.stringify({rows:report.rows.length,issued:report.rows.filter(r=>r.status==='issued').length}));
}finally{globalThis.fetch=request;}
