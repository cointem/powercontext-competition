# 开发记录（Qoder Desktop 内完成）

这份项目全程在 Qoder Desktop 里开发：读题、拆方案、写代码、跑命令、被服务端 4xx 打回、
改测试，都在同一个会话链里进行，`notes/2026-W44.md` 与本文就是 Recap 自己的输入语料。

## 阶段与实际产出

| 阶段 | 做了什么 | 落下的东西 |
| --- | --- | --- |
| 读题与选型 | 读完赛题要求（seekdb / PowerContext 必须真实承重、Qoder Sites 发布、Qoder 开发记录），比较三种可交付形态，选定"从工作上下文自动重组周报" | 方案一 + 三条限制写进开发约定：不能凭空造 2–3 周采纳数据；公开发布需确认；开发目录与提交目录分离 |
| P0 骨架 | `pyproject.toml`（`powercontext[cli,server,seekdb]==1.4.0`、`pylibseekdb`、`pyseekdb`、`httpx`）、`src/recap/` 模块切分、`recap.config.json` | 8 个模块 + 6 个子命令的骨架 |
| P1 Server 与 Scope | 起本地 Server（127.0.0.1:8100，token 只进 gitignore 的 `deploy/powercontext.env`），`recap doctor` 建 Scope | Scope `scp_46d9ve5qr8y5t29g2b4sdtk7hf` |
| P2 采集 | `traces.py` 读 `git log --author` 与 `notes/*.md` 的 `- [决定]` 行；`ingest.py` 写 Source / Memory / Work Contract / Handoff / outcome | 三个仓库的真实提交进日志 |
| P3 合成 | `report.py` 确定性合成，禁用生成模型；每条 bullet 挂引用；引用解析不到就当作 bug | `data/report-2026-W44.md` |
| P4 检索 | `store.py` 打开嵌入式 seekdb 集合、`hybrid_search` RRF、`raw_hybrid_sql` 一条语句；`indexer.py` 每次从 PowerContext 重建镜像 | 集合 `recap_docs`，表 `c$v2$1f13…`，54 条文档 |
| P5 站点 | `web_export.py` 把一次真实运行导出成站点数据；`web/` 单页站渲染同一份数据（引用抽屉、检索探针、裸 SQL） | 站点内容 0 处手写数据 |
| P6 验收 | `tests/` 离线用例、README 运行说明与验收样本、`scripts/demo.sh` 一键复现、本文与架构/分工文档 | 见仓库 |
| D 站点改成应用 | 评审意见是"像个介绍，不是能用的提效应用"：给站点加 Function + 站点数据库，读公开、写要登录，周报在服务端现场组合 | `functions/routes.mjs`、`entries`/`reports` 两张表、`web/app.js` 全量重写、`node --test` 16 个路由用例 |
| D2 界面重做 | 评审第二次意见是"界面不能做好看点吗"：保留全部元素 ID 与行为，把单栏 hero + 横排页签重排成"左导航 + 主区"的应用外壳，统一设计令牌、间距、状态条、空态与 1080/620 两档响应式 | `web/index.html`、`web/styles.css` 重写；1440 / 1024 / 760 / 390 与空库五种截图逐页核对，101 pytest + 16 路由用例保持全绿 |
| D3 文案精简 | 评审第三次意见是"好多无意义的文字"：删掉侧栏页脚、四个视图的副标题、检索结果里的 `GET` 调试行与扫描上限、周报末尾的引用清单（引用仍在每条 bullet 上），空分组不再重复渲染，403/404 等状态改成一句可行动的提示 | `web/index.html`、`web/app.js`、`web/styles.css`；重新发布后核对线上 HTML/JS/API 与 1440/390 截图，测试仍为 101 + 16 |

## 被服务端与驱动打回的五次（都在测试里钉住了）

1. **HTTP 422 `invalid_access_request`（`receiver-principal`）** —— 配置里写死的
   `receiver: "recap-cli"` 在 `acknowledge_handoff` 时被拒：`status=accepted` 的回执必须以
   认证主体署名。改成 `PowerContext.principal_id()`（从 `/v1/access/me` 取，缓存），
   并把 `Config.receiver` 和 `recap.config.json` 里的这个字段整个删掉。
   → `tests/test_pctx.py`、`tests/test_config.py::test_config_dataclass_has_no_receiver_left`
2. **49 条重复 Atomic Memory** —— 原子记忆没有身份键，同一句"决定"每次 collect 都新建一条，
   周报的"关键决定"变成一堆复读。写入侧加 `remember_new()`（先 `active_memories()` 召回再写），
   合成侧加 `_deduped()`，并用 `change_atomic_memory_lifecycle(state="forgotten")`
   把已经堆出来的 42 条历史重复条目下线（验证：active 从 49 → 7）。
   → `tests/test_ingest.py::test_recollect_replays_without_new_memory`
3. **HTTP 409 `source_conflict`（"The Source identity has different content."）** —— 笔记改一个字，
   用同一个 `source_id` 再捕获就被拒。`content_key(label, payload)` 把内容指纹折进 id：
   没变回放、变了追加新版本。Handoff 侧同理，`_already_committed()` 让重复运行停在
   `handoff/handoff@8` 而不是每次 +1。
   → `tests/test_pctx.py::TestContentKey`、`tests/test_ingest.py::TestAlreadyCommitted`
4. **`- {` 开头的周报条目** —— Source 日志里存的是 JSON 信封（work-contract / boundary /
   outcome），镜像进索引后当成"进展"渲染了出来。加 `_source_kind()` 识别信封、
   `indexer` 跳过 `memory-entry` 回声、`report.PROGRESS_KINDS = {evidence, note}` 只认人读痕迹；
   另外 `Note.as_text` 会给自己再加一层 H1，导致周报里出现 `# # 标题`，一并修掉。
   → `tests/test_indexer.py::test_build_docs_skips_the_memory_envelope_echo`、
   `tests/test_traces.py::TestAsText`
5. **检索样本里同一条证据出现两遍，旧那份还带着 `# # 标题`** —— 写验收样本时才暴露：
   Source 内容不可变，笔记每改一次重采就多一个日志版本，`build_docs()` 把每个改写版本
   都当成独立记录镜像进索引。加 `_trace_key()`：提交按 sha、笔记按标题识别痕迹本身，
   同一痕迹只保留 position 最大的那一版（结构化信封仍按 `source_id` 各自保留）。
   重建镜像后文档数 66 → 54，报告结论不变。
   → `tests/test_indexer.py::test_build_docs_keeps_only_the_latest_version_of_a_trace`

seekdb 侧另外两条不是 4xx 但同样改变设计：JSON metadata 的字符串范围比较被拒
（`Invalid argument code=1210`）→ 时间窗改用整数日键 `created_key`；`pylibseekdb` 的 cursor
不支持绑定参数、`fetchall()` 返回元组且没有 `description` → 裸 SQL 走白名单转义，
导出层按 `SQL_COLUMNS` 投影。

## 平台上把动态方案打回的四次

1. **`sites_quota_exceeded（backend_sites used 0 / limit 0）`** —— 第一次尝试给站点分配
   database + Function 时账号没有后端配额。按平台的硬要求只能交付"纯静态"版本，
   并把升级入口如实转达，没有绕道（不自建后端、不改域名、不换供应商）。
2. **换账号后的 `sites_request_failed` + `list_sites` 返回 0 条** —— 看起来像站点没了，
   实际上老站用未登录 `curl` 仍然 200/64 KB：是身份换了，不是资源丢了。按恢复规则
   不新建替代品、不换 requestId，先恢复读操作确认事实，再按作者明确要求在当前账号下
   重新开一站（`createNewSite`，同一份源码目录）。
3. **`sites_database_not_ready`** —— 迁移刚应用完就读表结构。等平台目录刷新操作
   succeeded 之后再读，拿到 entries 12 列 / 4 索引、reports 7 列 / 2 索引与新的 `schemaRef`，
   才把它作为完整草稿的依赖。
4. **`sites_gateway_origin_forbidden`** —— 用 `curl` 直接 POST 写入被网关拦下（缺 `Origin`）。
   这不是路由的错：浏览器同源请求自带 `Origin`，补上之后同一请求返回
   `401 login_required`，正好证明"公开读 / 登录写"是两层都在生效。

## 有意不做的事

- **不编造采纳数据。** 赛题想看真实使用，今天不可能有 2–3 周的自然使用记录。README"已知边界"
  直接写明：只有一周真实痕迹，"历史回溯"为空是事实。
- **不让网站变成第二套实现。** 云端站点只读自己数据库里的行；数据库里的行来自
  `recap seed` 导出的 `web/seed.json`（同一批 seekdb 镜像筛出来的工作痕迹）或登录后的表单提交。
  页面上没有一个数字是手填的，也没有一份兜底样例。
- **不直接往云端表里塞数据。** 导入走应用自己的 `/api/import`（登录 + 校验 + 按 `cite` 幂等），
  绕开它写原始 SQL / 直接写行会把校验、幂等与"周由日期推导"这些保证弄丢。
- **不调用生成模型写周报。** 可解释性与"每条都能回溯"优先于文采。
- **不碰别人的实例。** 本机 12883 端口的 seekdb 是内核赛道验证实例，约束已写成
  Atomic Memory 并进入 `notes`；Recap 只用 `data/seekdb-recap`。`scripts/demo.sh` 也刻意用
  独立端口 + 独立 `POWERCONTEXT_HOME`，避免和已在运行的 Server 抢同一个数据目录。

## 写作与验证时的几次误判

`web/app.js` 曾在报告页留下"未能读取 data.json"的兜底文案，而顶部数据条已经渲染出来了：
`boot()` 里抛异常被 `.catch()` 吞掉，`DATA` 从未赋值。定位方式是先用浏览器快照确认结构存在、
再用脚本求值确认变量状态，最后把 `DATA` 提到模块作用域由 `boot(payload)` 赋值。
这条也说明：视觉渲染必须实测，不能只靠代码推断。

动态版另外三次是"本地全绿但数据不对"：

- Markdown 首行写出 `共 undefined 条工作痕迹` —— `renderMarkdown(report)` 在
  `report.entry_count` 赋值之前就被调用了。补了一条断言：渲染前计数字段必须已经在对象上。
- 导入云端的第一版 `web/seed.json` 有 43 条，其中 35 条的标题是 `{` —— PowerContext 会把
  work-contract / handoff / outcome 这些 JSON 信封也写成 Source，`seed_items()` 只按引用前缀
  过滤没看内容。现在按首行是不是 JSON 信封跳过，19 条才是真痕迹（`skipped_process_docs: 35` 如实写在 meta 里）。
- 页面默认日期用了 `toISOString()`（UTC），CST 凌晨提交会落到前一天。改成按本地日期取，
  同时把"周"改成服务端由 `day` 推导：客户端把 `week` 填错也不会把痕迹分到别的周。

本地验收方式沿用同一套：`node dev/function-local.mjs` 起同一份路由（内存表），
点周报/检索/痕迹板/引擎四个页签、开引用抽屉、试匿名提交（按钮禁用 + 状态条提示登录），
再用真实 `seed.json` 走一遍导入。内嵌浏览器的视口是 0×0，因此视觉验收改用本机 Chrome
无头模式（临时 profile）按 1440 / 1024 / 760 / 390 与空库各截一张，逐页核对布局与空态；
线上的 `HeadlessChrome` UA 会被网关按 "Bot detected" 挡成 403，截图前要把 UA 覆盖成普通
Chrome——这是网关策略，不是站点故障；`curl` 仍可用于核对读路由的 200 与写路由的 401。

## 待办（提交前需要作者确认的动作）

- ~~Qoder Sites 公开发布~~ 当前账号的动态版本已上线：<https://recap-weekly-report-mpe7vqhm9ob.qoder.zone>
  （`access_mode=public`，Function + 站点数据库；未登录 `curl` 实测读路由 200、写路由 `401 login_required`）。
  旧账号的静态前身 <https://recap-weekly-report-ov0o0n50g0i.qoder.zone> 仍然在线，作为对照保留。
- **登录后点一次「导入本机痕迹」**：云端 `entries` 表目前是空的，导入用的是站点自带的
  `/api/import`（19 条真实痕迹，按 `cite` 幂等）。这一步需要作者的 Qoder 登录态，助手无法代为登录。
- 把交付物同步进比赛私有仓库 `qoder/` 目录（分支 `2026-competition-ecosystem`）——
  涉及切换作者正在使用的仓库，需作者确认后再执行。
- 找 3–5 位同学连续跑两个自然周，记录"写周报前后耗时对比"，让"历史回溯"长出内容。
