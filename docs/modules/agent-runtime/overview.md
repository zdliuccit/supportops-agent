# Agent 运行时

## 当前实现

运行时由 `AgentFactory`、`ModelAdapter`、Tool Registry、结构化输出策略和 PostgreSQL checkpoint 组成。Worker 只执行已经发布并固定版本的配置。

## 执行步骤

1. 校验运行预算和模型能力。
2. 创建固定模型客户端并执行脱敏策略。
3. 根据 Agent 版本、租户和用户角色过滤工具。
4. 通过 `create_agent` 构造图，注入 `SupportContext`。
5. 使用 `ToolStrategy(SupportAnswer)` 或受支持的 ProviderStrategy 校验输出。
6. 写入产品消息、checkpoint、Observation 和 Run 终态。

## 运行限制

默认模型调用上限为 6 次、工具调用上限为 8 次；同时受总超时、最大并行工具数、重试、token/cost 和平台安全中间件约束。

## 代码入口

- `packages/support_core/src/supportops_core/agent_runtime.py`
- `packages/support_core/src/supportops_core/worker_runtime.py`
