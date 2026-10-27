/* Recap 云端工作区：所有数据都来自站点自己的 Function，页面不内嵌任何样例内容。 */
const API = '/functions/v1/app/api';
const LOGIN = '/__qoder_auth/start?return_path=%2F';
const KINDS = [
  ['commit', '提交'], ['note', '记录'], ['decision', '决定'], ['constraint', '约束'],
  ['next_step', '下一步'], ['state', '状态'], ['fact', '事实'], ['open', '未决'],
];
const KIND_NAME = Object.fromEntries(KINDS);
const COPY = {
  login_required: '需要先用 Qoder 账号登录才能写入。',
  invalid_week: '周格式应为 2026-W44。',
  invalid_day: '日期格式不正确。',
  invalid_kind: '类型不在允许范围内。',
  invalid_title: '标题不能为空且不超过 200 字。',
  invalid_body: '正文不能为空且不超过 2000 字。',
  invalid_author: '署名不能为空且不超过 40 字。',
  invalid_cite: '引用格式不被接受。',
  missing_idempotency_key: '缺少幂等键。',
  idempotency_conflict: '同一个幂等键已经写过不同内容，请先核对已有记录。',
  no_entries: '这一周还没有可组合的痕迹。',
  not_found: '没有找到对应记录。',
  payload_too_large: '提交内容超出大小限制。',
  database_not_configured: '站点数据库尚未就绪。',
  database_credential_rejected: '站点数据库凭据被拒绝，需要平台侧恢复。',
};
const state = { week: null, weeks: [], signedIn: null, markdown: '', seed: null };

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

class ApiError extends Error {
  constructor(code, status) { super(code); this.code = code; this.status = status; }
}

async function api(path, { method = 'GET', body, headers = {} } = {}) {
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      method,
      credentials: 'same-origin',
      headers: { ...(body === undefined ? {} : { 'content-type': 'application/json' }), ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('network', 0);
  }
  let json = null;
  try { json = await response.json(); } catch { /* 网关或登录页返回的不是 JSON */ }
  if (!response.ok) throw new ApiError(json?.error || `http_${response.status}`, response.status);
  if (!json || typeof json !== 'object') throw new ApiError('invalid_response', 502);
  return json;
}

function say(message, isError = false) {
  const node = $('status');
  node.textContent = message || '';
  node.classList.toggle('err', Boolean(isError));
}

const errText = (error) => COPY[error.code] || (error.status === 404
  ? '接口不存在，请确认访问的是已发布站点。'
  : error.status === 503 ? '依赖暂时不可用，请稍后重试。' : `请求失败（${error.code}）。`);

function isoWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

// local date, not UTC: a CST user submitting just after midnight is on the next day
const today = () => new Date().toLocaleDateString('en-CA');

/* ---------- citations ---------- */

function citeButton(item) {
  const button = el('button', 'cite', item.cite.length > 34 ? `${item.cite.slice(0, 17)}…${item.cite.slice(-8)}` : item.cite);
  button.type = 'button';
  button.title = '查看原始记录';
  button.addEventListener('click', () => openEntry(item));
  return button;
}

async function openEntry(item) {
  const drawer = $('drawer');
  drawer.hidden = false;
  drawer.textContent = '';
  drawer.append(el('p', 'empty', '读取原始记录…'));
  drawer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  try {
    const entry = item.id ? await api(`/entry?id=${encodeURIComponent(item.id)}`) : null;
    drawer.textContent = '';
    const head = el('div', 'head');
    const kind = KIND_NAME[entry?.kind || item.label] || '';
    head.append(el('span', '', [entry?.day || item.day, kind].filter(Boolean).join(' · ')));
    const close = el('button', 'ghost', '关闭');
    close.type = 'button';
    close.addEventListener('click', () => { drawer.hidden = true; drawer.textContent = ''; });
    head.append(close);
    drawer.append(head, el('h3', '', entry?.title || item.text), el('pre', '', entry?.body || item.body || '（无正文）'));
    const meta = el('p', 'small');
    meta.append(
      el('span', 'mono', `引用 ${entry?.cite || item.cite}`),
      el('span', '', ` · 来源 ${entry?.origin === 'web' ? '网页提交' : '本机导入'}`),
      el('span', '', entry?.author ? ` · ${entry.author}` : ''),
      el('span', '', entry?.repo ? ` · ${entry.repo}` : ''),
    );
    drawer.append(meta);
  } catch (error) {
    drawer.textContent = '';
    drawer.append(el('p', 'status err', errText(error)));
  }
}

/* ---------- report ---------- */

function fillBuckets(target, items) {
  const list = el('ul');
  for (const item of items) {
    const li = el('li');
    li.append(el('span', '', item.text));
    if (item.repo) li.append(' ', el('span', 'repo-tag', item.repo));
    li.append(' ', citeButton(item));
    list.append(li);
  }
  target.append(list);
}

function renderReport(report) {
  const host = $('report');
  host.textContent = '';
  state.markdown = report.markdown;
  host.append(el('h4', '', `${report.window.since} – ${report.window.until} · ${report.entry_count} 条痕迹`));
  if (!report.entry_count) {
    host.append(el('p', 'empty', '这一周还没有痕迹。去「痕迹板」提交一条，或登录后导入本机痕迹。'));
  } else {
    for (const [heading, key] of [['本周进展', 'progress'], ['关键决定与约束', 'decisions'], ['未决与阻塞', 'open_items'], ['下周计划', 'next_week']]) {
      if (!report[key].length) continue;
      host.append(el('h4', '', heading));
      fillBuckets(host, report[key]);
    }
  }
  const last = report.snapshots?.[0];
  $('report-foot').textContent = last
    ? `已保存快照：${last.created_at}（${last.entry_count} 条）。`
    : '尚未保存快照。';
}

async function loadReport() {
  say('组合周报中…');
  try {
    const report = await api(`/report?week=${encodeURIComponent(state.week)}`);
    renderReport(report);
    say('');
  } catch (error) {
    say(errText(error), true);
  }
}

/* ---------- weeks ---------- */

async function loadWeeks() {
  const data = await api('/weeks');
  state.weeks = data.weeks;
  const picker = $('week-picker');
  picker.textContent = '';
  const options = [...data.weeks.map((row) => row.week)];
  const now = isoWeek(new Date());
  if (!options.includes(now)) options.unshift(now);
  for (const week of options) {
    const row = data.weeks.find((item) => item.week === week);
    const option = el('option', '', row ? `${week}（${row.entries} 条）` : `${week}（本周，还没有痕迹）`);
    option.value = week;
    picker.append(option);
  }
  if (!state.week || !options.includes(state.week)) state.week = options[0];
  picker.value = state.week;
  renderMeta(data);
}

function renderMeta(data) {
  const chips = $('meta');
  chips.textContent = '';
  const total = data.weeks.reduce((sum, row) => sum + row.entries, 0);
  for (const [label, value] of [
    ['入库痕迹', `${total} 条`],
    ['覆盖', `${data.weeks.length} 周`],
    ['身份', state.signedIn === null ? '检测中' : state.signedIn ? '已登录' : '匿名只读'],
  ]) {
    const chip = el('span', 'chip');
    chip.append(el('span', '', `${label} `), el('b', '', value));
    chips.append(chip);
  }
}

/* ---------- search ---------- */

function renderResults(data) {
  const host = $('results');
  host.textContent = '';
  const head = el('div', 'chip');
  head.append(el('b', '', `${data.matched} 条结果`));
  if (data.truncated) head.append(el('span', '', ` · 已扫描最近 ${data.scanned} 条`));
  host.append(head);
  const box = el('div', 'probe');
  if (!data.items.length) {
    box.append(el('p', 'empty', '没有匹配的痕迹。换个关键词或放宽日期。'));
  }
  for (const item of data.items) {
    const hit = el('div', 'hit');
    hit.append(el('span', 'scores', `${item.day} · ${KIND_NAME[item.kind] || item.kind}`));
    const txt = el('span', 'txt');
    txt.append(el('b', '', item.title));
    txt.append(el('div', 'small', `${(item.body || '').slice(0, 160)}${(item.body || '').length > 160 ? '…' : ''}`));
    txt.append(' ', citeButton(item));
    if (item.repo) txt.append(' ', el('span', 'repo-tag', item.repo));
    hit.append(txt);
    box.append(hit);
  }
  host.append(box);
}

async function runSearch(event) {
  event.preventDefault();
  const params = new URLSearchParams();
  for (const [id, key] of [['q', 'q'], ['from', 'from'], ['to', 'to'], ['kind-filter', 'kind'], ['limit', 'limit']]) {
    const value = $(id).value.trim();
    if (value) params.set(key, value);
  }
  say('检索中…');
  try {
    const data = await api(`/entries?${params}`);
    renderResults(data);
    say('');
  } catch (error) {
    say(errText(error), true);
  }
}

/* ---------- board ---------- */

const DRAFT = 'recap.pending.entry';

function readDraft() {
  try { return JSON.parse(localStorage.getItem(DRAFT) || 'null'); } catch { return null; }
}

function entryPayload() {
  return {
    week: isoWeek(new Date(`${$('entry-day').value || today()}T12:00:00Z`)),
    day: $('entry-day').value || today(),
    kind: $('entry-kind').value,
    title: $('entry-title').value.trim(),
    body: $('entry-body').value.trim(),
    repo: $('entry-repo').value.trim(),
    author: $('entry-author').value.trim(),
  };
}

async function submitEntry(event) {
  event.preventDefault();
  const payload = entryPayload();
  if (!payload.title || !payload.body || !payload.author) {
    say('标题、正文、署名都要填。', true);
    return;
  }
  let draft = readDraft();
  if (!draft || JSON.stringify(draft.payload) !== JSON.stringify(payload)) {
    draft = { key: crypto.randomUUID(), payload };
    localStorage.setItem(DRAFT, JSON.stringify(draft));
  }
  $('btn-submit').disabled = true;
  say('提交中…');
  try {
    const created = await api('/entry', { method: 'POST', body: payload, headers: { 'idempotency-key': draft.key } });
    localStorage.removeItem(DRAFT);
    say(`已入库：${created.cite}`);
    $('entry-form').reset();
    $('entry-day').value = today();
    await Promise.all([loadWeeks(), loadRecent()]);
  } catch (error) {
    say(error.status === 0 || error.status >= 500
      ? `${errText(error)} 结果未知：再点一次提交会用同一条命令核对，不会多出第二条。`
      : errText(error), true);
  } finally {
    $('btn-submit').disabled = false;
  }
}

async function loadRecent() {
  try {
    const data = await api('/entries?limit=12');
    const host = $('recent');
    host.textContent = '';
    for (const item of data.items) {
      const row = el('div', 'entry');
      row.append(el('div', 'head', `${item.day} · ${KIND_NAME[item.kind] || item.kind} · ${item.origin === 'web' ? '网页' : '本机'}`));
      row.append(el('div', 'txt', item.title), ' ', citeButton(item));
      host.append(row);
    }
    if (!data.items.length) host.append(el('p', 'empty', '还没有痕迹。提交一条，或导入本机痕迹。'));
  } catch (error) {
    say(errText(error), true);
  }
}

async function loadSeed() {
  try {
    const response = await fetch('seed.json', { cache: 'no-store' });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

async function importSeed() {
  if (!state.signedIn) {
    say('导入需要先登录。', true);
    return;
  }
  state.seed = state.seed || await loadSeed();
  if (!state.seed?.items?.length) {
    say('没有读到 seed.json，请先在本机执行 `recap seed`。', true);
    return;
  }
  $('btn-import').disabled = true;
  const totals = { received: 0, inserted: 0, already_present: 0 };
  say(`导入 ${state.seed.items.length} 条本机痕迹…`);
  try {
    for (let offset = 0; offset < state.seed.items.length; offset += 50) {
      const chunk = state.seed.items.slice(offset, offset + 50);
      const result = await api('/import', { method: 'POST', body: { items: chunk } });
      for (const key of Object.keys(totals)) totals[key] += result[key] || 0;
    }
    $('import-result').textContent = `新入库 ${totals.inserted} 条，已存在 ${totals.already_present} 条`;
    say(`导入完成：新入库 ${totals.inserted} 条。`);
    await Promise.all([loadWeeks(), loadRecent()]);
  } catch (error) {
    say(errText(error), true);
  } finally {
    $('btn-import').disabled = false;
  }
}

async function checkMe() {
  try {
    const me = await api('/me');
    state.signedIn = Boolean(me.signed_in);
    if (state.signedIn && !$('entry-author').value) $('entry-author').value = me.name || '';
  } catch {
    state.signedIn = false;
  }
  const hint = $('board-hint');
  hint.textContent = '';
  if (state.signedIn) {
    hint.append(el('span', '', '已登录，提交会记录你的署名。'));
  } else {
    hint.append(el('span', '', '登录后才能提交。'), ' ', el('a', '', '用 Qoder 登录'), ' ');
    hint.querySelector('a').href = LOGIN;
  }
  $('btn-submit').disabled = !state.signedIn;
  $('btn-snapshot').disabled = !state.signedIn;
  $('btn-import').disabled = !state.signedIn;
}

/* ---------- about ---------- */

async function loadService() {
  const host = $('service');
  host.textContent = '';
  try {
    const health = await api('/health');
    const seed = state.seed || (state.seed = await loadSeed());
    const table = el('table');
    const head = el('tr');
    for (const name of ['项', '值']) head.append(el('th', '', name));
    table.append(head);
    const rows = [
      ['服务', `${health.service} · 路由 ${health.routes.join('/')}`],
      ['本机导出', seed ? `${seed.meta.generated_at} · ${seed.meta.docs} 条 · Scope ${seed.meta.scope_id}` : '本站未附带 seed.json'],
      ['导出来源', seed ? `seekdb 集合 ${seed.meta.collection}（物理表 ${seed.meta.table}）` : '—'],
      ['身份入口', '/__qoder_auth/me（只用于显示署名）'],
    ];
    for (const [key, value] of rows) {
      const tr = el('tr');
      tr.append(el('td', '', key), el('td', 'mono', value));
      table.append(tr);
    }
    host.append(table);
  } catch (error) {
    host.append(el('p', 'status err', errText(error)));
  }
}

/* ---------- wiring ---------- */

function switchView(name) {
  for (const tab of document.querySelectorAll('.tab')) {
    tab.setAttribute('aria-selected', String(tab.dataset.view === name));
  }
  for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== `view-${name}`;
  $('drawer').hidden = true;
  if (name === 'board') loadRecent();
  if (name === 'about') loadService();
  if (name === 'search') $('q').focus();
}

function fillKindSelects() {
  const filter = $('kind-filter');
  filter.append(el('option', '', '全部类型'));
  filter.lastChild.value = '';
  for (const [value, label] of KINDS) {
    for (const host of [filter, $('entry-kind')]) {
      const option = el('option', '', label);
      option.value = value;
      host.append(option);
    }
  }
}

function bind() {
  for (const tab of document.querySelectorAll('.tab')) tab.addEventListener('click', () => switchView(tab.dataset.view));
  $('week-picker').addEventListener('change', (event) => { state.week = event.target.value; loadReport(); });
  $('btn-reload').addEventListener('click', loadReport);
  $('btn-clear').addEventListener('click', () => { $('search-form').reset(); $('results').textContent = ''; say(''); });
  $('search-form').addEventListener('submit', runSearch);
  $('entry-form').addEventListener('submit', submitEntry);
  $('btn-import').addEventListener('click', importSeed);
  $('btn-snapshot').addEventListener('click', saveSnapshot);
  $('btn-copy').addEventListener('click', async () => {
    await navigator.clipboard.writeText(state.markdown);
    say('周报 Markdown 已复制到剪贴板。');
  });
  $('btn-download').addEventListener('click', () => {
    const blob = new Blob([state.markdown], { type: 'text/markdown;charset=utf-8' });
    const link = el('a');
    link.href = URL.createObjectURL(blob);
    link.download = `recap-${state.week}.md`;
    link.click();
    URL.revokeObjectURL(link.href);
  });
}

async function saveSnapshot() {
  $('btn-snapshot').disabled = true;
  say('保存快照…');
  try {
    const saved = await api('/report', { method: 'POST', body: { week: state.week } });
    renderReport({ ...saved, snapshots: [{ created_at: saved.created_at, entry_count: saved.entry_count }] });
    say(`已保存快照（${saved.entry_count} 条痕迹）。`);
  } catch (error) {
    say(errText(error), true);
  } finally {
    $('btn-snapshot').disabled = !state.signedIn;
  }
}

async function boot() {
  fillKindSelects();
  $('entry-day').value = today();
  const draft = readDraft();
  if (draft) {
    for (const [key, node] of Object.entries(draft.payload)) {
      const target = { week: null, day: $('entry-day'), kind: $('entry-kind'), title: $('entry-title'), body: $('entry-body'), repo: $('entry-repo'), author: $('entry-author') }[key];
      if (target && node) target.value = node;
    }
    say('上一次提交的结果未知，表单已按同一条命令回填；确认无误后再点提交。', true);
  }
  bind();
  await checkMe();
  try {
    await loadWeeks();
    state.week = $('week-picker').value;
    await loadReport();
  } catch (error) {
    say(errText(error), true);
  }
  switchView('report');
}

boot();
