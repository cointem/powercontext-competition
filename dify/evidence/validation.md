# 实测记录

日期：2026-10-26。设备：macOS arm64，Docker Desktop Linux arm64。

- Dify：官方 1.18.0 API、worker、web，sandbox 0.2.15；DSL 0.7.0 导入状态 `completed`。
- seekdb：官方 `oceanbase/seekdb:1.4.0-100000172026082615`。
- 配套服务：Python 3.12.14，依赖版本见 requirements.txt。
- 执行交付目录的 `HOTEL_PORT=18090 docker compose up -d --build`，从空数据卷启动成功。
- 在真实 Dify draft/run 入口依次运行八次工作流：入住、捣乱、回忆、道歉、帮忙、探索、探索、赴约；全部成功，最终获得时光守护者结局。每次调用均经过 Dify Code 节点与 HTTP 节点。
- seekdb 中玩家版本为 8，八条操作记录与顺序一致。重启 hotel 容器后读取同一玩家，仍返回版本 8、结局状态及最近三条历史。
- 六项真实 seekdb 集成测试通过：两种结局与重开、跨会话记忆、幂等重试、并发版本冲突、奖励约束、访问控制与规则输出。原始输出见 pytest.txt。

`workflow-runs.json` 保留工作流 ID、节点结果、回复、耗时、SQL 查询摘要以及重启验证结果；凭据不在记录中。复现时导入 time-hotel.yml，按 README 的八步样例运行；新环境会生成自己的运行 ID。

本次实测使用无 Key 的规则剧情模式。可选模型叙事未调用，不作为已验证能力。
