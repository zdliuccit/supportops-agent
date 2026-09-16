# SupportOps Agent 实现文档

从 [整体架构](./00-architecture.md) 开始阅读，再查看 [模块状态](./status.md) 和 [运行链路](./02-runtime-flow.md)。

Agent 记忆相关文档：

- [会话内短期记忆](./modules/agent-runtime/short-term-memory.md)
- [长期记忆技术方案](./modules/agent-runtime/long-term-memory.md)

文档中的状态标签含义：

- **已实现**：代码和测试已经提供该能力。
- **基础已实现**：有可用基础，但仍缺少生产闭环或完整验收。
- **部分实现**：存在接口或受控占位实现，尚未连接完整业务系统。
- **未实现/规划中**：仅有需求、配置预留或设计方案。
