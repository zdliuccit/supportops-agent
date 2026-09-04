## Why

当前仓库只有产品基线，没有可运行的软件骨架，后续知识检索、工具诊断、工单、审批和评测都缺少稳定的身份、会话、运行状态、事件协议与持久化承载。需要先建立能够本地复现、独立测试并可向企业部署演进的平台基础，避免各能力重复定义生命周期和横切契约。

## What Changes

- 建立 Python 与 TypeScript 单仓库工程，统一开发、测试、格式化和构建入口。
- 提供 FastAPI 服务、Agent Worker 进程和 React 用户聊天端三个首期应用。
- 提供 PostgreSQL/pgvector、Redis 和本地 Docker Compose 运行环境。
- 建立租户、用户、会话、消息、Agent Run 和 Run Event 基础数据模型及迁移。
- 提供基于 JWT 的本地身份验证和服务端身份上下文，不接受客户端伪造租户或角色。
- 提供会话创建、消息发送、Run 查询、取消和 SSE 事件订阅 API。
- 使用 Redis 队列解耦 API 与 Worker，由确定性占位执行器验证 Run 生命周期和流式协议。
- 提供关联 ID、结构化日志、健康检查、就绪检查和基础运行指标字段。
- 提供后端自动化测试、前端类型检查与构建验证，以及本地启动文档。
- 明确不在本 change 中实现 LLM、LangGraph、知识库、业务工具、工单、人工接管或审批逻辑。

## Capabilities

### New Capabilities

- `platform-workspace`: 提供可复现的单仓库结构、配置、依赖、基础设施和统一验证入口。
- `identity-context`: 验证访问令牌并生成可信租户、用户、角色和追踪上下文。
- `conversation-runtime`: 创建和读取会话，持久化用户与助手消息，并执行租户和资源范围校验。
- `agent-run-lifecycle`: 为每条用户消息创建幂等 Run，通过队列交给 Worker，并支持查询、取消及确定性状态转换。
- `run-event-stream`: 持久化结构化 Run 事件并通过支持恢复的 SSE 协议发送给客户端。
- `foundation-observability`: 提供健康/就绪检查、关联 ID、结构化日志和不包含敏感内容的基础运行记录。
- `chat-client-shell`: 提供能够创建会话、发送消息、订阅 Run 事件并展示错误状态的最小 React 聊天端。

### Modified Capabilities

无。产品基线 change 尚未归档为主规格，本 change 直接遵循其企业产品范围、风险策略和发布门禁。

## Impact

- 新增 `apps/api`、`apps/agent_worker`、`apps/web_chat` 和 `packages/support_core`。
- 新增 Python、pnpm、Docker Compose、PostgreSQL/pgvector、Redis、Alembic 和前端构建配置。
- 新增 `/health/live`、`/health/ready`、`/v1/conversations`、`/v1/conversations/{id}`、`/v1/conversations/{id}/messages`、`/v1/runs/{id}`、`/v1/runs/{id}/cancel` 和 `/v1/runs/{id}/events` 接口。
- 新增数据库迁移及本地开发身份令牌生成方式。
- 后续知识、工具、Agent 编排和工单 change 将复用本 change 的身份、会话、Run、事件和追踪契约。
