import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {packet} from '../src/lib/evidence.ts';
import {sha256} from '../src/lib/runtime.ts';
const cli=fileURLToPath(new URL('../src/lib/cli.ts',import.meta.url));
function run(args:string[],cwd:string,env:NodeJS.ProcessEnv=process.env){return new Promise<{code:number|null;stdout:string;stderr:string}>(resolve=>{
  const child=spawn(process.execPath,['--experimental-strip-types',cli,...args],{cwd,env});let stdout='',stderr='';
  child.stdout.on('data',b=>stdout+=b);child.stderr.on('data',b=>stderr+=b);child.on('exit',code=>resolve({code,stdout,stderr}));
});}
test('CLI runs the actual HTTP transport and core harness with an explicit local fixture provider',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vati-cli-'));let calls=0;
  const q={id:'wire-fixture',question:'Will the synthetic event occur?',resolution_date:'2035-01-01',dated_metric:'Synthetic event register at deadline.',conditions:[],event_type:'occurrence'};
  const server=http.createServer(async(req,res)=>{let body='';for await(const b of req)body+=b;
    calls++;assert.equal(req.headers.authorization,'Bearer test-key-only');const input=JSON.parse(body);
    const content=calls===1?`Synthetic estimate.\n\`\`\`vaticinus-forecast\n${JSON.stringify({kind:'binary',p_yes:.3,question:q.question,resolution_date:q.resolution_date,dated_metric:q.dated_metric})}\n\`\`\``:JSON.stringify({valid:true,issues:[],no_point:false});
    assert.equal(input.model,'local-fixture');res.writeHead(200,{'Content-Type':'text/event-stream'});
    res.end('data: '+JSON.stringify({choices:[{delta:{content},finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:100}})+'\n\ndata: [DONE]\n\n');
  });await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    const text='This is a synthetic software fixture.';const now=new Date().toISOString();
    fs.writeFileSync(path.join(root,'q.json'),JSON.stringify(q));
    fs.writeFileSync(path.join(root,'p.json'),JSON.stringify(packet([{id:'s',url:'https://example.org',title:'Synthetic',text,fetched_at:now,published_at:now,publication_basis:'user_attested',kind:'fixture',content_sha256:sha256(text),snapshot_sha256:sha256(text)}])));
    const address=server.address() as {port:number};
    const result=await run(['forecast','--question','q.json','--packet','p.json','--model','local-fixture','--max-cost','.1','--endpoint',`http://127.0.0.1:${address.port}/chat/completions`,'--input-rate','0','--output-rate','0'],root,{...process.env,VATI_MODEL_API_KEY:'test-key-only',OPENROUTER_API_KEY:''});
    assert.equal(result.code,0,result.stderr);assert.equal(JSON.parse(result.stdout).probability,.3);assert.equal(calls,2);
    assert.equal((await run(['verify'],root)).code,0);assert.ok(!fs.readFileSync(path.join(root,'forecasts/budget.json'),'utf8').includes('test-key-only'));
  }finally{await new Promise<void>(resolve=>server.close(()=>resolve()));fs.rmSync(root,{recursive:true});}
});
test('offline demo is complete and refuses to overwrite its history',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vati-demo-'));
  try{const first=await run(['demo'],root);assert.equal(first.code,0,first.stderr);assert.equal(JSON.parse(first.stdout).score.rows[0].outcome,1);assert.equal((await run(['demo'],root)).code,1);}
  finally{fs.rmSync(root,{recursive:true});}
});
