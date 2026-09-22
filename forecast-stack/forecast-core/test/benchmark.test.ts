import test from 'node:test';
import assert from 'node:assert/strict';
import {freezeBenchmark,runBenchmark,scoreBenchmark,verifyBenchmark} from '../src/lib/benchmark.ts';
import {packet} from '../src/lib/evidence.ts';
import {sha256} from '../src/lib/runtime.ts';
const question={id:'fixture',question:'Will the fixture event happen?',resolution_date:'2030-02-01',dated_metric:'Synthetic register: YES if event is recorded.',event_type:'occurrence' as const,conditions:[],baseline:{probability:.5,description:'Synthetic comparator'}};
const text='Synthetic fixture, not evidence of a real event.';
const evidence=packet([{id:'s',url:'https://example.org',title:'Fixture',text,published_at:'2020-01-01T00:00:00Z',fetched_at:'2020-01-01T00:00:00Z',publication_basis:'user_attested',kind:'fixture',content_sha256:sha256(text),snapshot_sha256:sha256(text)}]);
const manifest=()=>freezeBenchmark({name:'Synthetic software test',mode:'fixture_replay',models:['fixture/model'],arms:['direct','harness'],cases:[{question,evidence,as_of:'2020-01-02T00:00:00Z',cluster:'one-event'}]});
test('same packet and model run both arms; paired scores include all registered rows',async()=>{
  const m=manifest();let calls=0;
  const report=await runBenchmark(m,async()=>({spent:()=>calls*.001,terminal:()=>false,close:()=>{},complete:async(_s,_u,stage)=>{
    calls++;return stage==='forecast_review'?JSON.stringify({valid:true,issues:[],no_point:false}):`A synthetic estimate.\n\`\`\`vaticinus-forecast\n${JSON.stringify({kind:'binary',p_yes:.7,question:question.question,resolution_date:question.resolution_date,dated_metric:question.dated_metric})}\n\`\`\``;
  }}),()=>{});
  assert.equal(report.rows.filter(r=>r.status==='issued').length,2);
  const score=scoreBenchmark(report,{fixture:1});assert.equal(score.paired[0].harness_minus_direct_brier,0);assert.equal(score.groups[0].missing,0);assert.equal(score.groups[0].calibration[7].count,1);
  const changed=structuredClone(report);changed.rows[0].model='unregistered';assert.throws(()=>scoreBenchmark(changed,{fixture:1}),/Unregistered/);
  const omitted=structuredClone(report);omitted.rows.pop();assert.throws(()=>scoreBenchmark(omitted,{fixture:1}),/omits/);
  m.name='changed';assert.throws(()=>verifyBenchmark(m),/changed/);
});
test('uncertain-charge stop preserves all unattempted cells and missingness bounds',async()=>{
  let stopped=false;
  const report=await runBenchmark(manifest(),async()=>({spent:()=>stopped?.1:0,terminal:()=>stopped,close:()=>{},complete:async()=>{stopped=true;throw Error('timeout after HTTP 200');}}),()=>{});
  assert.deepEqual(report.rows.map(r=>r.status),['error','not_attempted']);
  const score=scoreBenchmark(report,{fixture:1});assert.deepEqual(score.groups[0].all_case_brier_bounds,[0,1]);assert.equal(score.paired[0].pairs,0);
  report.rows[0].probability=.5;assert.throws(()=>scoreBenchmark(report,{fixture:1}),/probability/);
});
