import test from 'node:test';
import assert from 'node:assert/strict';
import {collectEvidence,extractPage,publicSourceUrl,discover} from '../src/lib/evidence.ts';
const resolve=(async()=>[{address:'93.184.216.34',family:4}]) as any;
test('collection distinguishes publication metadata from fetch time and retains failures',async()=>{
  const result=await collectEvidence({sources:[{url:'https://example.org/news'},{url:'https://example.org/broken'}],resolve,
    fetch:(async(url:any)=>String(url).endsWith('broken')?new Response('',{status:503}):new Response('<title>Official result</title><meta property="article:published_time" content="2025-01-02T00:00:00Z"><script>ignore</script><p>A long enough official release with meaningful factual content and exact units.</p>',{headers:{'content-type':'text/html'}})) as any});
  assert.equal(result.sources.length,1);assert.equal(result.failures.length,1);
  assert.equal(result.sources[0].published_at,'2025-01-02T00:00:00.000Z');assert.ok(!result.sources[0].text.includes('ignore'));
});
test('an undated page is not relabelled with its retrieval date',()=>{
  assert.equal(extractPage('<title>Page</title><meta name="dateModified" content="2025-01-01"><p>Hello</p>').published_at,null);
});
test('private evidence endpoints and redirect destinations are rejected',async()=>{
  await assert.rejects(publicSourceUrl('http://127.0.0.1/secret'),/Private/);
  await assert.rejects(publicSourceUrl('http://169.254.169.254/latest/meta-data/'),/Private/);
  let calls=0;
  const result=await collectEvidence({sources:[{url:'https://example.org/redirect'}],resolve,
    fetch:(async()=>{calls++;return new Response('',{status:302,headers:{location:'http://127.0.0.1/secret'}});}) as any});
  assert.equal(calls,1);assert.equal(result.sources.length,0);assert.equal(result.failures.length,1);
});
test('live research cannot masquerade as a historical backtest',async()=>{
  await assert.rejects(collectEvidence({historical:true,sources:[]}),/Live retrieval/);
});
test('search discovers source URLs without elevating snippets to fetched evidence',async()=>{
  const urls=await discover(['release primary source'],{endpoint:'https://search.example.org/search',kind:'searxng'},
    (async()=>Response.json({results:[{url:'https://example.org/source',title:'Release',content:'unverified snippet'}]})) as any);
  assert.deepEqual(urls,[{url:'https://example.org/source',title:'Release'}]);
});
