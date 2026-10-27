// Local harness for the deployed business routes. It runs the real `application()`
// from functions/routes.mjs with in-memory dependencies — no cloud credentials,
// no database token, no identity headers from the browser.
//
//   node dev/function-local.mjs [port]
//
// Serves web/ and proxies /functions/v1/app/* to the actual routes. Set the
// local identity with the `x-local-user` header (dev/test console only).
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, resolve } from 'node:path';
import { application } from '../functions/routes.mjs';

const ROOT = resolve(import.meta.dirname, '..');
const WEB = join(ROOT, 'web');

export class FakeError extends Error {
  constructor(code, status) { super(code); this.code = code; this.status = status; }
}

const UNIQUE = { entries: ['cite'] };

export function makeDb(tables) {
  const clone = (row) => structuredClone(row);
  const dup = (name, candidate, ignoreId) =>
    (UNIQUE[name] || []).some((column) =>
      tables[name].some((row) => row[column] === candidate[column] && row.id !== ignoreId));

  return {
    from(name) {
      if (!(name in tables)) throw new FakeError('invalid_table', 400);
      const state = { action: 'select', filters: [], order: null, limit: null, columns: null, values: null };
      const query = {
        select(columns) { state.columns = columns; return query; },
        eq(key, value) { state.filters.push([key, value]); return query; },
        order(key, options) { state.order = { key, descending: options?.ascending === false }; return query; },
        limit(value) { state.limit = value; return query; },
        insert(rows) { state.action = 'insert'; state.values = Array.isArray(rows) ? rows : [rows]; return query; },
        insertIfAbsent(row, options) { state.action = 'insertIfAbsent'; state.values = [row]; state.conflict = options?.onConflict; return query; },
        update(values) { state.action = 'update'; state.values = values; return query; },
        delete() { state.action = 'delete'; return query; },
        then(resolveFn, rejectFn) {
          return Promise.resolve().then(() => resolveFn(run(name, state))).then(undefined, rejectFn);
        },
      };
      return query;
    },
  };

  function run(name, state) {
    const table = tables[name];
    const selected = () => table.filter((row) => state.filters.every(([k, v]) => row[k] === v));
    if (state.action === 'select') {
      let found = selected().map(clone);
      if (state.order) {
        const { key, descending } = state.order;
        found.sort((a, b) => (a[key] < b[key] ? -1 : a[key] > b[key] ? 1 : 0));
        if (descending) found.reverse();
      }
      if (state.limit) found = found.slice(0, state.limit);
      return { data: found, error: null };
    }
    if (state.action === 'insert' || state.action === 'insertIfAbsent') {
      const fresh = [];
      for (const row of state.values) {
        if (dup(name, row)) {
          if (state.action === 'insertIfAbsent') continue;
          return { data: null, error: { code: 'unique_violation', status: 409 } };
        }
        const stored = { ...row };
        table.push(stored);
        fresh.push(clone(stored));
      }
      return { data: fresh, error: null };
    }
    if (state.action === 'update') {
      const updated = [];
      for (const row of selected()) {
        Object.assign(row, state.values);
        updated.push(clone(row));
      }
      return { data: updated, error: null };
    }
    const doomed = selected();
    for (const row of doomed) table.splice(table.indexOf(row), 1);
    return { data: doomed.map(clone), error: null };
  }
}

export function fixtures() {
  const day = '2026-10-27'; // a Tuesday inside 2026-W44, so week and day agree
  const base = { week: '2026-W44', day, origin: 'local', owner_id: 'local', created_at: `${day}T12:00:00Z` };
  const rows = [
    ['source/aaa111', 'commit', 'recap: 混合检索改成整数日键', 'pyseekdb 拒绝 JSON metadata 字符串范围比较，改用 created_key。', 'recap'],
    ['source/bbb222', 'note', 'Recap 开发记录 · 2026-W44', '本周从读题到把 Recap 跑通。', ''],
    ['atomic-memory/m1@rev1', 'decision', '每条结论必须挂可回溯引用', '没有出处的句子直接判定为 bug。', ''],
    ['atomic-memory/m2@rev1', 'constraint', '不能往本机 12883 的 seekdb 实例写数据', '那是内核赛道的验证实例。', ''],
    ['atomic-memory/m3@rev1', 'next_step', '找 3-5 位同学真实用两个周期', '记录写周报前后耗时对比。', ''],
    ['atomic-memory/m4@rev1', 'open', '两周采纳数据仍未覆盖', '等待同学开始使用。', ''],
  ];
  return {
    entries: rows.map(([cite, kind, title, body, repo], index) => ({
      id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
      cite, kind, title, body, repo, author: 'cointem', ...base,
    })),
    reports: [],
  };
}

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml' };

export function startServer({ port = 8123, tables = fixtures() } = {}) {
  const db = makeDb(tables);
  const deps = {
    db,
    BackendError: FakeError,
    requireUser: (ctx) => { if (!ctx.user) throw new FakeError('login_required', 401); return ctx.user; },
  };
  const handle = application(deps);

  const server = createServer(async (req, res) => {
    const url = new URL(req.url, `http://127.0.0.1:${port}`);
    try {
      if (url.pathname.startsWith('/functions/')) {
        const request = new Request(url, {
          method: req.method,
          headers: req.headers,
          body: ['POST', 'PATCH', 'PUT', 'DELETE'].includes(req.method) ? req : undefined,
          duplex: 'half',
        });
        const user = req.headers['x-local-user'];
        const response = await handle({ request, user: user ? { id: String(user), name: String(user) } : null, requestId: 'local' });
        res.writeHead(response.status, { 'content-type': response.headers.get('content-type') || 'application/json' });
        res.end(response.body ? Buffer.from(await response.arrayBuffer()) : null);
        return;
      }
      const rel = normalize(url.pathname === '/' ? 'index.html' : url.pathname.slice(1));
      if (rel.startsWith('..')) throw new FakeError('invalid_path', 400);
      const body = await readFile(join(WEB, rel));
      res.writeHead(200, { 'content-type': MIME[extname(rel)] || 'application/octet-stream', 'cache-control': 'no-store' });
      res.end(body);
    } catch (error) {
      res.writeHead(error?.status || 500, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: error?.code || 'local_failure' }));
    }
  });
  return new Promise((done) => server.listen(port, '127.0.0.1', () => done({ server, tables, port })));
}

if (resolve(process.argv[1] || '') === resolve(import.meta.filename)) {
  const { port } = await startServer({ port: Number(process.argv[2]) || 8123 });
  console.log(`Recap 本地后端：http://127.0.0.1:${port}/ （内存数据，未连接云端）`);
}
