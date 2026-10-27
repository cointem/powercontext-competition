# 架构与数据流

Recap 只有四个动作：**读痕迹 → 写进 PowerContext → 镜像进 seekdb → 确定性合成**。
每个动作都能单独重跑，且都可从命令行的 JSON 输出核对。

```
本机工作痕迹                       PowerContext Server(:8100)                嵌入式 seekdb
─────────────────                  ────────────────────────────              ─────────────
git log --author=cointem   ──┐     Source 日志（只追加、内容不可变）  ──┐
notes/*.md 的 - [决定] 行  ──┤     Atomic Memory（active/forgotten） │
                             │     Handoff（单一 lineage，版本化）   │
                             ▼                                       ▼
                       ingest.collect()                        indexer.refresh()
                       pctx.capture / remember_new              build_docs → upsert
                       work_contract / snapshot / commit        delete_mirrored 先丢弃旧镜像
                       acknowledge / record_outcome
                                                                  ▼
                                                            recap_docs 集合
                                                            document + embedding + metadata(JSON)
                                                                  │
                             report.compose()  ◀──────────────────┘
                             ├─ 窗口内进展：index.search(after=since) + created_key 边界 + kind ∈ {evidence, note}
                             ├─ 决定/约束/状态：active_memories 按 MEMORY_ORDER 排序、去重
                             ├─ 未决与下周：handoff 的 omissions / next_action + memory 的 state / next_step
                             └─ 历史回溯：index.search(topic, before=since) —— PowerContext 不会答的跨周问题
                                    │
                                    ▼
                       render_markdown() → data/report-<周>.md
                       web_export.export() → data/snapshot.json（一次运行的完整载荷，留档）
                       web_export.export_seed() → web/seed.json → 站点登录后导入云端 entries 表
```

## 云端那一半

```
web/app.js ──同源 fetch──▶ /functions/v1/app/api/*  （Qoder Sites Function，edge/Deno）
                                     │  functions/index.ts 只做注入：createDatabaseClient / requireUser / BackendError
                                     ▼
                              functions/routes.mjs   全部业务逻辑都在这里（与本地 dev/function-local.mjs 同一份）
                                     │
                       ┌─────────────┴─────────────┐
                       ▼                             ▼
                 app.entries                    app.reports
                 一条工作痕迹一列，              一次快照一行（markdown + 分桶 JSON），
                 cite UNIQUE 做幂等键            按 week 索引
```

| 路由 | 读/写 | 说明 |
| --- | --- | --- |
| `GET /api/health`、`/api/me` | 读 | 匿名可读；`/api/me` 只回是否登录与署名 |
| `GET /api/weeks` | 读 | 按周聚合条数与日期窗，返回 `scanned / truncated` |
| `GET /api/report?week=` | 读 | 现场组合（分桶 + 去重 + 引用清单 + Markdown），附带历史快照列表 |
| `GET /api/entries?q&from&to&kind&week&limit` | 读 | 最近 1000 条内有界扫描，空格分词按 AND 命中 |
| `GET /api/entry?id=` | 读 | 引用抽屉取原文 |
| `POST /api/report` | 写 | 登录，落一份快照行 |
| `POST /api/entry` | 写 | 登录 + `Idempotency-Key`，`cite=web:<key>`，重放同一条不新增 |
| `POST /api/import` | 写 | 登录，1–100 条一批，已存在的 `cite` 跳过 |

云端没有 embedding，也没有全文索引，所以检索是扫描 + 内存过滤；`web/seed.json` 由
`recap seed` 从同一批 seekdb 镜像里筛出（跳过 Handoff/报告与 JSON 信封这类流程记录）。
换账号发布前的静态前身仍然在线，用作对照。

## 模块职责

| 文件 | 只做这件事 | 为什么单独存在 |
| --- | --- | --- |
| `traces.py` | 把 `git log` 与 markdown 读成结构化记录 | 唯一读磁盘的模块，其余都不碰文件系统 |
| `pctx.py` | PowerContext 异步客户端的门面 + 引用归一 | 服务端的四条硬规则都在这里落地（见下） |
| `ingest.py` | 一周的写入编排 | 幂等性、"一个 Scope 一个 Handoff 边界"都住在这里 |
| `store.py` | seekdb 集合、混合检索、裸 SQL 与输入白名单 | 唯一的检索层出口，SQL 只在这一处拼接 |
| `indexer.py` | 决定"什么值得进镜像" | 丢弃信封类 Source（memory-entry）与同一痕迹的旧改写（`_trace_key`），索引才可读 |
| `report.py` | 确定性合成与渲染 | 不做生成，只做筛选、排序、去重、挂引用 |
| `web_export.py` | 把一次真实运行导出成留档载荷与云端导入源 | 网站不是第二套实现：数据只从同一条 `build_docs()` / `compose()` 路径来 |
| `cli.py` | 参数、状态文件（Scope id）、输出 | 七个子命令，评审照抄的就是这一层 |

## 服务端教给 Recap 的规则

这些不是设计偏好，是被 4xx 与驱动行为逼出来的，`tests/` 里都有对应用例：

1. **Source 身份不可变。** 同一个 `source_id` 换内容会被拒（409 `source_conflict`）。
   → `pctx.content_key(label, payload)` 把内容指纹折进 id：内容没变就回放，变了就追加一个新版本。
2. **Atomic Memory 没有身份键。** 重复 remember 只会不断新建条目（第一次跑就堆出 49 条重复）。
   → `remember_new()` 先 `active_memories()` 召回再写；报告侧 `_deduped()` 再兜一层。
3. **一个 Scope 只有一条 Handoff lineage**（`artifact_id` 就是 `handoff`）。每个仓库各开一个边界会互相覆盖。
   → `_snapshot_week()` 每周只开一个边界覆盖所有仓库；`_already_committed()` 让重复运行停在 `handoff/handoff@8`。
4. **`acknowledge_handoff` 要求 `receiver` 等于认证主体**，配死的 receiver 会撞 422 `invalid_access_request`。
   → `PowerContext.principal_id()` 从 `/v1/access/me` 取真实 Principal 并缓存，配置里的 `receiver` 字段被删掉。

另外两条属于 seekdb 侧：JSON metadata 里的字符串范围比较会被拒（`Invalid argument code=1210`），
所以时间窗用整数日键 `created_key=20261020`；`pylibseekdb` 的 cursor 不支持绑定参数、也没有
`description`，所以裸 SQL 走白名单转义、返回的元组按 `SQL_COLUMNS` 投影。

第五条属于日志语义本身：Source 只追加，改过的笔记重采会留下多个版本。`_trace_key()`
用提交 sha / 笔记标题认出"这是同一条痕迹"，镜像只保留 position 最大的那一版。

## 可丢弃的部分

`recap index` 每次都先 `delete_mirrored()` 再从 PowerContext 全量重建：索引不是第二份事实，
删了就能重长。唯一被保留的是 `family="report"` 的文档——那是 Recap 自己产出的周报归档，
是下一周"历史回溯"的检索语料。
