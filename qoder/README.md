# Recap —— 把一周真实留下的痕迹，重组成本周报

2026 OceanBase 生态大赛 · Qoder 赛道交付物。

Recap 读你这一周**真实存在**的工作痕迹（git 提交、markdown 开发记录），把它们写成
PowerContext 的 Source / Atomic Memory / Handoff，再把这些 artifact 镜像进嵌入式 seekdb，
用一条混合检索查询补上"窗口之前谁也讨论过这件事"，最后**确定性地**合成一份周报——
每一条结论都挂着可回溯的引用，没有出处的句子按 bug 处理。整个过程中没有任何生成模型
参与写作，也没有一行内容是手工编造的样例。

- **在线站点（免登录可读）**：<https://recap-weekly-report-mpe7vqhm9ob.qoder.zone> —— 由 Qoder Sites 公开发布，
  前端 [`web/`](web/) + 云端 Function [`functions/`](functions/) + 站点自己的数据库（`entries` / `reports` 两张表）。
  页面上的每一条痕迹、每一次组合与检索都是 Function 现查的，不是静态快照。
- 本报告的一周真实产物：[`samples/report-2026-W44.md`](samples/report-2026-W44.md)（`recap report` 写到 gitignore 的 `data/`，仓库里留一份同内容样本）
- 云端痕迹板的导入源：[`web/seed.json`](web/seed.json)（由 `recap seed` 从同一条链路导出，19 条真实痕迹）

## 30 秒理解技术分工

| | PowerContext | seekdb |
| --- | --- | --- |
| 负责 | "现在仍然成立的是什么"：Scope、只追加的 Source 日志、版本化 Handoff、原子 Memory | "整份档案里跟这个话题相关的过去"：向量 + 全文 + 标量时间窗的混合检索 |
| 为什么需要它 | 工作痕迹必须有出处与版本，`atomic-memory/…@1`、`source/src-…` 才是可回溯的引用 | Server 自身只暴露 `search_modes=['auto','fts']`（无 embedding provider），跨周档案召回补不上 |
| 在 Recap 里 | 唯一的事实来源（index 每次从它重建，索引可丢弃） | 检索层，同时也是 PowerContext Server 自己的存储内核（`DATABASE_KIND=seekdb`） |

细节见 [docs/powercontext-seekdb.md](docs/powercontext-seekdb.md)，模块与数据流见
[docs/architecture.md](docs/architecture.md)。

## 快速开始

前置：macOS/Linux、Python 3.12+、[uv](https://docs.astral.sh/uv/)、curl。

```bash
cd qoder
scripts/demo.sh
```

`scripts/demo.sh` 是评审的一键复现路径：它在 `127.0.0.1:8101` 起一个**一次性的**
PowerContext Server（独立数据目录 `data/demo/`，不与本机已在运行的 Server 抢目录），
然后依次执行 `doctor → collect → index → report → search`，退出时关闭 Server。

想换输入源或端口，用环境变量，不必改脚本：

```bash
REPO=~/code/my-project AUTHOR=me SINCE=2026-10-20 UNTIL=2026-10-26 PORT=8201 scripts/demo.sh
```

在线站点已经可读：<https://recap-weekly-report-mpe7vqhm9ob.qoder.zone>。想在本地跑同一个应用
（改样式、换成自己的数据、验证写入路径）：

```bash
uv sync
node dev/function-local.mjs 8123          # 打开 http://127.0.0.1:8123/
```

这个本地后端用内存表实现云端 Function 的同一份路由代码（`functions/routes.mjs`），
所以页面行为与线上一致；它不连云端，也不碰本机 seekdb。

## 站点这个应用由两半组成

| | 本机 Recap | 云端站点 |
| --- | --- | --- |
| 干什么 | 采痕迹、写 PowerContext、镜像进嵌入式 seekdb、混合检索与裸 SQL | 痕迹板：登录写入、按周组合周报、关键词 + 日期窗检索、快照留档 |
| 承重的是什么 | PowerContext 的 Source / Memory / Handoff + seekdb 的 `hybrid_search` | 站点自己的数据库（`entries`、`reports`）+ Function 里的确定性合成 |
| 数据从哪来 | 你自己的 git 与笔记 | `recap seed` 导出的 `web/seed.json`，登录后由页面「导入本机痕迹」写入 |
| 边界 | 需要本地 Server 与 seekdb 目录 | 检索是 1000 条上限的有界扫描，云端没有 embedding；混合检索的完整能力在本机那一半 |

写入一律走登录（Qoder 账号），未登录只能读；提交用 `Idempotency-Key`，重复点击不会产生第二条。
公开读、登录写的实际响应可以在站点上直接验证：未登录时提交按钮禁用，`POST /api/entry` 返回 `401 login_required`。

## 手动跑一遍自己的周期

```bash
cd qoder
uv sync
cp deploy/powercontext.env.example deploy/powercontext.env   # 替换 __TOKEN__，或直接 powercontext config init
uv run powercontext server run --env-file deploy/powercontext.env --role all

uv run recap doctor                                        # 握手、建 Scope、报告集合与物理表
uv run recap collect --since 2026-10-20 --until 2026-10-26 # 工作痕迹 → Source / Memory / Handoff
uv run recap index   --since 2026-10-20 --until 2026-10-26 # 从 PowerContext 重建 seekdb 镜像
uv run recap report  --since 2026-10-20 --until 2026-10-26 --archive
uv run recap search "混合检索 时间窗" --limit 5 --raw-sql   # 走向量 + 全文 + 时间窗
uv run recap snapshot --since 2026-10-20 --until 2026-10-26  # 导出 data/snapshot.json（一次运行的完整载荷）
uv run recap seed                                           # 导出 web/seed.json，云端痕迹板的导入源
```

输入源在 [`recap.config.json`](recap.config.json)：读哪些仓库（路径 + author）、读哪些
markdown 目录、数据落在哪里。**这一步必须换成你自己的仓库**，否则采集不到痕迹。
`deploy/powercontext.env`（含 token）已被 gitignore，仓库里只有 `.example`。

## 命令

| 命令 | 作用 | 关键实现 |
| --- | --- | --- |
| `recap doctor` | 检查 Server 可达、Scope 存在、seekdb 集合与物理表 | `src/recap/cli.py:49` |
| `recap collect` | git 提交与笔记 → Source 日志；`- [决定]` 行 → Atomic Memory；一周状态 → Handoff 边界 + commit + acknowledge | `src/recap/ingest.py:46` |
| `recap index` | 删掉镜像（保留 Recap 自己归档的周报）后从 PowerContext 全量重建 | `src/recap/indexer.py:152` |
| `recap report` | 确定性合成周报，窗口内进展 + 决定/约束 + 未决 + 下周 + 历史回溯 | `src/recap/report.py:24` |
| `recap search` | 混合检索；`--raw-sql` 打印那条同时带全文、时间窗、向量排序的语句 | `src/recap/store.py:167` |
| `recap snapshot` | 把一次真实运行导出成 `data/snapshot.json`，用于对照与留档 | `src/recap/web_export.py:53` |
| `recap seed` | 把同一批痕迹导出成云端痕迹板可导入的 `web/seed.json`（跳过流程记录，只留工作痕迹） | `src/recap/web_export.py:133` |

## 验收样本（这一条是可复核的）

仓库里留了三份同一次真实运行的产物，评审不需要读代码就能对数：

| 文件 | 内容 |
| --- | --- |
| [`samples/doctor.json`](samples/doctor.json) | `recap doctor` 的真实握手：Scope `scp_46d9ve5qr8y5t29g2b4sdtk7hf`、Memory 7 条、Source 97 条、seekdb 集合 `recap_docs` / 物理表 `c$v2$1f1390c0d13a11f19c1ccaaafa3a2043` / docs 54 |
| [`samples/report-2026-W44.md`](samples/report-2026-W44.md) | `recap report` 合成的周报，进展 12 条 / 决定与约束 / 未决 / 下周，每条都带 `source/…`、`atomic-memory/…@rev` 引用 |
| [`samples/search-hybrid.txt`](samples/search-hybrid.txt) | `recap search --raw-sql` 的命中与那条"一条语句三个条件"的裸 SQL（全文 `MATCH … AGAINST` + 时间窗 + 向量距离排序） |

```
week            2026-W44            窗口 2026-10-20 → 2026-10-26
scope           scp_46d9ve5qr8y5t29g2b4sdtk7hf
index           collection recap_docs，docs 54，物理表 c$v2$1f1390c0d13a11f19c1ccaaafa3a2043
seed            web/seed.json：同一批镜像里筛出的 19 条工作痕迹（跳过 35 条流程记录）
```

云端那一半的对照数据在页面上现取：`GET /functions/v1/app/api/weeks`、
`GET /api/report?week=2026-W44`、`GET /api/entries?q=seekdb&from=…&to=…`，
返回值里的 `matched / scanned / truncated` 会如实告诉你这次扫了多少条。

复核路径：`uv run recap doctor` 会打印同一个 Scope 与文档数；`uv run pytest -q` 会跑
101 个 Python 用例（含 SQL 输入白名单的注入用例）；`node --test tests/` 跑 16 个 Function
路由用例；`scripts/demo.sh` 在全新目录里重跑整条链路。

## 测试

```bash
uv run pytest -q                    # 101 passed
node --test                         # 16 passed（Function 路由）
```

不需要 Server 与 seekdb 实例：`tests/test_ingest.py`、`tests/test_report.py`、
`tests/test_indexer.py` 用假 PowerContext / 假索引驱动真实的编排与合成代码，
`tests/test_store.py` 钉住裸 SQL 的输入白名单，`tests/test_traces.py` 钉住 git 窗口与
`\x1f` 定位切分，`tests/test_pctx.py` 钉住 Source id 的内容指纹规则，
`tests/test_web_export.py` 钉住 `seed_items()` 的痕迹映射（流程记录与 JSON 信封不进板）。

云端路由用同一份 `functions/routes.mjs` 加注入的假 `db` 跑：登录门槛、幂等重放、
按日期推导周、引用格式白名单、空周窗口、有界扫描都在 `tests/function_routes.test.mjs` 里。

## 已知边界

- **只有一周真实数据。** 采集器读的是本机 2026-10-20 之后的提交，所以"历史回溯"这一节
  为空是事实而不是缺陷；再跑两个自然周它就会长内容。
- **幂等但不越界。** 同一周重复 `collect` 不新增 Memory、不推进 Handoff revision
  （`handoff/handoff@8` 稳定），这是测试锁住的行为；未提交的本机草稿仍在
  Handoff 的 omissions 里被如实报告。
- **不调用生成模型。** 周报是查表 + 排序 + 去重的结果，可读性换确定性，句子可能略干。
- **`recap search` 的裸 SQL 走白名单转义**，因为 `pylibseekdb` 的 cursor 不支持绑定参数；
  不在白名单内的输入直接抛错，不做静默清洗。
- **改过的笔记会在日志里留下旧版本。** Source 内容不可变，所以重采一篇改过的笔记会追加新版本；
  镜像按痕迹本身（提交 sha / 笔记标题）只保留最新一版，旧改写不再进检索结果。
- **云端检索是有界扫描。** Function 只能按 `select / eq / order / limit` 过滤，站点数据库也不支持
  全文索引与向量列，所以 `GET /api/entries` 取最近 1000 条在内存里比关键词与日期窗，并把
  `matched / scanned / truncated` 三个数一起返回——扫到上限会明确告诉你，不假装是全量。
  真正的混合检索（向量 + 全文 + 时间窗一条语句）在本机那一半，`recap search --raw-sql` 可复现。
- **写入只认登录身份。** `POST /api/entry`、`/api/import`、`/api/report` 都要 Qoder 登录态，
  未登录一律 `401 login_required`；站点公开的是读。云端库的内容等于你导入 + 提交的东西，
  新部署的库是空的，登录后点「导入本机痕迹」才会把 `web/seed.json` 的 19 条真痕迹灌进去
  （按 `cite` 幂等，重复点不会多出第二条）。
- **周由日期推导，不信客户端。** 提交的 `day` 决定它落在哪个周，`week` 字段填错也不会把痕迹
  分到别的周——这是 `a declared week never overrides the week the day belongs to` 那条用例。

## 目录

```
src/recap/        采集(ingest/traces) → 存储(pctx/store/indexer) → 合成(report) → 导出(web_export/cli)
functions/        云端 Function：routes.mjs（全部业务逻辑）+ index.ts（Deno 入口，注入平台后端）
web/              网站（index.html + app.js + styles.css + seed.json，seed.json 是真实运行导出）
dev/              function-local.mjs：用内存表在本地跑同一份路由，页面行为与线上一致
tests/            101 个 Python 离线用例 + 16 个 Function 路由用例
samples/          验收样本：一次真实运行的 report / doctor / search 输出
notes/            本周的开发记录，同时是 Recap 的输入
deploy/           PowerContext Server 配置模板（真实 env 已 gitignore）
docs/             架构、技术分工、Qoder 开发记录
scripts/demo.sh   评审一键复现
data/             运行产物（周报 markdown、seekdb 目录、state.json、snapshot.json），已 gitignore
```

站点地址：<https://recap-weekly-report-mpe7vqhm9ob.qoder.zone>（当前账号的动态版本）；
旧账号的静态前身 <https://recap-weekly-report-ov0o0n50g0i.qoder.zone> 仍然在线，保留作为对照。
