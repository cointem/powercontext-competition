import express from 'express';
import { SeekdbClient, SeekdbAdminClient, Schema, VectorIndexConfig, FulltextIndexConfig } from 'seekdb';
import { mkdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';

const config = { host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 12882),
  user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '' };
const database = process.env.SEEKDB_DATABASE || 'stepfun_browser_kb';
const admin = new SeekdbAdminClient(config);
try { await admin.createDatabase(database); } catch (error) { if (!/exist/i.test(error.message)) throw error; }
await admin.close();
const client = new SeekdbClient({ ...config, database, queryTimeout: 60000 });
const pending = new Map();
const app = express();
app.use(express.json({ limit: '12mb' }));
// seekdb-js hybridSearch uses a connection-scoped @search_parm. Keep each
// request's SET / GET_SQL / execution together on this shared connection.
let databaseQueue = Promise.resolve();
app.use('/api', async (_req, res, next) => {
  const previous = databaseQueue;
  let release;
  databaseQueue = new Promise(resolve => { release = resolve; });
  await previous;
  res.once('finish', release);
  res.once('close', release);
  next();
});
function bad(message) { return Object.assign(new Error(message), { status: 400 }); }
function collectionName(name) {
  if (typeof name !== 'string' || !/^kb_[a-z0-9_]{1,55}$/.test(name)) throw bad('知识库名称只支持小写字母、数字和下划线');
  return name;
}
function vector(value) {
  if (!Array.isArray(value) || value.length !== 384 || !value.every(Number.isFinite)) throw bad('需要浏览器生成的 384 维向量');
  return value;
}
async function collection(name) {
  collectionName(name);
  if (!pending.has(name)) pending.set(name, client.getOrCreateCollection({ name, embeddingFunction: null, schema: new Schema({
    vectorIndex: new VectorIndexConfig({ hnsw: { dimension: 384, distance: 'cosine' }, embeddingFunction: null }),
    fulltextIndex: new FulltextIndexConfig('beng'),
  }) }).catch(error => { pending.delete(name); throw error; }));
  return pending.get(name);
}
app.get('/api/health', async (_req, res) => {
  await client.listCollections({ withEmbeddingFunction: false });
  res.json({ database, sdk: 'seekdb@1.4.0', inference: 'browser only' });
});
app.get('/api/collections', async (_req, res) => {
  const all = await client.listCollections({ withEmbeddingFunction: false });
  res.json(await Promise.all(all.filter(c => c.name.startsWith('kb_')).map(async c => ({ name: c.name, count: await c.count() }))));
});
app.post('/api/import', async (req, res) => {
  const { name, documents, refresh = true } = req.body;
  if (!Array.isArray(documents) || !documents.length || documents.length > 64) throw bad('每批导入 1–64 个片段');
  for (const doc of documents) {
    if (typeof doc.id !== 'string' || doc.id.length > 128 || !doc.id || typeof doc.text !== 'string' || !doc.text.trim() || doc.text.length > 8000) throw bad('无效文档');
    vector(doc.embedding);
  }
  const col = await collection(name);
  const t = performance.now();
  await col.upsert({ ids: documents.map(d => d.id), documents: documents.map(d => d.text),
    embeddings: documents.map(d => d.embedding), metadatas: documents.map(d => ({ title: String(d.title || d.id).slice(0,300), source: String(d.source || 'import').slice(0,300) })) });
  const writeMs = performance.now() - t;
  const r = performance.now();
  if (refresh) await col.refresh_index();
  res.json({ count: await col.count(), writeMs, refreshMs: performance.now() - r, refreshed: refresh });
});
app.post('/api/search', async (req, res) => {
  const { name, embedding, text, mode = 'hybrid', k = 5 } = req.body;
  vector(embedding);
  if (typeof text !== 'string' || !text.trim() || text.length > 2000 || !['hybrid','vector'].includes(mode) || !Number.isInteger(k) || k < 1 || k > 20) throw bad('无效查询');
  const col = await collection(name);
  const t = performance.now();
  const result = mode === 'hybrid'
    ? await col.hybridSearch({ query: { whereDocument: { $contains: text }, nResults: 20 },
      knn: { queryEmbeddings: [embedding], nResults: 20 }, rank: { rrf: { rankConstant: 60, rankWindowSize: 20 } },
      nResults: k, include: ['documents','metadatas','distances'] })
    : await col.query({ queryEmbeddings: [embedding], nResults: k, include: ['documents','metadatas','distances'] });
  res.json({ result, dbMs: performance.now() - t, engine: mode === 'hybrid' ? 'seekdb DBMS_HYBRID_SEARCH + RRF' : 'seekdb HNSW cosine' });
});
// Only benchmark output is saved here; the server never computes embeddings or ranks results.
app.post('/api/evidence', async (req, res) => {
  if (req.body?.kind !== 'browser-benchmark' || !Array.isArray(req.body.runs)) throw bad('无效评测记录');
  const filename = `${new Date().toISOString().replace(/[:.]/g, '-')}-${randomUUID().slice(0,8)}.json`;
  await mkdir('evidence/runtime', { recursive: true });
  await writeFile(`evidence/runtime/${filename}`, JSON.stringify(req.body, null, 2));
  res.json({ file: `evidence/runtime/${filename}` });
});
app.use(express.static('dist'));
app.use((error, _req, res, _next) => { console.error(error.message); res.status(error.status || 500).json({ error: error.message }); });
app.listen(Number(process.env.PORT || 15174), '127.0.0.1', () => console.log('API http://127.0.0.1:' + (process.env.PORT || 15174)));
process.on('SIGTERM', async () => { await client.close(); process.exit(0); });
