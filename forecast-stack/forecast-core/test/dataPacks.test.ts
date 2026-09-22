import test from 'node:test';
import assert from 'node:assert/strict';
import {collectDataPack} from '../src/lib/dataPacks.ts';
const now=new Date('2026-09-22T12:00:00Z');
const fake=(body:unknown)=>({now,resolve:async()=>[{address:'8.8.8.8',family:4}] as any,fetch:(async()=>Response.json(body)) as typeof fetch});
test('weather baseline excludes sparse days and never calls observation time publication time',async()=>{
  const features=[];
  for(let d=12;d<22;d++)for(let h=0;h<24;h++)features.push({properties:{timestamp:`2026-09-${d}T${String(h).padStart(2,'0')}:00:00Z`,temperature:{unitCode:'wmoUnit:degC',value:d%2?25:15}}});
  const pack=await collectDataPack({kind:'weather',station:'KJFK',target_date:'2026-09-23',threshold_c:20},fake({features}));
  assert.equal(pack.question.baseline?.probability,.5);assert.equal(pack.metadata.baseline_sample_size,10);assert.equal(pack.evidence.sources[0].published_at,null);
  await assert.rejects(collectDataPack(pack.config,fake({features:features.slice(0,24)})),/Fewer than five/);
});
test('macro freezes current vintage and refuses an already reported target',async()=>{
  const data=Array.from({length:12},(_,i)=>({year:'2025',period:`M${String(i+1).padStart(2,'0')}`,value:i<6?'4.0':'5.0'}));
  const options=fake({status:'REQUEST_SUCCEEDED',Results:{series:[{seriesID:'LNS14000000',data}]}});
  const config={kind:'macro' as const,series:'LNS14000000',target_month:'2026-09',resolution_date:'2026-10-15',threshold:4.5,unit:'percent'};
  const pack=await collectDataPack(config,options);assert.equal(pack.question.baseline?.probability,.5);assert.match(pack.question.dated_metric,/not a claim about the first release/);
  await assert.rejects(collectDataPack({...config,target_month:'2025-12'},options),/already present/);
});
test('world baseline uses elapsed exposure and fails on incomplete catalogs',async()=>{
  const feature={id:'fixture-quake',properties:{time:Date.parse('2026-09-01'),updated:Date.parse('2026-09-02'),mag:6.1}};
  const config={kind:'world' as const,start:'2026-09-23T00:00:00Z',end:'2026-09-30T00:00:00Z',minimum_magnitude:6};
  const pack=await collectDataPack(config,fake({metadata:{count:1},features:[feature]}));assert.ok(Math.abs(pack.question.baseline!.probability-(1-Math.exp(-7/90)))<1e-10);
  assert.match(pack.metadata.limitations[0],/does not model geopolitical/);
  await assert.rejects(collectDataPack(config,fake({metadata:{count:2},features:[feature]})),/incomplete/);
});
