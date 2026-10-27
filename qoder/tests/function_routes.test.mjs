// Route-level acceptance for the cloud business logic, run against the real
// `application()` with in-memory dependencies:
//
//   node --test tests/function_routes.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

import { application } from '../functions/routes.mjs';
import { makeDb, FakeError, fixtures } from '../dev/function-local.mjs';

const ORIGIN = 'https://recap.example/functions/v1/app';

function harness(rows = fixtures()) {
  const db = makeDb(rows);
  const handle = application({
    db,
    BackendError: FakeError,
    requireUser: (ctx) => { if (!ctx.user) throw new FakeError('login_required', 401); return ctx.user; },
  });
  const call = async (path, { method = 'GET', body, headers = {}, user = null } = {}) => {
    const request = new Request(`${ORIGIN}${path}`, {
      method,
      headers: { ...(body === undefined ? {} : { 'content-type': 'application/json' }), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    // Mirrors functions/index.ts: the platform turns a thrown BackendError into a
    // safe JSON error, so the tests assert the same shape the browser receives.
    let response;
    try {
      response = await handle({ request, user, requestId: 'test' });
    } catch (error) {
      response = Response.json({ error: error.code || 'internal', request_id: 'test' }, { status: error.status || 500 });
    }
    return { status: response.status, json: await response.json() };
  };
  return { rows, call };
}

test('health is anonymous', async () => {
  const { call } = harness();
  const { status, json } = await call('/api/health');
  assert.equal(status, 200);
  assert.equal(json.ok, true);
});

test('identity is reported without leaking a login requirement', async () => {
  const { call } = harness();
  assert.deepEqual(await call('/api/me'), { status: 200, json: { signed_in: false } });
  const signed = await call('/api/me', { user: { id: 'u-1', name: 'cointem' } });
  assert.equal(signed.json.signed_in, true);
  assert.equal(signed.json.name, 'cointem');
});

test('weeks group the stored traces and report the scan window', async () => {
  const { call } = harness();
  const { json } = await call('/api/weeks');
  assert.equal(json.weeks.length, 1);
  assert.equal(json.weeks[0].week, '2026-W44');
  assert.equal(json.weeks[0].entries, 6);
  assert.equal(json.scanned, 6);
  assert.equal(json.truncated, false);
});

test('search filters by terms, kind and day window', async () => {
  const { call } = harness();
  assert.equal((await call('/api/entries?q=seekdb')).json.matched, 2);
  assert.equal((await call('/api/entries?q=seekdb%20pyseekdb')).json.matched, 1);
  assert.equal((await call('/api/entries?q=%E6%9C%AA%E8%A6%86%E7%9B%96')).json.matched, 1);
  assert.equal((await call('/api/entries?kind=decision')).json.matched, 1);
  assert.equal((await call('/api/entries?from=2026-10-28')).json.matched, 0);
  assert.equal((await call('/api/entries?to=2026-10-27')).json.matched, 6);
  assert.equal((await call('/api/entries?week=2026-W44&q=%E5%91%A8%E6%8A%A5')).json.matched, 1);
});

test('search rejects bad parameters instead of ignoring them', async () => {
  const { call } = harness();
  assert.equal((await call('/api/entries?kind=nope')).json.error, 'invalid_kind');
  assert.equal((await call('/api/entries?from=22-10-2026')).json.error, 'invalid_day');
  assert.equal((await call('/api/entries?week=W44')).json.error, 'invalid_week');
});

test('a live report composes every bucket with citations', async () => {
  const { call } = harness();
  const { status, json } = await call('/api/report?week=2026-W44');
  assert.equal(status, 200);
  assert.equal(json.mode, 'live');
  assert.equal(json.progress.length, 2);
  assert.equal(json.decisions.length, 2);
  assert.equal(json.next_week.length, 1);
  assert.equal(json.open_items.length, 1);
  assert.equal(json.entry_count, 6);
  assert.deepEqual(json.refs, json.refs.slice().sort());
  for (const item of [...json.progress, ...json.decisions, ...json.next_week, ...json.open_items]) {
    assert.ok(item.cite && item.id, 'every line must be citable');
  }
  assert.match(json.markdown, /^# 周报 · 2026-W44/m);
  assert.match(json.markdown, /## 本周进展/);
  assert.match(json.markdown, /`source\/aaa111`/);
  // the count line is rendered from the same object, so it must be filled before render
  assert.ok(json.entry_count > 0);
  assert.ok(json.markdown.includes(`共 ${json.entry_count} 条工作痕迹`), json.markdown.split('\n')[2]);
  assert.deepEqual(json.snapshots, []);
});

test('an empty week still renders a deterministic window', async () => {
  const { call } = harness();
  const { json } = await call('/api/report?week=2026-W45');
  assert.equal(json.entry_count, 0);
  assert.deepEqual(json.window, { since: '2026-11-02', until: '2026-11-08' });
  assert.match(json.markdown, /窗口内没有|无。/);
});

test('report snapshots require login and persist a version', async () => {
  const { rows, call } = harness();
  const denied = await call('/api/report', { method: 'POST', body: { week: '2026-W44' } });
  assert.equal(denied.status, 401);
  assert.equal(denied.json.error, 'login_required');
  const created = await call('/api/report', { method: 'POST', body: { week: '2026-W44' }, user: { id: 'u-1' } });
  assert.equal(created.status, 201);
  assert.equal(created.json.mode, 'persisted');
  assert.equal(rows.reports.length, 1);
  const listed = await call('/api/report?week=2026-W44');
  assert.equal(listed.json.snapshots.length, 1);
  assert.equal(listed.json.snapshots[0].entry_count, 6);
});

test('a week with no traces cannot be reported', async () => {
  const { call } = harness();
  const empty = await call('/api/report', { method: 'POST', body: { week: '2026-W45' }, user: { id: 'u-1' } });
  assert.equal(empty.status, 409);
  assert.equal(empty.json.error, 'no_entries');
});

test('submitting a trace needs login, an idempotency key and valid fields', async () => {
  const { rows, call } = harness();
  const body = { week: '2026-W44', day: '2026-10-27', kind: 'note', title: '云端提交的一条痕迹', body: '来自站点表单。', author: 'cointem' };
  assert.equal((await call('/api/entry', { method: 'POST', body, user: null })).json.error, 'login_required');
  assert.equal((await call('/api/entry', { method: 'POST', body, user: { id: 'u-1' } })).json.error, 'missing_idempotency_key');
  const key = '11111111-1111-4111-8111-111111111111';
  const headers = { 'idempotency-key': key };
  const created = await call('/api/entry', { method: 'POST', body, headers, user: { id: 'u-1' } });
  assert.equal(created.status, 201);
  assert.equal(created.json.cite, `web:${key}`);
  assert.equal(created.json.origin, 'web');
  assert.equal(created.json.week, '2026-W44');
  assert.equal(rows.entries.length, 7);
  const replay = await call('/api/entry', { method: 'POST', body, headers, user: { id: 'u-1' } });
  assert.equal(replay.status, 200);
  assert.equal(replay.json.id, created.json.id);
  assert.equal(rows.entries.length, 7, 'a replay must not add a second record');
  const changed = await call('/api/entry', { method: 'POST', body: { ...body, title: '改了内容' }, headers, user: { id: 'u-1' } });
  assert.equal(changed.status, 409);
  assert.equal(changed.json.error, 'idempotency_conflict');
});

test('submitted text is shape-checked and flattened', async () => {
  const { rows, call } = harness();
  const headers = { 'idempotency-key': '22222222-2222-4222-8222-222222222222' };
  const bad = await call('/api/entry', { method: 'POST', body: { week: '2026-W44', day: '2026-10-23', kind: 'whatever', title: 'x', body: 'y', author: 'z' }, headers, user: { id: 'u-1' } });
  assert.equal(bad.json.error, 'invalid_kind');
  const before = rows.entries.length;
  const injected = await call('/api/entry', {
    method: 'POST',
    body: { week: '2026-W44', day: '2026-10-23', kind: 'note', title: '第一行\n第二行', body: '正常正文', author: 'cointem' },
    headers,
    user: { id: 'u-1' },
  });
  assert.equal(injected.status, 201);
  assert.equal(injected.json.title, '第一行 第二行');
  assert.equal(rows.entries.length, before + 1);
});

test('a declared week never overrides the week the day belongs to', async () => {
  const { call } = harness();
  const headers = { 'idempotency-key': '33333333-3333-4333-8333-333333333333' };
  const created = await call('/api/entry', {
    method: 'POST',
    body: { week: '2026-W52', day: '2026-10-23', kind: 'note', title: '周填错了', body: '应当按日期归档。', author: 'cointem' },
    headers,
    user: { id: 'u-1' },
  });
  assert.equal(created.json.week, '2026-W43');
  assert.equal((await call('/api/report?week=2026-W43')).json.entry_count, 1);
  assert.equal((await call('/api/report?week=2026-W52')).json.entry_count, 0);
});

test('import is login-gated, idempotent by citation and validates references', async () => {
  const { rows, call } = harness();
  const items = [
    { week: '2026-W44', day: '2026-10-22', kind: 'commit', title: '混合检索改成整数日键', body: '已有引用。', cite: 'source/aaa111', author: 'cointem', repo: 'recap' },
    { week: '2026-W44', day: '2026-10-23', kind: 'decision', title: '云端只做展示', body: '新条目。', cite: 'atomic-memory/new1@rev1', author: 'cointem' },
  ];
  assert.equal((await call('/api/import', { method: 'POST', body: { items } })).json.error, 'login_required');
  const user = { id: 'u-1' };
  const result = await call('/api/import', { method: 'POST', body: { items }, user });
  assert.equal(result.status, 200);
  assert.deepEqual(result.json, { received: 2, inserted: 1, already_present: 1 });
  assert.equal(rows.entries.length, 7);
  const again = await call('/api/import', { method: 'POST', body: { items }, user });
  assert.equal(again.json.inserted, 0);
  assert.equal(rows.entries.length, 7);
  const badCite = await call('/api/import', { method: 'POST', body: { items: [{ ...items[0], cite: 'https://evil/x' }] }, user });
  assert.equal(badCite.json.error, 'invalid_cite');
  assert.equal((await call('/api/import', { method: 'POST', body: { items: [] }, user })).json.error, 'invalid_batch');
});

test('one stored trace can be read back for a citation', async () => {
  const { call } = harness();
  const { json } = await call('/api/entries?q=%E5%BC%95%E7%94%A8');
  const id = json.items[0].id;
  const single = await call(`/api/entry?id=${id}`);
  assert.equal(single.json.cite, json.items[0].cite);
  assert.equal((await call('/api/entry?id=not-a-uuid')).json.error, 'invalid_id');
  assert.equal((await call('/api/entry?id=00000000-0000-4000-8000-0000000000ff')).status, 404);
});

test('unknown routes and methods stay errors', async () => {
  const { call } = harness();
  assert.equal((await call('/api/nope')).status, 404);
  assert.equal((await call('/api/entries', { method: 'DELETE' })).json.error, 'method_not_allowed');
  assert.equal((await call('/api/health', { method: 'POST' })).status, 404);
  const foreign = new Request('https://recap.example/other/path');
  const handle = application({ db: makeDb(fixtures()), BackendError: FakeError, requireUser: (ctx) => ctx.user });
  const status = await handle({ request: foreign, user: null }).then((r) => r.status, (error) => error.status);
  assert.equal(status, 404);
});

test('the logical and the physical function prefix both resolve', async () => {
  const rows = fixtures();
  const handle = application({
    db: makeDb(rows),
    BackendError: FakeError,
    requireUser: (ctx) => { if (!ctx.user) throw new FakeError('login_required', 401); return ctx.user; },
  });
  for (const base of ['https://h/functions/v1/app', 'https://h/app', `https://h/app-d${'0'.repeat(32)}`]) {
    const request = new Request(`${base}/api/health`);
    const response = await handle({ request, user: null, requestId: 'test' });
    assert.equal(response.status, 200, base);
  }
});
