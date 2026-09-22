import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {collectDataPack} from '../dist/dataPacks.js';
import {sha256} from '../dist/runtime.js';
for(const kind of ['weather','macro','world']){
  const directory=fileURLToPath(new URL('./packs/'+kind+'/',import.meta.url));
  const pack=JSON.parse(fs.readFileSync(path.join(directory,'pack.json'),'utf8'));
  const summary=JSON.parse(pack.evidence.sources[0].text).summary;
  const now=new Date(kind==='world'?summary.history_end:pack.snapshots[0].captured_at);
  let index=0;
  const replay=await collectDataPack(pack.config,{now,resolve:async()=>[{address:'8.8.8.8',family:4}],fetch:async(url)=>{
    const snapshot=pack.snapshots[index++];assert.ok(snapshot,'Unexpected extra source request');
    assert.equal(String(url),snapshot.url,'Adapter source request changed since fixture capture');
    const body=fs.readFileSync(path.join(directory,snapshot.body_file));assert.equal(sha256(body),snapshot.sha256);
    return new Response(body,{headers:{'Content-Type':'application/json'}});
  }});
  assert.equal(index,pack.snapshots.length);assert.deepEqual(replay.question,pack.question);
  console.log(JSON.stringify({kind,offline:true,snapshots:index,baseline:replay.question.baseline.probability,sample_size:replay.metadata.baseline_sample_size}));
}
