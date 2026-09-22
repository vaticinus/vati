import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {runForecast,ForecastHistory,questionHash,type ForecastQuestion,type Resolution} from '../src/lib/workflow.ts';
import {packet,type Evidence} from '../src/lib/evidence.ts';
import {sha256} from '../src/lib/runtime.ts';
import type {ForecastCompletion} from '../src/lib/coherence.ts';
const q:ForecastQuestion={id:'release',question:'Will the initial release be at least 10 units?',resolution_date:'2030-02-01',
  dated_metric:'First publication only, at least 10 units. Later revisions are excluded.',event_type:'quantity_threshold',conditions:['initial release only'],
  numeric_clause:{threshold:10,threshold_dir:'>=',ci_unit:'units'},baseline:{probability:.5,description:'Declared uninformed comparator'}};
const text='The last initial release measured 9 units. This is the only observed input; no future observation is supplied.';
const source:Evidence={id:'s',url:'https://example.org/release',title:'Initial release',text,published_at:'2030-01-01T00:00:00Z',fetched_at:'2030-01-02T00:00:00Z',
  publication_basis:'user_attested',content_sha256:sha256(text),snapshot_sha256:sha256(text),kind:'fixture'};
const evidence=packet([source]);
const completion:ForecastCompletion=async(_s,_u,stage)=>stage==='forecast_review'?JSON.stringify({valid:true,issues:[],no_point:false}):
  `I estimate 40% using the supplied initial release and an explicitly judgmental prior. The event is uncertain.\n\n\`\`\`vaticinus-forecast\n${JSON.stringify({kind:'binary',p_yes:.4,question:q.question,resolution_date:q.resolution_date,dated_metric:q.dated_metric})}\n\`\`\``;
const options={model:'fixture/model',mode:'fixture_replay' as const,now:new Date('2030-01-03T00:00:00Z')};
test('public harness, immutable revision, resolution and score form a complete lifecycle',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vati-life-')),history=new ForecastHistory(root);
  try{
    const first=await runForecast(q,evidence,completion,options);assert.equal(first.status,'issued');assert.equal(first.probability,.4);history.add(first);
    const second=await runForecast(q,evidence,completion,{...options,now:new Date('2030-01-04T00:00:00Z'),supersedes:first.id});history.add(second);
    assert.equal(history.read().length,2);assert.equal((history.read()[0].payload as any).probability,.4);
    const resolution:Resolution={question_id:q.id,question_hash:questionHash(q),outcome:1,observed_at:'2030-02-01T12:00:00Z',recorded_at:'2030-02-01T12:01:00Z',
      source:{url:'https://example.org/release',text:'Initial release: 10 units.',sha256:sha256('Initial release: 10 units.')},value:10,unit:'units',note:'Initial publication retained.'};
    history.resolve(resolution);assert.equal(history.score('first').rows[0].run_id,first.id);assert.equal(history.score('latest').rows[0].run_id,second.id);
    assert.equal(history.score().rows[0].brier,.36);assert.equal(history.score().rows[0].baseline_brier,.25);
    assert.throws(()=>history.resolve(resolution),/already recorded/);assert.throws(()=>history.add({...second,id:'third'}),/Resolved/);
    const raw=fs.readFileSync(history.filename,'utf8');fs.writeFileSync(history.filename,raw.replace('"probability":0.4','"probability":0.9'));
    assert.throws(()=>history.read(),/integrity/);
  }finally{fs.rmSync(root,{recursive:true});}
});
test('transport failures remain error rows, never fabricated 50% forecasts',async()=>{
  const run=await runForecast(q,evidence,async()=>{throw Error('timeout');},options);
  assert.equal(run.status,'error');assert.equal(run.probability,null);assert.match(run.issues[0],/timeout/);
});
test('direct comparator cannot change the settling rule silently',async()=>{
  const run=await runForecast(q,evidence,async(s,u,stage)=>(await completion(s,u,stage,100))!.replace('First publication only','Latest revised publication'),{...options,arm:'direct'});
  assert.equal(run.status,'rejected');assert.equal(run.probability,null);
});
test('historical replay requires older checkpoint provenance and archived evidence',async()=>{
  await assert.rejects(runForecast(q,evidence,completion,{model:'model',mode:'historical_replay',now:new Date('2031-01-01'),asOf:'2030-01-03T00:00:00Z'}),/checkpoint/);
  const checkpoint={revision:'sha:released-weights',released_at:'2029-01-01',provenance:'https://example.org/model-card'};
  const run=await runForecast(q,evidence,completion,{model:'model',mode:'historical_replay',now:new Date('2031-01-01'),asOf:'2030-01-03T00:00:00Z',checkpoint});
  assert.equal(run.mode,'historical_replay');assert.equal(run.created_at,'2031-01-01T00:00:00.000Z');
  await assert.rejects(runForecast(q,packet([{...source,fetched_at:'2031-01-01T00:00:00Z'}]),completion,{model:'model',mode:'historical_replay',now:new Date('2031-01-01'),asOf:'2030-01-03T00:00:00Z',checkpoint}),/cutoff/);
});
test('numeric resolution enforces the exact comparator',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'vati-resolution-')),history=new ForecastHistory(root);
  try{
    history.add(await runForecast(q,evidence,completion,options));
    assert.throws(()=>history.resolve({question_id:q.id,question_hash:questionHash(q),outcome:0,observed_at:'2030-02-01T12:00:00Z',recorded_at:'2030-02-01T13:00:00Z',
      source:{url:'https://example.org/source',text:'10',sha256:sha256('10')},value:10,unit:'units',note:'first release'}),/comparator/);
  }finally{fs.rmSync(root,{recursive:true});}
});
