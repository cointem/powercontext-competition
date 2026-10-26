import './style.css';
import { sha256 } from '@noble/hashes/sha2.js';
import { corpus, queries } from './corpus.js';
const $ = id => document.getElementById(id);
let worker, sequence = 0, currentInfo, report, busy = false, selectedName;
const requests = new Map();
function resetWorker() {
  worker?.terminate();
  worker = new Worker(new URL('./embedding.worker.js', import.meta.url), { type: 'module' });
  worker.onmessage = ({data}) => {
    if (data.progress) {
      const p = data.progress;
      if (p.status === 'embedding') { $('progress').value = p.done / p.total * 100; status(`浏览器向量化：${p.done} / ${p.total}`); }
      else if (p.status === 'progress') { $('progress').value = p.progress || 0; status(`加载 ${p.file} · ${Math.round(p.progress || 0)}%`); }
      return;
    }
    const pending = requests.get(data.id); requests.delete(data.id);
    if (!pending) return;
    if (data.error) pending.reject(new Error(data.error));
    else { currentInfo = data.info; showBackend(); pending.resolve(data); }
  };
  worker.onerror = event => { for (const p of requests.values()) p.reject(new Error(event.message)); requests.clear(); };
}
resetWorker();
function status(text) { $('status').textContent = text; }
function showBackend() {
  $('backend').textContent = `${currentInfo.backend.toUpperCase()} · ${currentInfo.dtype.toUpperCase()}${currentInfo.reason ? ' · ' + currentInfo.reason : ''}`;
}
const options = () => ({backend:$('device').value,dtype:$('dtype').value});
function embed(texts, batchSize = null, config = options()) {
  return new Promise((resolve,reject) => { const id = ++sequence; requests.set(id,{resolve,reject}); worker.postMessage({id,texts,batchSize,options:config}); });
}
async function api(path, body) {
  const r = await fetch('/api/' + path, body ? {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
  const value = await r.json(); if (!r.ok) throw new Error(value.error || r.statusText); return value;
}
function activeName(info = currentInfo) {
  const base = $('collection').value.trim();
  if (!/^[a-z0-9_]{1,32}$/.test(base)) throw new Error('知识库名称请使用 1–32 个小写字母、数字或下划线');
  localStorage.setItem('stepfun_collection',base);
  localStorage.setItem('stepfun_selection',JSON.stringify({base,device:$('device').value,dtype:$('dtype').value,selectedName}));
  return selectedName || `kb_${base}_${info.dtype}`;
}
async function listCollections() {
  const cols = await api('collections');
  $('existing').replaceChildren(new Option('选择已有知识库',''), ...cols.filter(c=>!c.name.startsWith('kb_bench_')).map(c=>new Option(`${c.name} · ${c.count} 片段`,c.name)));
  if (currentInfo) $('count').textContent = `当前知识库 ${activeName()}：${cols.find(c=>c.name===activeName())?.count || 0} 个片段`;
}
async function run(task) {
  if (busy) return; busy=true;
  document.querySelectorAll('button,input,select,textarea').forEach(x=>x.disabled=true);
  try { await task(); } catch(error) { status('操作失败：' + error.message); console.error(error); }
  finally { busy=false; document.querySelectorAll('button,input,select,textarea').forEach(x=>x.disabled=false); $('export').disabled=!report; }
}
async function hash(text) { return [...sha256(new TextEncoder().encode(text))].map(x=>x.toString(16).padStart(2,'0')).join(''); }
function chunks(text) {
  const parts = [];
  for (const para of text.split(/\n\s*\n/).filter(x=>x.trim())) {
    const words = para.trim().split(/\s+/);
    for(let i=0;i<words.length;i+=160) {
      const piece=words.slice(i,i+160).join(' ');
      for(let j=0;j<piece.length;j+=1000) parts.push(piece.slice(j,j+1000));
    }
  }
  return parts;
}
async function importDocuments(docs) {
  const start=performance.now();
  let writeMs=0,refreshMs=0,embedMs=0,count=0;
  for(let i=0;i<docs.length;i+=32) {
    const batch=docs.slice(i,i+32), result=await embed(batch.map(d=>d.text)); embedMs+=result.embedMs;
    if(selectedName && !selectedName.endsWith('_'+result.info.dtype)) throw new Error('当前集合的存储精度与推理精度不同；检索可继续，导入请新建一个知识库名称');
    const saved=await api('import',{name:activeName(result.info),documents:batch.map((d,j)=>({...d,embedding:result.vectors[j]})),refresh:i+32>=docs.length});
    writeMs+=saved.writeMs; refreshMs+=saved.refreshMs;count=saved.count;
  }
  await listCollections();
  $('progress').value=100;
  status(`导入完成 · ${count} 个片段 · 向量化 ${embedMs.toFixed(0)} ms · 写入 ${writeMs.toFixed(0)} ms · 索引刷新 ${refreshMs.toFixed(0)} ms · 总计 ${(performance.now()-start).toFixed(0)} ms`);
}
function renderResults(data) {
  $('results').replaceChildren();
  data.result.ids[0].forEach((id,i)=>{
    const article=document.createElement('article'),title=document.createElement('h3'),p=document.createElement('p'),small=document.createElement('small');
    title.textContent=`${i+1}. ${data.result.metadatas?.[0]?.[i]?.title || id}`;
    p.textContent=data.result.documents?.[0]?.[i] || '';
    small.textContent=`${id} · ${data.result.metadatas?.[0]?.[i]?.source || ''}`;
    article.append(title,p,small);$('results').append(article);
  });
  if(!data.result.ids[0].length) $('results').textContent='当前知识库没有匹配结果，请先导入文档。';
}
$('load').onclick=()=>run(async()=>{ const loaded=await embed(); await listCollections();status(`模型准备完成 · ${loaded.info.loadMs.toFixed(0)} ms · ${loaded.info.reused?'复用已加载模型':'首次初始化'}`); });
$('sample').onclick=()=>run(()=>importDocuments(corpus));
$('import').onclick=()=>run(async()=>{
  const sources=[];
  if($('document').value.trim()) sources.push({title:$('title').value,text:$('document').value,source:'粘贴'});
  for(const file of $('files').files) { if(file.size>2_000_000) throw new Error('单个文本文件请小于 2 MB'); sources.push({title:file.name,text:await file.text(),source:file.name}); }
  const docs=[];
  for(const source of sources) for(const text of chunks(source.text)) docs.push({id:await hash(source.source+'\n'+source.title+'\n'+text),title:source.title,text,source:source.source});
  if(!docs.length) throw new Error('请选择文件或输入正文');
  await importDocuments(docs);
});
$('search').onclick=()=>run(async()=>{
  const text=$('query').value.trim(); if(!text) throw new Error('请输入查询');
  const start=performance.now(), encoded=await embed([text]);
  const data=await api('search',{name:activeName(encoded.info),text,embedding:encoded.vectors[0],mode:$('mode').value,k:5});
  renderResults(data);
  await new Promise(requestAnimationFrame);
  $('timing').textContent=`向量化 ${encoded.embedMs.toFixed(1)} ms · seekdb ${data.dbMs.toFixed(1)} ms · 提交至显示 ${(performance.now()-start).toFixed(1)} ms`;
  status(data.engine); await listCollections();
});
$('existing').onchange=()=>run(async()=>{
  if(!$('existing').value)return;
  const matched=$('existing').value.match(/^kb_(.+)_(fp32|fp16|q8)$/);
  if(!matched)return;
  selectedName=$('existing').value;
  $('collection').value=matched[1];$('dtype').value=matched[2];
  await embed();await listCollections();$('results').replaceChildren();status('已切换知识库');
});
$('collection').onchange=()=>run(async()=>{selectedName=undefined;await embed();await listCollections();$('results').replaceChildren();});
$('dtype').onchange=$('device').onchange=()=>run(async()=>{await embed();await listCollections();$('results').replaceChildren();status('推理配置已更新');});
if(localStorage.getItem('stepfun_collection'))$('collection').value=localStorage.getItem('stepfun_collection');
try {
  const saved=JSON.parse(localStorage.getItem('stepfun_selection'));
  if(saved){$('collection').value=saved.base;$('device').value=saved.device;$('dtype').value=saved.dtype;selectedName=saved.selectedName;}
}catch { /* Ignore an obsolete local UI preference. */ }
listCollections().catch(error=>status('数据库连接失败：'+error.message));

const median = values => [...values].sort((a,b)=>a-b)[Math.floor(values.length/2)];
function quality(ids,relevant,k=5) {
  const hits=ids.slice(0,k).filter(x=>relevant.includes(x)).length;
  const dcg=ids.slice(0,k).reduce((s,id,i)=>s+(relevant.includes(id)?1/Math.log2(i+2):0),0);
  const ideal=relevant.slice(0,k).reduce((s,_,i)=>s+1/Math.log2(i+2),0);
  return {recall:hits/relevant.length,ndcg:dcg/ideal};
}
async function benchQuery(name, query, config) {
  const t=performance.now(), emb=await embed([query.text],1,config);
  const result=await api('search',{name,text:query.text,embedding:emb.vectors[0],k:5});
  // Include real DOM creation and a rendering opportunity in end-to-end timing.
  renderResults(result); await new Promise(requestAnimationFrame);
  return {query:query.text,ids:result.result.ids[0],embeddingMs:emb.embedMs,dbMs:result.dbMs,e2eMs:performance.now()-t,...quality(result.result.ids[0],query.relevant)};
}
$('benchmark').onclick=()=>run(async()=>{
  const requested=$('device').value;
  const probe=await embed(undefined,8,{backend:requested,dtype:'fp16'});
  const backend=probe.info.backend,quantized=backend==='webgpu'?'fp16':'q8';
  report={kind:'browser-benchmark',at:new Date().toISOString(),userAgent:navigator.userAgent,hardwareConcurrency:navigator.hardwareConcurrency,
    gpuAvailable:!!navigator.gpu,backendProbe:probe.info,manifest:await (await fetch('/model-manifest.json')).json(),
    dataset:{documents:corpus.length,queries:queries.length,version:'evaluation-v1',k:5,repeats:3},runs:[]};
  const runId=Date.now().toString(36);
  for(const dtype of ['fp32',quantized]) {
    const config={backend,dtype};resetWorker();
    const loaded=await embed(undefined,8,config);
    const one={config:loaded.info,loadMs:loaded.info.loadMs,batch:[],queries:[],writes:[]};report.runs.push(one);
    // Both batch sizes are warmed up before paired measurements.
    await embed(corpus.slice(0,8).map(d=>d.text),8,config);
    let vectors;
    for(let repeat=0;repeat<3;repeat++) {
      for(const batchSize of repeat%2?[8,1]:[1,8]) {
        const result=await embed(corpus.map(d=>d.text),batchSize,config); vectors=result.vectors;
        one.batch.push({repeat,batchSize,ms:result.embedMs,documentsPerSecond:corpus.length/result.embedMs*1000});
      }
    }
    const name=`kb_bench_${runId}_${dtype}`;
    const docs=corpus.map((d,i)=>({...d,embedding:vectors[i]}));
    // Compare the same 4 upserts with per-batch vs final-only refresh.
    for(let repeat=0;repeat<3;repeat++) {
      for(const strategy of repeat%2?['final','each']:['each','final']) {
        let writeMs=0,refreshMs=0;const t=performance.now();
        for(let i=0;i<docs.length;i+=8) {
          const r=await api('import',{name,documents:docs.slice(i,i+8),refresh:strategy==='each'||i+8>=docs.length});
          writeMs+=r.writeMs;refreshMs+=r.refreshMs;
        }
        one.writes.push({repeat,strategy,writeMs,refreshMs,e2eMs:performance.now()-t});
      }
    }
    one.firstQuery=await benchQuery(name,queries[0],config);
    for(let repeat=0;repeat<3;repeat++)for(const query of queries)one.queries.push({repeat,...await benchQuery(name,query,config)});
    const incremental={id:'incremental',title:'Incremental observatory',text:'The Zephyr observatory opens at midnight for violet comet viewing.',source:'incremental-test'};
    const inc=await embed([incremental.text],1,config);
    await api('import',{name,documents:[{...incremental,embedding:inc.vectors[0]}]});
    const found=await api('search',{name,text:'Zephyr observatory violet comet',embedding:inc.vectors[0],k:5});
    const other=await api('search',{name:name+'_other',text:incremental.text,embedding:inc.vectors[0],k:5});
    const again=await api('search',{name,text:incremental.text,embedding:inc.vectors[0],k:5});
    one.stateChecks={incrementalFound:found.result.ids[0].includes('incremental'),otherCollectionEmpty:other.result.ids[0].length===0,switchBackFound:again.result.ids[0].includes('incremental')};
    if(Object.values(one.stateChecks).some(x=>!x))throw new Error('增量 / 集合状态验证失败');
    one.summary={recall5:one.queries.reduce((s,x)=>s+x.recall,0)/one.queries.length,ndcg5:one.queries.reduce((s,x)=>s+x.ndcg,0)/one.queries.length,
      queryEmbeddingMedianMs:median(one.queries.map(x=>x.embeddingMs)),dbMedianMs:median(one.queries.map(x=>x.dbMs)),e2eMedianMs:median(one.queries.map(x=>x.e2eMs)),
      batch1MedianMs:median(one.batch.filter(x=>x.batchSize===1).map(x=>x.ms)),batch8MedianMs:median(one.batch.filter(x=>x.batchSize===8).map(x=>x.ms)),
      refreshEachMedianMs:median(one.writes.filter(x=>x.strategy==='each').map(x=>x.e2eMs)),refreshFinalMedianMs:median(one.writes.filter(x=>x.strategy==='final').map(x=>x.e2eMs))};
    $('report').textContent=JSON.stringify(report.runs.map(r=>({config:r.config,summary:r.summary,stateChecks:r.stateChecks})),null,2);
  }
  report.saved=await api('evidence',report);
  $('results').replaceChildren(); $('timing').textContent='';
  $('report').textContent=JSON.stringify({saved:report.saved,results:report.runs.map(r=>({config:r.config,summary:r.summary,stateChecks:r.stateChecks}))},null,2);
  await listCollections(); status(`评测完成 · ${backend.toUpperCase()} · 原始数据已保存 ${report.saved.file}`);
});
$('export').onclick=()=>{
  const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='stepfun-browser-benchmark.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
