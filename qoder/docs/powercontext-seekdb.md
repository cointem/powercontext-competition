# PowerContext 与 seekdb 的分工：谁承重，承什么重

这份项目有两个数据库，很容易做成"挂个名字"。逐条说清各自不可替代的部分，以及
**去掉其中一个会坏掉什么**。

## 一句话分工

- **PowerContext 决定"这条结论能不能被引用"**：它保存工作痕迹本身，带出处、带版本、带生命周期。
- **seekdb 决定"这条结论还和哪些过去有关"**：它保存全量镜像，回答时间窗、跨周话题召回与向量近似。

## PowerContext 承的重

| 能力 | 在 Recap 里的用法 | 换掉它会怎样 |
| --- | --- | --- |
| Source 日志（只追加、内容不可变、按 position 有序） | 每条 git 提交、每篇笔记都是一条 Source，`source/src-…` 直接成为周报的引用 | 引用会变成"我说我做过"。普通日志文件可以随意改写，无法证明周报没被事后编辑 |
| Scope 隔离 | 竞赛交付单独一个 Scope（`scp_46d9ve5qr8y5t29g2b4sdtk7hf`），换机器只要指回同一个 Scope 就能续上 | 周报会把无关项目的提交混进来 |
| Atomic Memory + 生命周期（active/forgotten） | `- [决定]` / `- [约束]` 行成为记忆条目，报告按 `decision/constraint/state` 取"当前仍然成立"的部分 | 决定散落在提交信息里，无法表达"这条已经作废" |
| Handoff 工作循环（Work Contract → boundary → commit → acknowledge → outcome） | 一周的状态成为一个版本化 Handoff；`omissions` 让周报能如实写"未覆盖未提交的本机草稿" | 周报只能报喜，阻塞与缺口无处安放 |
| 认证主体（`/v1/access/me`） | 回执必须以真实 Principal 署名（`receiver == principal.id`，否则 422） | 手写 receiver 字段——已经被服务端拒过一次 |

**它答不了的问题**：Scope 内跨全量档案的时间窗召回。`search_atomic_memory` 是 Scope 作用域的
召回，而运行中的 Server 通过 capabilities 报出的检索模式只有

```
search_modes = ['auto', 'fts']        # Recap 客户端向本机 8100 Server 实测取回
```

也就是这台 Server 没有配置 embedding provider，只有全文可用（Recap 因此始终用
`mode="text"` 调它，见 `pctx.search_memories()`）。而周报的"历史回溯"需要的是
"六周前那条同样的阻塞在哪儿出现过" —— 向量近似 + 全文 + 时间窗同时成立。这正是 seekdb 的位置。

## seekdb 承的重

| 能力 | 在 Recap 里的用法 | 换掉它会怎样 |
| --- | --- | --- |
| 嵌入式（embedded）运行，不需要独立进程 | `RecapIndex(path="data/seekdb-recap")`，评审 clone 完就能跑，不必装数据库服务 | 交付物多一个外部服务依赖，一键复现就不成立 |
| `hybrid_search`：全文 + knn + RRF 融合 | `store.RecapIndex.search()` 一次拿到窗口内进展与跨周相关记录 | 只能做关键词匹配，中文近义表述召回不了 |
| `MATCH … AGAINST` + `JSON_VALUE(metadata,…)` + `L2_DISTANCE(…)` 在**一条语句**里 | `raw_hybrid_sql()`，旧静态站第 04 节"一条 SQL"展示的就是它真实执行后的语句与返回行 | 三次查询、三次排序，时间窗与相似度无法联合约束 |
| 向量列与自动 embedding（`pyseekdb.DefaultEmbeddingFunction()`） | 镜像每条文档都有 embedding，`index.embed(phrase)` 用同一个模型出查询向量 | 语义召回缺失，Server 那边也补不上（见上） |
| 同一内核还是 PowerContext Server 自己的存储后端 | `deploy/powercontext.env` 里 `POWERCONTEXT_SERVER_DATABASE_KIND=seekdb` | "两个组件其实是同一个内核的两种用法"这件事就只能靠嘴说 |

物理层可核对：集合 `recap_docs` 落到表 `c$v2$1f1390c0d13a11f19c1ccaaafa3a2043`，
列 `['_id','document','embedding','metadata']`（`recap doctor` 会打印同一行）。

## 去掉哪一个会坏掉什么

- **去掉 PowerContext，只留 seekdb**：还能搜，但周报的每一条引用都失去了不可变出处——
  索引是可以随时 upsert 覆盖的，无法证明"这行是本周真实提交留下的"。
- **去掉 seekdb，只留 PowerContext**：本周周报仍能生成（进展来自 Source、结论来自 Memory），
  但"历史回溯"整节直接空掉，跨周召回退化为 Scope 内关键词；`recap search --raw-sql` 也没了。
  这台 Server 只有 `fts`，连语义近似都补不上。

两处都不是装饰：一个给出处，一个给召回。
