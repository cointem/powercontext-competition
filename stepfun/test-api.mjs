// Real seekdb tests with synthetic vectors. Browser model tests live in the UI benchmark.
import test from 'node:test';
import assert from 'node:assert/strict';
const base = process.env.TEST_URL || 'http://127.0.0.1:15174/api/';
const prefix = 'kb_bench_api_' + Date.now().toString(36);
const basis = i => Array.from({length:384},(_,j)=>i===j?1:0);
async function post(path,body) { const r=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); return {status:r.status,data:await r.json()}; }
test('real seekdb: upsert, incremental refresh, parallel hybrid isolation, input validation',async()=>{
  for(const [suffix,index,text] of [['a',0,'Cerulean comet telescope'],['b',1,'Golden pastry oven']]){
    const name=prefix+'_'+suffix;
    const doc={id:'first',text,embedding:basis(index)};
    assert.equal((await post('import',{name,documents:[doc]})).status,200);
    assert.equal((await post('import',{name,documents:[doc]})).data.count,1);
    assert.equal((await post('import',{name,documents:[{...doc,id:'second'}]})).data.count,2);
  }
  const searches=await Promise.all(Array.from({length:12},(_,i)=>post('search',{name:prefix+(i%2?'_b':'_a'),text:i%2?'pastry':'comet',embedding:basis(i%2),k:5})));
  searches.forEach((r,i)=>{assert.equal(r.status,200);assert.equal(r.data.result.ids[0].length,2);assert.ok(r.data.result.documents[0].every(x=>x.includes(i%2?'pastry':'comet')));});
  assert.equal((await post('search',{name:prefix+'_a',text:'test',embedding:[1]})).status,400);
  assert.equal((await post('import',{name:'bad-name',documents:[{id:'x',text:'test',embedding:basis(0)}]})).status,400);
});
