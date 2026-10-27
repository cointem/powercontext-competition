# 验证记录 · 2026-10-27

| 验证 | 实际结果 | 边界 |
| --- | --- | --- |
| 规则单元测试 | 8 passed | 执行规则源码与生成的 Code 节点代码 |
| 本地 Dify + PowerContext | 20次工作流均 succeeded | 本地验证副本移除 LLM 分支；没有模拟 PowerContext |
| 本地 Dify 网页入口 | 实际点击“运行”得到开场与账本 | 规则版，没有模型演出 |
| Dify Cloud 导入 | 创建成功，Auto-Saved、Unpublished | 在用户已登录工作区操作 |
| Cloud 原生 LLM 节点 | SUCCESS，2.554秒，323 tokens | 单独提供虚构测试提示；不是云端全链路 |
| 云端全链路与发布 | 未执行 | 按用户选择，没有公开本机 PowerContext |

本地工作流运行详情和记忆响应见 `local-dify-runs.json`。其中 `recall` 响应包含真实 PreparedContext，`save` 响应包含 Memory 的 artifact ID、revision 和实际存档文本。

本地试玩：<http://localhost:13000/workflow/ifGUrSuCoPsZCFuE>。

完整交付 DSL 保留 Dify 的 IF/ELSE 与原生 LLM 节点。它与本地验收副本的差异仅为模型演出分支和模式字段的说明；银两、回合、条件判断及四个 PowerContext HTTP 调用的业务逻辑一致。

后续上线仍需部署有鉴权的 HTTPS PowerContext 1.4.0，设置 Cloud 的 `PC_URL`、`PC_TOKEN`，再在 Cloud 重跑完整样例并发布。不要把“单节点成功”或“本地规则验收通过”表述成云端全链路成功。
