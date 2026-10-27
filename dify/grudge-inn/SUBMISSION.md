# Dify 提测说明：这家客栈会记仇

作品目录：`dify/grudge-inn/`。基于官方 `2026-competition-ecosystem` 分支开发。

## 本次验收入口

六次行动，想办法在雨夜客栈留下过夜。掌柜记住谎言，跑堂记住借款与援手，神秘客根据真实召回的人情决定是否推荐。Dify 负责规则和全部流程，PowerContext 负责持久化与真实记忆召回；网页只呈现结果。

优先导入 **`grudge-inn-rules.yml`**，这是已完整实测的规则试玩工作流，不需要模型额度或第三方插件。只需导入此文件，没有前置 DSL 导入顺序。

**`grudge-inn.yml`** 是可选的 AI 演出工作流。它保留原生 LLM 分支，模板指向 `langgenius/openai/openai` 的 `gpt-4o-mini`；导入后须在自己的工作区安装模型提供商插件、配置模型并重新选择 LLM 节点模型。此分支的 Cloud LLM 节点已单独验证，但最新完整 AI 流程未作端到端实测，不作为当前已验证能力申报。两份 DSL 是二选一的独立应用，不要按先后顺序覆盖同一个应用。

## 最短复现路径

声明环境：macOS 主机、Docker Desktop、本地 Dify 1.18.0、DSL 0.7.0、PowerContext 1.4.0、Python 3.12。不使用 Embedding、Rerank 或外部知识库，无预置数据。

1. 准备 Dify 1.18.0，保留官方 Code 执行沙箱与 HTTP 节点能力。安装 PowerContext：`uv tool install "powercontext[cli,server]==1.4.0"`。
2. 在本目录执行 `python3 start_memory.py`。服务默认在主机 `127.0.0.1:18092`，首次自动生成独立空数据目录和随机鉴权令牌，不依赖作者旧数据。
3. 在 Dify Studio 导入 `grudge-inn-rules.yml`。设置环境变量 `PC_URL`（本机 Docker Desktop 示例 `http://host.docker.internal:18092`）及 Secret 类型的 `PC_TOKEN`（本机 `.data/runtime.json` 的 token）。服务必须能被 Dify API / worker 容器访问；其他网络环境应调整服务监听与容器路由。Cloud 无法使用这个本机地址。
4. 在 Dify 测试运行中填写 8 位以上随机旅客口令、存档号 `first`，行动选“进店看看”，演出模式选“规则试玩”。后续沿用同一口令与存档号。每次运行是一轮交互，观察与回忆不耗行动。
5. 可选网页入口：发布上述工作流，在 Dify 的“访问 API”中创建应用 API 密钥，按 `web/config.example.json` 将配置填入本机 `.data/web-config.json`（权限设为 0600），执行 `python3 web/serve.py`，打开 `http://localhost:18093/`。配置中的 `api_base` 要填当前 Dify 的服务 API 地址，末尾含 `/v1`。网页服务不包含游戏规则。

已有自己控制的本地 Dify 时，也可以安装 `requirements-dev.txt`，执行 `python validate_dify.py --credentials-file /path/to/local-admin.json --configure-web` 自动导入、验收、发布和生成本地网页配置；凭据格式及完整步骤见 README。这不是评审必须使用的入口，不需要作者账户或任何真实密钥。

## 验证样例

初始空档：10 文、0 欠款、0 信誉、0/6 行动。

| 顺序 | 行动 | 预期 |
| --- | --- | --- |
| 1 | 冒充食评家 | 信誉 -2，掌柜记下谎言 |
| 2 | 借二十文 | 30 文、欠款 20 文 |
| 2a | 新一次运行 / 刷新后进店看看 | 欠款保持 20，跑堂提到上次借款；不耗行动 |
| 3 | 帮跑堂 | 40 文、信誉 0，形成援手记忆 |
| 4 | 归还借款 | 20 文、欠款 0、信誉 1 |
| 5 | 坦白道歉 | 信誉 2 |
| 6 | 找神秘客 | 真实召回援手，推荐后自动结算“贵宾入住” |

在 Dify 原生运行页帮助跑堂后，可先执行一次“回忆旧账”刷新召回和可选行动；网页会自动执行这次免费召回。另开存档验证“帮掌柜 → 申请入住”为打工抵债，“冒充食评家 → 借二十文 → 申请入住”为被赶出门。换存档号或在网页选择“我的旅程 → 开启新的雨夜”即可恢复空白状态，旧档保留。

## 材料与证据

| 材料 | 路径 |
| --- | --- |
| 已完整实测 DSL | `grudge-inn-rules.yml` |
| 可选 AI 分支 DSL | `grudge-inn.yml` |
| 完整运行说明与已知限制 | `README.md` |
| 工作流源码及生成器 | `rules.py`、`workflow_nodes.py`、`build_workflow.py` |
| PowerContext 初始化 / 启动 | `start_memory.py` |
| 网页源码、代理与配置示例 | `web/` |
| 真实 Dify 与 PowerContext 记录 | `evidence/local-dify-runs.json`（20 次） |
| 真实网页 API 验证 | `evidence/web-api-runs.json`（7 次） |
| Cloud 模型节点单独测试 | `evidence/cloud-model-test.json` |
| 实机截图 | `evidence/web-desktop.png`、`evidence/web-narrow.png` |
| 回归测试 | `tests/test_game.py`（10 项通过） |

证据含测试生成的虚构游戏数据，不包含模型密钥、PowerContext 令牌或用户登录凭据。固定规则台词不冒充模型生成。所有权威状态保存在 PowerContext；移除记忆服务会阻止恢复和推进。

## 平台填写

- 赛题：Dify × OceanBase / Just for Fun。
- 仓库：`https://github.com/cointem/powercontext-competition`（按参赛平台既有配置）。
- 分支：`2026-competition-ecosystem`。
- commit：使用本次成功推送后返回的完整 SHA，不使用作者本地未推送的 SHA。
- 入口说明：`dify/grudge-inn/SUBMISSION.md`；验收 DSL：`dify/grudge-inn/grudge-inn-rules.yml`。
- 不把 localhost 试玩地址或 Dify Studio 草稿链接当成评审的可访问入口。题目允许评审按材料部署复现，并未要求公网演示地址。

官方要求：[Dify 赛题](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/lhfslihtnnb8yqh6)、[GitHub 使用说明](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qs1povt8zbza6hts)。官方指南要求私有参赛仓库；本次按参赛者明确指定使用上述现有仓库，其可见性未由本提交修改。
