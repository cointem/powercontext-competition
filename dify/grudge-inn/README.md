# 这家客栈会记仇

**赛事提测从 [SUBMISSION.md](SUBMISSION.md) 开始。已实测入口为 `grudge-inn-rules.yml`，不依赖模型；`grudge-inn.yml` 保留可选 AI 分支。**

六次行动，想办法在打烊前留下过夜。掌柜会记得你冒充食评家，跑堂会追讨二十文，也会记住你帮过忙，介绍神秘客为你担保。

**游戏核心是 Dify 原生 Workflow。** 规则、持久化记忆和结局都在 Dify 内完成，HTTP Request 节点直连官方 PowerContext Server。新增的网页提供一屏游戏界面，轻量代理只负责隐藏 Dify 应用密钥和转发请求，不实现游戏规则或记忆存储；原生 Dify 运行页仍可单独使用。

## 打开作品

- 本机游戏网页：[雨夜客栈](http://localhost:18093/)。状态、场景、对话和行动在一个视口内；宽窗口侧边显示旧账，窄窗口点击右上角展开。
- 用户 Dify Cloud 草稿：[这家客栈会记仇](https://cloud.dify.ai/app/c0a686d0-9d85-4510-a6b8-8745e47c04db/workflow)。需要当前工作区权限；尚未发布。
- 可移植交付：`grudge-inn-rules.yml`（已实测规则版）或 `grudge-inn.yml`（可选 AI 版），在 Dify Studio 中选择 Import DSL file；二选一导入为独立应用。
- 验证环境：Dify 1.18.0、DSL 0.7.0、PowerContext 1.4.0、Python 3.12。

当前云端草稿已导入并绑定工作区的 gpt-4o-mini；NPC LLM 节点已单独实测。尚未给本地 PowerContext 开公网入口，因此**不能声称云端完整流程已经打通**。本地规则流程与真实 PowerContext 已通过20次工作流运行；当地 Dify 缺少插件服务，验收脚本移除了本地验证副本的 LLM 分支，完整交付 DSL 保留该分支。

## Dify 中的流程

```text
开始：旅客口令、存档号、行动、可选台词
 → Code：生成独立 Scope 标识
 → HTTP：PowerContext 创建或取得 Scope
 → Code：准备读取请求
 → HTTP：读取当前存档的记忆清单
 → HTTP：PreparedContext 召回 NPC 旧账
 → Code：裁定规则、金钱、关系、六回合与结局
 → HTTP：保存行动到 Memory（观察和回忆仅执行只读请求）
 → Code：核验写入结果，整理账本与模型提示
 → IF/ELSE：规则试玩直接输出 / AI演出调用原生 LLM
 → Code：合并模型对白和权威账本
 → 输出
```

PowerContext 承担全部持久化，不使用本地文件或数据库冒充指定技术。存档写成 `inn_save_v1` 类型的原子记忆；每次有效行动保留一个快照，一局最多六个。`/v1/context/prepare` 的真实召回结果与账本事件交叉核对，召回到“帮跑堂”后才能获得神秘客推荐。移除或中断 PowerContext 会阻止恢复和推进游戏。

模型只写 NPC 对白，不能修改账本。玩家的自由台词仅在 AI 演出模式使用；行动必须从列表选择。规则试玩是无需模型额度的验收模式，不把固定台词当作模型生成。

## 本地部署与导入

1. 安装官方 PowerContext 1.4.0（需要 Python 3.11+ 和 uv）：

   ```sh
   uv tool install "powercontext[cli,server]==1.4.0"
   python3 start_memory.py
   ```

   服务监听 `127.0.0.1:18092`，数据与随机鉴权令牌位于本目录的 `.data/`，该目录已被 Git 忽略。服务默认使用自己的 SQLite 存储，不需要模型、Embedding 或 Rerank。

2. 使用已有本地 Dify，或按 [Dify 官方部署说明](https://docs.dify.ai/en/self-host/quick-start/docker-compose)部署完整实例。导入 `grudge-inn.yml`。本机 Docker 中的 Dify 可把 `PC_URL` 设为 `http://host.docker.internal:18092`；其他网络布局填写真正可访问的服务地址。Linux Docker 可能需要 `host.docker.internal:host-gateway` 映射。

3. Dify 的 Environment Variables 中配置：

   | 变量 | 类型 | 内容 |
   | --- | --- | --- |
   | PC_URL | String | 可访问的 PowerContext 1.4.0 地址，无末尾斜杠 |
   | PC_TOKEN | Secret | `.data/runtime.json` 的 `token` 字段 |

   Cloud 无法访问 localhost 或 host.docker.internal。云端上线须另行部署有鉴权的 HTTPS PowerContext 服务，再替换这两个变量。本次未建立隧道、未公开本地服务。

4. 点击“NPC 即兴演出”模型节点，选工作区已可用的聊天模型。已在用户 Cloud 工作区测试 gpt-4o-mini。重新导入 DSL 不包含模型密钥或免费额度，不保证目标工作区已有相同模型。完整本地 Dify 也需要插件服务与对应模型插件；不完整实例请使用下述本地规则验收脚本。

5. 点击 Test Run，旅客口令填8位以上随机字符（例如自己生成的 UUID），存档号填 `first`，行动选“进店看看”。每次运行是一回合，使用相同旅客口令和存档号续玩。新对话、新运行乃至服务重启都不影响已写入的记忆。

口令是演示用的存档访问凭据，Dify 工作区成员可以查看执行记录。此原型面向单人串行试玩，不是公共多租户服务。正式公开发布前须另行接入可信用户身份与 Scope 授权。

## 完整验收样例

每条路径使用新存档号。观察、回忆、无效或重复行动不耗回合，不发奖励。

| 步骤 | 输入行动 | 预期 |
| --- | --- | --- |
| 0 | 进店看看 | 0/6回合，10文、0欠款、0信誉 |
| 1 | 冒充食评家 | 1/6回合，信誉-2 |
| 2 | 借二十文 | 2/6回合，30文、欠款20 |
| 2a | 新一次运行：进店看看 | 跑堂认出借款人并追债；回合仍为2 |
| 3 | 帮跑堂 | 3/6回合，40文、信誉0；记下帮助 |
| 4 | 归还借款 | 4/6回合，20文、欠款0、信誉1 |
| 4a | 新一次运行：进店看看 | 记得借款历史，同时明确已经还清 |
| 5 | 坦白道歉 | 5/6回合，信誉2 |
| 6 | 找神秘客 | 召回帮助事件、拿到推荐，自动结算“贵宾入住” |

- “打工抵债”：新档依次执行“帮掌柜、申请入住”。
- “被赶出门”：新档依次执行“冒充食评家、借二十文、申请入住”。
- 不能刷钱：连续两次“帮跑堂”，第二次不改变金币、关系或回合。
- 不能重复扣款：同一请求编号、同一输入重试，重放已保存结果。
- 隔离：换旅客口令或存档号，初始欠款为0，不召回他人的故事。
- 重置：更换存档号即可获得完全空白的记忆；“重新开局”按钮会说明操作方法，不会删除原档。
- 结局后：只能回忆或换档，其他行动不会改变结局。

## 复现验证和修改

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python build_workflow.py
pytest -q tests
```

10项规则与输出测试覆盖结局、不能刷钱、推荐必须依赖真实召回、还款记忆、封盘以及DSL中的节点源码可以独立执行。

本地 Dify 真实流程验收（仅用于自己控制的本地实例）：

```sh
python validate_dify.py --credentials-file /path/to/local-dify-admin.json
```

凭据文件格式为 `{"email":"YOUR_LOCAL_EMAIL","password":"YOUR_LOCAL_PASSWORD"}`。脚本使用已有账户，不会注册或修改密码。默认连接 `http://127.0.0.1:15001/console/api`，在该实例建立并复用一个测试应用。它运行20次工作流并生成 `evidence/local-dify-runs.json`，保存节点状态、回复和真实记忆响应，不保存凭据。每次使用新的随机测试玩家，不改已有玩家。

主 DSL 修改后须重新导入或替换目标工作流。新增网页所用的 `ui_data` 结构化输出已经更新至本地发布版本；Cloud 草稿仍是先前版本，上线前需要同步最新 DSL，并重新确认模型与环境变量。

## 一屏网页

```sh
# 已有本地 Dify 的首次连接（先完成前述依赖安装）
python validate_dify.py --credentials-file /path/to/local-dify-admin.json --configure-web
python3 web/serve.py
# 打开 http://localhost:18093/
```

`--configure-web` 在20次真实流程验收通过后发布本地工作流，创建或复用该应用的 API 密钥。配置保存在 `.data/web-config.json`（0600，Git忽略），浏览器不会获得应用密钥。网页是无构建依赖的 HTML/CSS/JS；代理使用 Python 标准库（3.9+）。

- 页面直接消费 Dify 核验记忆写入后的 `ui_data`，不通过解析 Markdown 猜测数值。
- 每次有效行动后追加一次不耗回合的真实召回，更新旧账与可选行动。
- 浏览器只保存随机旅客凭据、存档入口和待确认请求；权威状态在 PowerContext。
- 请求出错保留原编号，刷新与重试继续核验同一回合；按钮在处理中禁用。
- “我的旅程”可新建、切换存档。不会删除旧档。清除浏览器网站数据会丢失本机的续玩入口。
- `?playtest=1` 使用独立浏览器测试存档，避免自动验收操作影响玩家正在玩的那一局。
- `python3 web/validate_web.py` 验证真实发布 API、重试、续玩、隔离、跨站请求限制和静态路径限制。
- 本地版本为规则试玩；将 `INN_MODE` 配为 `AI演出` 并连接包含模型节点的已发布工作流时，网页显示可选台词和 AI 对白。

未来部署可在服务器环境中提供 `DIFY_API_BASE`（含 `/v1`）、`DIFY_API_KEY`、`INN_MODE`、`HOST`、`PORT`，通过 HTTPS 反向代理部署相同网页与转发服务；应用密钥必须保留在服务器端。公开多人服务需要增加用户身份、授权及共享并发控制。当前按要求只在本机监听，没有建立公网隧道或部署公开网站。

美术素材与内置 imagegen 最终提示词见 [web/ARTWORK.md](web/ARTWORK.md)。

## 运行边界

- PowerContext 1.4.0 使用 Atomic Memory。较早版的 Memory 响应结构不同，不可直接替换。
- 每个存档请一次运行一个行动。模型失败时，账本可能已经写入；用原请求编号、原输入重试可重放该回合。不要在未知结果时换编号重复行动。
- 同档并发不是本原型支持的模式。若检测到同回合的冲突快照，会拒绝继续，避免偷偷丢弃其中一条；换新档复测。
- 缺失、失败或被截断的召回不会伪造成角色记忆。召回不到帮助事件时，不解锁推荐。
- 一局只有六个有效行动，所有事件为原创固定场景，没有外部知识库或预置数据依赖。
- 没有提交模型密钥、PC令牌、用户登录态或真实个人信息。

接口参考：[PowerContext 快速开始](https://powercontext.oceanbase.io/zh/docs/develop/api-quickstart/)、[Dify HTTP Request](https://docs.dify.ai/en/cloud/use-dify/nodes/http-request)。工作流结构参考本地已有 Dify 1.18.0 原型和官方测试夹具，剧情、规则与记忆处理为本项目编写。
