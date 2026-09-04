## Context

项目当前没有应用代码。产品基线要求建设企业级智能技术支持与工单处置 Agent，但后续所有能力都依赖一组稳定的基础契约：可信身份、租户隔离、会话、消息、Run、事件、异步执行和追踪。本 change 建立这些契约，并用确定性占位执行器验证完整技术链路。

## Goals / Non-Goals

**Goals:**

- 提供本地一条命令可启动的 PostgreSQL/pgvector 与 Redis 基础设施。
- 提供独立 API、Worker 和 React Chat 应用，以及共享领域包。
- 建立可迁移的持久化模型、JWT 身份上下文和租户过滤。
- 建立幂等消息提交、异步 Run、可取消状态机和可恢复 SSE。
- 建立后续 Agent、RAG、工具和工单能力可复用的接口与测试基线。

**Non-Goals:**

- 不调用真实 LLM，不引入 LangGraph 图。
- 不实现知识检索、工具网关、工单、人工接管或审批。
- 不提供生产 OIDC、完整 RBAC/ABAC、Kubernetes 或多区域部署。
- 不将占位 Worker 的回复逻辑视为 Agent 能力。

## Decisions

### 1. 使用 uv Python workspace 与 pnpm workspace

Python workspace 包含 `apps/api`、`apps/agent_worker` 和 `packages/support_core`；前端位于 `apps/web_chat`。uv 和 pnpm 分别生成锁文件，根目录提供统一命令说明。

替代方案是单一 Python 包。它更简单，但会把 Web 进程、长任务 Worker 和共享领域模型耦合在一个部署单元中。

### 2. API 与 Worker 进程分离

API 负责认证、事务、查询和 SSE；Redis 列表承担基础 Run 队列；Worker 使用阻塞消费并通过数据库条件更新声明 Run。队列采用至少一次投递语义，数据库状态机和幂等约束提供最终保护。

替代方案是在 FastAPI `BackgroundTasks` 中执行。该方式无法跨进程恢复，API 重启会丢失任务，不适合作为企业基础。

### 3. PostgreSQL 是事实来源，Redis 不是关键状态唯一存储

会话、消息、Run 和 Run Event 全部持久化到 PostgreSQL。Redis 只用于唤醒 Worker 和后续短期协调；重复或丢失通知可以通过重新入队或扫描 `queued` Run 恢复。

### 4. 使用 SQLAlchemy 2 异步模型和 Alembic

生产运行使用 PostgreSQL `asyncpg`。测试允许使用 SQLite `aiosqlite` 验证领域与 API 行为，但数据库迁移需要额外在 PostgreSQL 上执行验证，以避免方言差异被掩盖。

### 5. Foundation 使用严格的本地 JWT，而不是伪造请求头

本地令牌使用 HS256、固定 issuer/audience 和短有效期，并要求 `sub`、`tenant_id`、`roles`。密钥来自环境变量。OIDC/JWKS 验证留给身份能力 change，但业务代码只依赖统一 Principal，不依赖具体令牌实现。

开发环境允许通过 CLI 生成令牌并在首次访问时引导创建本地租户/用户；该引导模式由显式配置控制，production 默认禁止。

### 6. Run 与事件采用显式状态和追加写模型

Run 状态由服务层执行允许的转换。事件使用 `(run_id, sequence)` 唯一约束追加写入，SSE ID 即序号。终态事件与 Run 状态在同一数据库事务提交，确保查询与流式结果一致。

### 7. SSE 使用授权 fetch 流而不是原生 EventSource

浏览器原生 EventSource 不能可靠设置 Authorization 头，Web Chat 使用 `fetch` 读取 `text/event-stream`，维护最后事件序号并解析 `id/event/data` 字段。

### 8. 关联 ID 是一等字段

中间件接受格式受限的 `X-Correlation-ID` 或生成 UUID。关联 ID 写入响应头、日志、消息、Run 和事件。消息正文、令牌和连接串不进入结构化日志。

## Risks / Trade-offs

- [Redis 入队发生在数据库提交之后，进程可能在两者之间退出] → 提供重新入队命令或 Worker 周期扫描 `queued` Run；后续可升级为 Outbox。
- [SQLite 测试无法覆盖所有 PostgreSQL 行为] → Compose 验证运行 Alembic 和 PostgreSQL 集成测试。
- [本地 HS256 不满足企业生产身份要求] → Principal 接口隔离令牌实现，production 配置禁止开发身份引导，后续替换为 OIDC/JWKS。
- [数据库轮询 SSE 在高并发下效率有限] → foundation 优先保证恢复语义；规模化阶段引入 Redis 通知或专用事件总线，同时保留数据库回放。
- [单 Worker 阻塞消费吞吐有限] → 消费设计保持无状态，可水平启动多个 Worker；后续按模型限额和队列长度扩展。

## Migration Plan

1. 安装锁定依赖并启动 PostgreSQL/Redis。
2. 执行 Alembic 升级创建基础数据结构。
3. 生成本地 JWT，启动 API 和 Worker。
4. 启动 Web Chat，验证创建会话、发送消息、SSE 和取消。
5. 运行后端检查、测试、前端检查及生产构建。

当前没有旧系统数据。回滚时停止新应用并回退最新迁移；不得删除与本 change 无关的 OpenSpec 产物。

## Open Questions

- 后续生产身份系统的 OIDC issuer、audience 和角色映射尚待目标企业确定。
- P0 后是否将 Redis 队列升级为 Outbox + 专用队列框架，应根据可靠性演练结果决定。
- API 与 Worker 镜像是否在后续 change 中统一基础镜像，取决于部署平台约束。
