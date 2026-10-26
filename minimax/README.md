# 规有所依：MiniMax SDD 工程上下文

面向订单查询的可运行 SDD 样例：从 seekdb 检索有效规格、待改代码和测试，使用 PowerContext 复用任务状态，再由 MiniMax 修改代码。交付包含真实模型请求与响应、修改前后快照、差异、数据库查询、进程重启与测试记录。

本次真实调用模型为 **MiniMax-M3**。第一次生成通过 13/14 项目标测试；将列表输入的错误反馈给模型后，第二次生成通过 **14/14 项功能测试、10/10 项回归测试**。修改前同一组功能测试有 13 项断言失败，回归测试全部通过。上下文模块另有 4 项真实 seekdb 集成测试。

## 环境与初始化

需要 Python 3.12、Git、Docker Compose 和 uv。所有依赖锁在 `requirements-lock.txt`；PowerContext 来自官方固定提交 `dab240063bfcfa7917898dc44da4baadb3747bd0`，版本 `1.2.1.dev2+gdab240063`。seekdb 镜像固定为 `1.4.0-100000172026082615`。在本目录执行：

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements-lock.txt
docker compose up -d
cp .env.example .env
set -a
. ./.env
set +a
```

默认连接 `127.0.0.1:12883`。等待 seekdb 可连接后运行以下流程；若服务刚启动时报连接拒绝，查看 `docker compose logs seekdb` 后重试。也可以通过 `SEEKDB_HOST/PORT/USER/PASSWORD/DATABASE` 指向已有 seekdb。程序自动创建专用数据库及 `sdd_materials` 表。Compose 的无密码 root 仅用于本机绑定的演示数据库。

## 一次完整复现（不消耗模型额度）

```sh
.venv/bin/python run_demo.py prepare --run .runs/replay-001
.venv/bin/python run_demo.py verify --run .runs/replay-001 --recorded
.venv/bin/python -m pytest -q tests
```

`prepare` 从未改代码、原始规格、初始数据和空 PowerContext 状态开始；运行修改前测试，导入更新后的规格，再用独立进程读取状态。`verify --recorded` 应用已交付的真实 MiniMax 输出，执行原封不动的测试，再写入并从新进程读回验证结果。最后输出六项检查全部为 `true`。每次使用新的运行目录；证据位于该目录的 `evidence/`。

这是**已记录模型输出的复现**，不会声称再次调用了模型。原始实际调用证据在提交的 `evidence/` 中。评审无需模型重新生成完全相同的补丁。

## 发起一次新的真实 MiniMax 开发

```sh
# 私有文件只包含 Key，建议权限 600；不要把 Key 写入源码或版本库。
export MINIMAX_API_KEY_FILE=/absolute/private/path/minimax-key
.venv/bin/python run_demo.py prepare --run .runs/live-new
.venv/bin/python run_demo.py generate --run .runs/live-new
# 审阅 .runs/live-new/evidence/generated-orders.py 后：
.venv/bin/python run_demo.py verify --run .runs/live-new
```

也支持环境变量 `MINIMAX_API_KEY`。默认官方端点 `https://api.minimax.cn/anthropic`，模型 `MiniMax-M3`；可配置 `MINIMAX_BASE_URL`（仅官方 HTTPS 域名）与 `MINIMAX_MODEL`。不跟随认证请求重定向，不保存认证头，不保存模型私有思维链。模型只返回 `orders.py` 的完整替换内容，不执行模型提供的命令。

模型新生成的代码不保证总能通过测试。源码语法/结构检查后仍需审阅；若失败，保留本轮响应和日志，将失败摘要置于运行目录 `evidence/repair-feedback.json`，还原 `workspace/orders.py` 为 `workspace/before/orders.py`，再执行 `generate`。本次真实第二轮请求已包含第一轮失败摘要，见 `evidence/minimax-request.json`；第一轮原始记录在 `evidence/attempt-1/`。

## 验证样例与有效规格

业务入口：`orders.py:list_orders(orders, customer_id=None, status=None, limit=20)`。输入为虚构客户 alice/bob 的订单字典列表，数据在 `sample/data/orders.json`。

任务是增加状态筛选：支持 pending、paid、shipped、cancelled，并在规格更新后支持 refunded。非法状态（包含列表、数字、空串、大小写不匹配）即便空数据也须抛出 ValueError。状态与客户筛选按 AND 组合，然后按 ID 排序、最后限量。

必须保留：不传状态时的旧行为、缺少状态的历史订单、客户筛选、升序排序、默认上限 20、上限 0、非法上限报错，以及不修改输入数据。

| 必要来源 | 内容 |
| --- | --- |
| `specs/orders.md` | ORD-001 兼容行为、ORD-002 状态筛选、ORD-003 验证来源；更新后追加 ORD-004 refunded |
| `orders.py:list_orders` | 待修改的真实源文件 |
| `tests/test_orders_feature.py` | 14 个目标功能用例，包括更新规格的 refunded 用例 |
| `tests/test_orders_regression.py` | 10 个兼容性用例 |
| `tests/conftest.py`、`data/orders.json` | 测试夹具与初始数据 |

这些路径相对于每次运行的 `workspace/`。初始代码在 `sample/before/orders.py`；最终模型代码在 `snapshots/after/orders.py`；有效的最终规格在 `snapshots/specs/orders-v2.md`。`sample/specs/inventory.md` 是额外资料，用来证明查询输入影响结果。规格更新时重新导入，旧索引内容被替换；删除资料后重新导入会移除旧资料。

重置无需删除数据库：换一个新的 `--run` 路径，会自动创建独立项目 ID 和空 PowerContext 存储。不要复用其他项目的状态目录。已有运行目录不会被 `prepare` 覆盖。

## 上下文模块 CLI

以下命令可在 `prepare` 生成运行目录后独立调用。`--project` 使用 `.runs/replay-001/run.json` 中的实际值。

```sh
.venv/bin/python sdd_context.py import --root .runs/replay-001/workspace --project PROJECT_ID --state-dir .runs/replay-001/powercontext
.venv/bin/python sdd_context.py context --root .runs/replay-001/workspace --project PROJECT_ID --state-dir .runs/replay-001/powercontext --query 'orders list_orders status customer_id limit'
.venv/bin/python sdd_context.py resume --state-dir .runs/replay-001/powercontext
.venv/bin/python sdd_context.py save --state-dir .runs/replay-001/powercontext --state progress.json
```

`import` 输入工程目录，输出路径/类别/SHA-256；`context` 输入项目与查询，输出真实 SQL、参数、数据库版本、相关文件正文/行号/哈希、已完成/待办/验证结果与下一步；`save` 输入带递增 `sequence`、`task`、`completed`、`todo`、`validation` 的 JSON；`resume` 输出 PowerContext 中检索到的最新任务记录及引用。`--output` 可把结果同时写入 JSON 文件。

## 交付证据

| 验收项 | 证据 |
| --- | --- |
| 可运行及环境 | `requirements-lock.txt`、`compose.yaml`、`evidence/summary.json` |
| MiniMax 实际参与 | `evidence/attempt-1/`、`minimax-request.json`、`minimax-response.json`、`model-change.json` |
| 来源正确与规格更新 | `evidence/context-v1.json`、`context-v2.json`，含规格正文、哈希及真实 SQL |
| seekdb 实际参与 | `evidence/import-v1.json`、`import-v2.json`、上下文中的查询记录，以及 `context-tests.log` |
| 空状态与重启复用 | `evidence/state-empty.json`、`state-save-ready.json`、`context-for-minimax.json`、`state-save-after.json`、`context-resumed.json`；包含不同进程 PID、真实 Memory 引用及复用后的下一步 |
| 同测试先红后绿 | `evidence/before-feature.*`、`after-feature.*`、`before-regression.*`、`after-regression.*`，含命令、退出码、源码/测试哈希 |
| 修改前后差异 | `sample/before/orders.py`、`snapshots/after/orders.py`、`evidence/orders.diff` |

`evidence/summary.json` 是本地六项验收自检，不代表赛事平台判分。日志中的本机运行根路径导出为 `<RUN>`，不改变测试内容、断言、模型输出、文件哈希或结果。

## 范围和来源

此模块面向小型 Python 工程样例，使用 SQL 词项匹配，不宣称语义向量检索；查询最多取 30 个资料文件，最多处理 24 个英文词项，PowerContext 最多读取 50 个状态条目。样例为单任务串行工作流，不支持多写者并发。PowerContext 使用其官方 SQLite 后端保存状态，工程资料使用独立 seekdb；没有用本地 JSON 替代 PowerContext 的状态读写，JSON 只保存 scope 标识和可审查证据。

业务样例、模块及测试为本项目编写；最终业务改码由 MiniMax 实际生成。依赖 [PowerContext](https://github.com/oceanbase/powercontext/tree/dab240063bfcfa7917898dc44da4baadb3747bd0) 与 [seekdb](https://github.com/oceanbase/seekdb)，遵循其各自许可。接口依据 [MiniMax 官方 Anthropic 兼容文档](https://platform.minimax.cn/docs/api-reference/text-anthropic-api)。要求依据 [MiniMax 赛题](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qlqnoryzrs7gtqbw)。
