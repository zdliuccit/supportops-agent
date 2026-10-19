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

## 未启用的候选契约与设计

W25.2新增 `memory_contracts.py`，完成长期记忆政策/修订、本人消息来源、确认、期限、撤销与删除投影的字段设计及纯绑定校验，完整说明见[长期记忆方案第22节](./long-term-memory.md#22-w252-字段与生命周期契约2026-10-19)。批准/执行/召回恒false，运行配置long_term_policy_id仍None，没有存储、提取、确认服务或召回接线；25.1业务批准及25.3以后保持未完成。

W26.2新增 `specialist_task_contracts.py`，定义严格不可变的子任务输入/输出/证据、父子Agent/模型/发布清单版本、父Run/attempt/generation和Trace关联。仅包含有界问题、服务端只读资源投影、固定证据与期限、父预算范围声明；不传整个父对话或秘密。结果核对原输入摘要/版本/证据及期望事实键，未知用量保留null；纯函数汇总按任务ID排序保留失败/取消/缺失，并标记相同资源/事实键的显式不同值，不投票选择结论。全部完成也仅供父级复核。

当前execution/write/预算预留许可均为false，不调用子Agent、创建Span、访问数据库或消费预算；声明自洽不等于当前权限有效。26.1启动证据、26.3隔离执行/共享计量及26.4调度/循环限制仍未实现。绑定/字段/边界详见[设计第61节](../../../openspec/changes/complete-supportops-production-roadmap/design.md#61-w262-子任务输入输出证据与失败汇总契约)。

W24.2的[设计第62节](../../../openspec/changes/complete-supportops-production-roadmap/design.md#62-w242-版本化模型路由策略与-agent-配置升级设计)定义未来AgentConfigV3判别联合、不可变策略与精确候选解析、最新探测/当前凭据revision校验及发布清单升级。当前AgentConfigV2、空fallback和固定模型执行保持不变；没有实现v3解析、动态路由或故障切换，也没有新增启用批准。上述交付均未运行测试、类型检查或构建。
