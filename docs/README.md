# SupportOps Agent 实现文档

从 [整体架构](./00-architecture.md) 开始阅读，再查看 [模块状态](./status.md) 和 [运行链路](./02-runtime-flow.md)。

运行维护文档：[可观测性、告警与租约口径](./modules/observability/overview.md)、[发布、回滚与故障处理 Runbook](./modules/observability/operations-runbook.md)。Runbook 是操作依据，实际部署/恢复/演练状态以任务证据为准。

应用交付：[API/Worker/Web 构建、运行、单执行者迁移与 TLS/SSE 模板](../deploy/runtime/README.md)。模板尚未执行或完成生产验收。

Agent 记忆相关文档：

- [会话内短期记忆](./modules/agent-runtime/short-term-memory.md)
- [长期记忆技术方案](./modules/agent-runtime/long-term-memory.md)

文档中的状态标签含义：

- **已实现**：代码提供该能力；不代表已重新测试或完成生产验收，验证情况以任务证据为准。
- **基础已实现**：有可用基础，但仍缺少生产闭环或完整验收。
- **部分实现**：存在接口或受控占位实现，尚未连接完整业务系统。
- **未实现/规划中**：仅有需求、配置预留或设计方案。
