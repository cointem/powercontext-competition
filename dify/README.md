# Dify 本次提测入口

当前参赛作品：**这家客栈会记仇**，材料位于 [`grudge-inn/`](grudge-inn/)。

- 首先阅读：[提测与复现说明](grudge-inn/SUBMISSION.md)。
- 已实测工作流：[grudge-inn-rules.yml](grudge-inn/grudge-inn-rules.yml)，Dify 1.18.0 + PowerContext 1.4.0，无模型依赖。
- 可选 AI 演出工作流：[grudge-inn.yml](grudge-inn/grudge-inn.yml)，模型节点单独实测，完整 AI 路径验证边界见说明。
- 一屏网页源码、配置示例、启动方式、完整样例和运行证据均在同目录。
- 验收主作品无需使用本机 localhost 或作者的 Cloud Studio 账号。

以下保留此前的“时空旅馆”示例说明；与本次客栈作品是两个独立应用，请按上述入口验收本次提交。

---

# 时空旅馆：阿零记得你

一款通过 Dify 运行的选择式互动剧情。NPC 会记住玩家的捣乱、道歉和帮助；选择改变信任值、道具、场景以及最终结局。使用相同旅客代号即可在新一次工作流运行中继续剧情。

默认玩法不需要模型 API Key。规则剧情由 Dify Code 节点执行，seekdb 保存状态与历史。可选模型叙事接口保留在配套服务中，本次交付与验证使用规则模式。

## 技术分工

- **Dify 1.18.0**：输入、读取记忆、剧情判断、保存结果、叙事输出的完整编排。`time-hotel.yml` 可直接导入，DSL 版本为 0.7.0，不依赖第三方插件。
- **seekdb 1.4.0**：`hotel_players` 保存玩家当前场景、信任、道具和轮次；`hotel_turns` 保存每次选择及回复，按玩家隔离。真实读取的状态和历史进入下一轮剧情判断。
- **配套 Python 服务**：提供记忆读写和事务控制。重复请求复用已保存结果，版本冲突返回 409，由用户重新执行本轮。

## 启动

需要 Docker Compose 和 Python 3（仅用于生成本地配置）。在本目录执行：

```sh
python3 init_env.py
docker compose up -d --build
curl http://127.0.0.1:18089/health
```

首次启动自动创建专用 seekdb 数据库和表，不依赖预置数据。健康检查应返回 seekdb 版本。此 Compose 启动应用后端及 seekdb；Dify 单独使用已有的 1.18.0 实例，或按照 [Dify 官方部署说明](https://docs.dify.ai/en/self-host/quick-start/docker-compose)部署。

在 Dify 中选择“导入 DSL 文件”，导入 `time-hotel.yml`，设置两个工作流环境变量：

| 变量 | 值 |
| --- | --- |
| GAME_URL | Dify 在本机 Docker 中时使用 `http://host.docker.internal:18089`；其他部署方式填写 Dify 能访问到的后端地址 |
| GAME_TOKEN | 本目录 `.env` 中生成的值，使用 Dify 的 secret 类型 |

Linux Docker 可为 Dify API 和 worker 配置 `extra_hosts: ["host.docker.internal:host-gateway"]`。如果 Dify 的网络策略限制访问本机后端，需按该实例的网络规则允许此应用服务。Dify Cloud 无法直接访问本机服务；本样例使用本地部署。

打开工作流的测试运行入口，填入旅客代号（字母、数字、下划线或短横线）、选择行动，即可游玩。请求编号通常留空，每次自动生成；复现同一请求时可以手动填写固定编号。发布后可使用 Dify 提供的应用页面或 Workflow API 作为入口。

## 验证样例

使用一个从未用过的代号，例如 `demo_alice_001`。按顺序每次执行一个行动：

| 次序 | 行动 | 预期表现 |
| --- | --- | --- |
| 1 | 入住 | 获得铜钥匙，信任为 0 |
| 2 | 捣乱 | 前台记住纸飞机事件，信任为 -2 |
| 3 | 回忆 | 新一次运行仍读出入住和捣乱历史 |
| 4 | 道歉 | 信任回升到 -1 |
| 5 | 帮忙 | 获得星灯，信任为 1 |
| 6 | 探索 | 进入档案室 |
| 7 | 探索 | 携星灯到达天台 |
| 8 | 赴约 | 获得“时光守护者”结局 |

对比样例：新旅客依次选择“入住、捣乱、帮忙、探索、探索、赴约”，因未修复信任，获得“独行旅客”结局。重复“帮忙”不会反复增加奖励。“重新开始”清空当前道具与信任、进入新轮次；历史仍保留。需要完全空白的测试条件时换一个旅客代号。

停止并重新启动后端后，使用相同代号选择“回忆”，应仍能读取最近三轮记录。另一个代号的状态从空白开始。输入相同请求编号与相同行动两次，版本只增加一次；同一编号用于不同行动会报冲突。

## 开发和测试

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python build_workflow.py
# 指向可通过 MySQL 协议连接的 seekdb 测试实例
SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=12881 SEEKDB_DATABASE=dify_time_hotel_test pytest -q
```

测试会创建独立测试库，并清理自己创建的测试玩家，不删除数据库。`rules.py` 是 Dify Code 节点的源码，修改后用 `build_workflow.py` 重新生成 DSL。测试中的状态读写使用真实 seekdb，不使用内存数据库替代。

`evidence/` 提供实际 Dify 运行摘要及数据库结果；复现方法和运行边界见 `evidence/validation.md`。

## 可选模型叙事

默认不配置。需要时，在后端 `.env` 添加兼容 Chat Completions 的 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，然后重新创建 hotel 容器。模型只重述剧情，游戏状态仍由 Dify Code 节点决定；界面输出 `mode` 标识实际使用的叙事方式。此可选路径未作真实模型调用验证，不计入本次验证结果。

本应用面向本地试玩，旅客代号用于继续剧情，不是身份认证。配套服务源码、剧情、工作流和样例均为本项目编写。Dify、seekdb 使用官方镜像及各自许可。
