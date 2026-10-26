# PowerContext 2026 参赛实现

参赛仓库：<https://github.com/cointem/powercontext-competition>。实现分支为 `2026-competition`。
保留官方提交历史，包含冻结基线 `9db1753434162c0aa7c9f1189a1722e617880a52`。
实现基于官方 `2026-competition` 分支提交 `dab240063bfcfa7917898dc44da4baadb3747bd0`。

## 实现入口

| 题目 | 产品代码入口 | 行为 |
| --- | --- | --- |
| 一：仓库准备 | Git 历史 | 保留冻结基线，默认分支包含实现；私有仓库须在赛事平台授予读取权限 |
| 二：CSV | `src/powercontext/sources/csv_rows.py` | 完整校验后解析，行级身份、原始字段及回查 |
| 三：Pi 会话 | `src/powercontext/sources/pi_session.py` | 校验事件树，采集选中分支，持久化 checkpoint，恢复与冲突检测 |
| 四：PDF | `src/powercontext/sources/pdf_pages.py` | 全部物理页解析，文档摘要和页级引用，通过作用域 Memory 公共接口获取候选并排序 |
| 五：HTTP 重排 | `src/powercontext/builtin/artifacts/memory/http_reranker.py` | 跨批次全局 rank、严格响应校验及稳定排序 |
| 六：增量上下文 | `src/powercontext/builtin/runtime/context_pack.py` | UTF-8 分块、摘要与字节数验证、公共前缀差量和完整重建 |
| 七：数量触发 | `src/powercontext/builtin/runtime/artifact_processing.py` | 按各处理族已提交游标计算新增数量，复用持久化调度与重试 |
| 八：Embedding | `src/powercontext/builtin/inference/coalescing.py` | 并发去重、合并批次、成功向量 LRU、取消隔离及失败重试 |
| 九：来源生命周期 | `src/powercontext/builtin/runtime/source_lifecycle.py` | 持久化依赖图和来源事件，停用与恢复原记忆，保留历史审计引用 |
| 十：证据选择 | `src/powercontext/builtin/runtime/evidence_fragments.py` | 查询相关原文片段，精确版本及字符偏移引用，包含完整外壳的字节预算 |
| 十一：混合检索 | `src/powercontext/builtin/persistence/oceanbase/memory_index.py` | seekdb 单 SQL 获取全文及向量两路候选，保留现有融合与过滤 |

证据选择通过 `PrepareContextRequest(evidence_selection=True)` 显式启用，HTTP `/v1/context/prepare` 也支持同名字段，默认关闭。
PDF 解析依赖可选 extra：

```bash
uv sync --extra pdf
```

## 验证与复现

2026-10-26 使用官方固定依赖 Docker 环境执行全部公开代码题测试：**95 passed**。
宿主为 macOS ARM64，容器运行 Linux x86_64；跨架构执行时间不用于声明比赛性能得分。
seekdb 混合检索使用真实嵌入式数据库，公开测试校验查询结果和原生索引执行计划。

| 公开测试 | 通过数 |
| --- | ---: |
| CSV | 17 |
| Pi 会话 | 15 |
| PDF | 6 |
| HTTP 重排 | 10 |
| 增量上下文 | 14 |
| 数量触发 | 17 |
| Embedding | 8 |
| 来源生命周期 | 4 |
| 证据选择 | 3 |
| 混合检索 | 1 |

```bash
make -C competition docker-build
make -C competition docker-test
# 已按 competition/README.md 安装本机自测环境后：
make -C competition check-baseline
```

产品回归测试覆盖上下文构造、调度配置、调度执行及扫描回滚，共 133 项通过。
另有来源撤回中途写入失败、关闭数据库后重启重放的回归测试，验证恢复完成、原版本不变及重复事件不新增记忆修订。

```bash
uv run pytest -q tests/builtin/runtime/test_prepared_context.py \
  tests/builtin/runtime/test_artifact_processing_config.py \
  tests/builtin/runtime/test_processing_scheduler.py \
  tests/builtin/runtime/test_processing_scan_rollback.py \
  tests/builtin/runtime/test_competition_lifecycle_recovery.py
make contract-test
uv lock --check
uv run ty check --exclude competition
```

`competition/` 的题面、测试、数据及依赖保持官方原样。仓库全量 `prek` 检查会报告该目录已有的类型诊断及 JSON 格式差异；产品代码独立通过类型和 Ruff 检查。

公开测试结果不代表隐藏质量、性能验收或最终成绩。PDF 检索采用本地词法评分，每次查询读取当前作用域页级记忆；证据选择采用词项权重和原文句子预算选择，没有在线模型调用。是否满足隐藏数据的质量阈值，以赛事验收为准。

## 提测

提交仓库 HTTPS URL、分支 `2026-competition` 和 `git rev-parse HEAD` 输出的完整 commit。
私有仓库必须先按赛事平台流程授予读取权限。本地基线检查无法代替平台权限检查；本实现不含平台授权凭据，也不自动上报成绩。

实现与测试由 Codex 辅助完成。
