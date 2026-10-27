// Business routes for the Recap site. Dependencies (database, verified identity)
// are injected so the same routes run locally with fakes and in the deployed
// Function; nothing here accepts a database endpoint, credential or owner ID
// from the browser.
export function application({ db, requireUser, BackendError }) {
  const uuid = (v) => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
  const fail = (code, status) => { throw new BackendError(code, status); };
  const rows = (result) => { if (result.error) fail(result.error.code, result.error.status); return result.data ?? []; };

  const WEEK_RE = /^\d{4}-W(0[1-9]|[1-4][0-9]|5[0-3])$/;
  const DAY_RE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
  const KINDS = ['commit', 'note', 'decision', 'constraint', 'next_step', 'state', 'fact', 'open'];
  // Cite is the natural key of a trace: the local engine's own references
  // (`source/…`, `atomic-memory/…@rev`) plus browser submissions (`web:<uuid>`).
  const CITE_RE = /^[a-z-]+\/[A-Za-z0-9@._:-]{1,80}$|^web:[0-9a-f-]{36}$/;
  const SCAN = 1000;
  const ENTRY_COLUMNS = 'id,week,day,kind,repo,author,owner_id,origin,title,body,cite,created_at';

  async function input(request, maxBytes) {
    if (!request.headers.get('content-type')?.startsWith('application/json')) fail('invalid_input', 400);
    const reader = request.body?.getReader();
    if (!reader) fail('invalid_input', 400);
    let size = 0;
    const chunks = [];
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > maxBytes) { await reader.cancel(); fail('payload_too_large', 413); }
        chunks.push(value);
      }
    } finally {
      reader.releaseLock();
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { fail('invalid_input', 400); }
  }

  const clean = (value, max) => {
    if (typeof value !== 'string') return null;
    // Control characters would let one stored record inject layout into every reader.
    const text = value.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
    return text && text.length <= max ? text : null;
  };

  function validateEntry(raw) {
    if (!raw || typeof raw !== 'object') fail('invalid_input', 400);
    if (!DAY_RE.test(String(raw.day ?? '')) || Number.isNaN(Date.parse(String(raw.day)))) fail('invalid_day', 400);
    if (!KINDS.includes(raw.kind)) fail('invalid_kind', 400);
    const title = clean(raw.title, 200); if (!title) fail('invalid_title', 400);
    const body = clean(raw.body, 2000); if (!body) fail('invalid_body', 400);
    const repo = clean(raw.repo, 40) ?? '';
    const author = clean(raw.author, 40); if (!author) fail('invalid_author', 400);
    const day = String(raw.day);
    // The week is derived from the day, never taken from the request: a trace filed
    // under a week its date does not belong to would silently vanish from that week.
    return { week: weekOfDay(day), day, kind: String(raw.kind), repo, author, title, body };
  }

  function weekOfDay(day) {
    const [year, month, number] = day.split('-').map(Number);
    const date = new Date(Date.UTC(year, month - 1, number));
    date.setUTCDate(date.getUTCDate() + 4 - (date.getUTCDay() || 7)); // Thursday of this ISO week
    const iso = date.getUTCFullYear();
    const week = Math.ceil(((date - Date.UTC(iso, 0, 1)) / 86400000 + 1) / 7);
    return `${iso}-W${String(week).padStart(2, '0')}`;
  }

  async function recentEntries() {
    return rows(await db.from('entries').select(ENTRY_COLUMNS).order('day', { ascending: false }).limit(SCAN));
  }

  function matches(entry, { q, from, to, kind, week }) {
    if (kind && entry.kind !== kind) return false;
    if (week && entry.week !== week) return false;
    if (from && entry.day < from) return false;
    if (to && entry.day > to) return false;
    if (q) {
      const haystack = `${entry.title} ${entry.body} ${entry.repo}`.toLowerCase();
      // Every term must appear (AND), so "seekdb 索引" narrows instead of widening.
      for (const term of q.toLowerCase().split(/\s+/).filter(Boolean)) {
        if (!haystack.includes(term)) return false;
      }
    }
    return true;
  }

  const KIND_LABEL = {
    commit: '提交', note: '记录', decision: '决定', constraint: '约束',
    next_step: '下一步', open: '未决', state: '状态', fact: '事实',
  };

  function compose(week, entries) {
    const inWeek = entries.filter((e) => e.week === week).sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0));
    const bucket = (...kinds) => {
      const seen = new Set();
      const out = [];
      for (const entry of inWeek) {
        if (!kinds.includes(entry.kind) || seen.has(entry.title)) continue;
        seen.add(entry.title);
        out.push({
          text: entry.title.replace(/^#+\s*/, ''),
          body: entry.body,
          cite: entry.cite,
          id: entry.id,
          repo: entry.repo,
          day: entry.day,
          label: KIND_LABEL[entry.kind] || entry.kind,
        });
      }
      return out;
    };
    const report = {
      week,
      window: windowOf(week, inWeek),
      progress: bucket('commit', 'note'),
      decisions: bucket('decision', 'constraint', 'state'),
      next_week: bucket('next_step'),
      open_items: bucket('open'),
    };
    report.refs = [...new Set([...report.progress, ...report.decisions, ...report.next_week, ...report.open_items].map((i) => i.cite))].sort();
    report.entry_count = inWeek.length;
    report.markdown = renderMarkdown(report);
    return report;
  }

  function windowOf(week, entries) {
    const days = entries.map((e) => e.day).sort();
    if (days.length) return { since: days[0], until: days[days.length - 1] };
    // An empty week still needs a displayable window, so derive it from the ISO week.
    const [year, number] = week.split('-W').map(Number);
    const jan1 = Date.UTC(year, 0, 1);
    const isoWeekday = ((new Date(jan1).getUTCDay() + 6) % 7) + 1;
    const monday = jan1 - (isoWeekday - 1) * 86400000 + (number - 1) * 7 * 86400000;
    const iso = (ms) => new Date(ms).toISOString().slice(0, 10);
    return { since: iso(monday), until: iso(monday + 6 * 86400000) };
  }

  const bullets = (items) => (items.length ? items : [{ text: '无。', cite: '', body: '', repo: '', day: '', label: '' }]);

  function renderMarkdown(report) {
    const line = (i) => `- ${i.text}${i.repo ? `（${i.repo}）` : ''}${i.cite ? ` \`${i.cite}\`` : ''}`;
    return [
      `# 周报 · ${report.week}`,
      '',
      `数据窗口 ${report.window.since} → ${report.window.until}，共 ${report.entry_count} 条工作痕迹。`,
      '每条结论后的引用可回溯到原始记录。',
      '',
      '## 本周进展',
      ...bullets(report.progress).map(line),
      '',
      '## 关键决定与约束',
      ...bullets(report.decisions).map(line),
      '',
      '## 未决与阻塞',
      ...bullets(report.open_items).map(line),
      '',
      '## 下周计划',
      ...bullets(report.next_week).map(line),
      '',
      '## 引用清单',
      ...(report.refs.length ? report.refs.map((r) => `- \`${r}\``) : ['- 无。']),
    ].join('\n');
  }

  return async (ctx) => {
    const request = ctx.request;
    // The Gateway replaces the logical `app` segment with the deployed function
    // name and may strip /functions/v1 before invoking; match either prefix.
    const pathname = new URL(request.url).pathname;
    const match = pathname.match(/^\/(?:functions\/v1\/)?(?:app|app-d[0-9a-f]{32})(\/.*)?$/);
    if (!match) fail('not_found', 404);
    const path = match[1] || '/';
    const query = new URL(request.url).searchParams;

    if (path === '/api/health' && request.method === 'GET') {
      return Response.json({ ok: true, service: 'recap', routes: ['weeks', 'report', 'entries', 'entry', 'me', 'import'] });
    }
    if (path === '/api/me' && request.method === 'GET') {
      try {
        const user = requireUser(ctx);
        return Response.json({ signed_in: true, name: user.name || user.id, id: user.id });
      } catch (error) {
        if (error instanceof BackendError && error.code === 'login_required') return Response.json({ signed_in: false });
        throw error;
      }
    }

    if (path === '/api/weeks' && request.method === 'GET') {
      const entries = await recentEntries();
      const byWeek = new Map();
      for (const entry of entries) {
        const row = byWeek.get(entry.week) || { week: entry.week, entries: 0, since: entry.day, until: entry.day, kinds: {} };
        row.entries += 1;
        row.since = row.since < entry.day ? row.since : entry.day;
        row.until = row.until > entry.day ? row.until : entry.day;
        row.kinds[entry.kind] = (row.kinds[entry.kind] || 0) + 1;
        byWeek.set(entry.week, row);
      }
      const weeks = [...byWeek.values()].sort((a, b) => (a.week < b.week ? 1 : -1));
      return Response.json({ weeks, scanned: entries.length, truncated: entries.length >= SCAN });
    }

    if (path === '/api/report' && request.method === 'GET') {
      const week = query.get('week') || '';
      if (!WEEK_RE.test(week)) fail('invalid_week', 400);
      const report = compose(week, await recentEntries());
      const snapshots = rows(await db.from('reports').select('id,created_at,entry_count').eq('week', week).order('created_at', { ascending: false }).limit(5));
      return Response.json({ ...report, mode: 'live', snapshots });
    }

    if (path === '/api/report' && request.method === 'POST') {
      const user = requireUser(ctx);
      const body = await input(request, 8192);
      const week = body?.week;
      if (typeof week !== 'string' || !WEEK_RE.test(week)) fail('invalid_week', 400);
      const report = compose(week, await recentEntries());
      if (!report.entry_count) fail('no_entries', 409);
      const id = crypto.randomUUID();
      const created_at = new Date().toISOString();
      rows(await db.from('reports').insert({
        id, week, markdown: report.markdown, lines: { progress: report.progress, decisions: report.decisions, next_week: report.next_week, open_items: report.open_items, refs: report.refs, window: report.window },
        entry_count: report.entry_count, owner_id: user.id, created_at,
      }));
      return Response.json({ id, created_at, ...report, mode: 'persisted' }, { status: 201 });
    }

    if (path === '/api/entries' && request.method === 'GET') {
      const filters = { q: query.get('q') || '', from: query.get('from') || '', to: query.get('to') || '', kind: query.get('kind') || '', week: query.get('week') || '' };
      if (filters.q.length > 80) fail('query_too_long', 400);
      if (filters.from && !DAY_RE.test(filters.from)) fail('invalid_day', 400);
      if (filters.to && !DAY_RE.test(filters.to)) fail('invalid_day', 400);
      if (filters.kind && !KINDS.includes(filters.kind)) fail('invalid_kind', 400);
      if (filters.week && !WEEK_RE.test(filters.week)) fail('invalid_week', 400);
      const limit = Math.min(Math.max(Number(query.get('limit')) || 30, 1), 100);
      const scanned = await recentEntries();
      const items = scanned.filter((entry) => matches(entry, filters));
      return Response.json({ items: items.slice(0, limit), matched: items.length, scanned: scanned.length, truncated: scanned.length >= SCAN });
    }

    if (path === '/api/entry' && request.method === 'GET') {
      const id = query.get('id') || '';
      if (!uuid(id)) fail('invalid_id', 400);
      const found = rows(await db.from('entries').select(ENTRY_COLUMNS).eq('id', id).limit(1));
      if (!found.length) fail('not_found', 404);
      return Response.json(found[0]);
    }

    if (path === '/api/entry' && request.method === 'POST') {
      const user = requireUser(ctx);
      const body = await input(request, 8192);
      const entry = validateEntry(body);
      const key = request.headers.get('idempotency-key');
      if (!uuid(key)) fail('missing_idempotency_key', 400);
      const cite = `web:${key}`;
      const id = crypto.randomUUID();
      const created_at = new Date().toISOString();
      const record = { id, ...entry, cite, origin: 'web', owner_id: user.id, created_at };
      const inserted = rows(await db.from('entries').insertIfAbsent(record, { onConflict: 'cite' }).select(ENTRY_COLUMNS));
      // A lost response is recovered by the same key, never by a second record.
      if (inserted.length) return Response.json(inserted[0], { status: 201 });
      const existing = (await recentEntries()).find((item) => item.cite === cite);
      if (!existing || existing.title !== entry.title || existing.body !== entry.body) fail('idempotency_conflict', 409);
      return Response.json(existing);
    }

    if (path === '/api/import' && request.method === 'POST') {
      requireUser(ctx);
      const body = await input(request, 262144);
      const items = Array.isArray(body?.items) ? body.items : null;
      if (!items || !items.length || items.length > 100) fail('invalid_batch', 400);
      const prepared = items.map((raw) => {
        const entry = validateEntry(raw);
        const cite = String(raw.cite ?? '');
        if (!CITE_RE.test(cite)) fail('invalid_cite', 400);
        const created_at = `${entry.day}T${(/^\d{2}:\d{2}$/.test(String(raw.time ?? '')) ? raw.time : '12:00')}:00Z`;
        return { id: crypto.randomUUID(), ...entry, cite, origin: 'local', owner_id: 'local', created_at };
      });
      const seen = new Set((await recentEntries()).map((entry) => entry.cite));
      const missing = prepared.filter((item) => !seen.has(item.cite));
      if (missing.length) rows(await db.from('entries').insert(missing));
      return Response.json({ received: prepared.length, inserted: missing.length, already_present: prepared.length - missing.length });
    }

    if (path === '/api/entries' && request.method !== 'GET') fail('method_not_allowed', 405);
    fail('not_found', 404);
  };
}
