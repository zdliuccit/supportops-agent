# AGENTS.md — SupportOps Agent 项目工作指南

本文适用于整个仓库，供 AI 编码助手和项目贡献者建立实现上下文。`apps/api/AGENTS.md`、`apps/web_chat/AGENTS.md` 补充各自目录的约定；修改相应目录前应阅读这些文件，并按指令优先级与作用域执行。

实现快照日期：**2026-10-12**。以当前代码为事实来源，包含当前工作区已有的未提交实现；这里的“已实现”表示存在实现，不表示本次文档更新重新运行了全部测试，也不表示已经通过生产验收。

## 1. 产品定位与当前阶段

SupportOps Agent 面向企业员工、客服和技术支持，目标是完成“理解问题 → 获取知识与业务证据 → 诊断 → 回答或升级 → 受控业务动作 → 结果复核”的支持闭环。

当前已形成 **企业 Agent 控制面 + 员工聊天工作台 + 单 Agent 异步执行底座**，具备知识生命周期管理和运行观测。真实知识检索、诊断编排、外部工单、MCP 执行网关、人工审批与业务动作复核尚未接入主链路。

- 当前部署边界：**3 个应用 + 1 个共享 Python 核心包**，属于 API 与 Worker 分进程的模块化单体。
- 本文按职责划分 **12 个逻辑能力模块（M01–M12）**。模块不等于目录、微服务或独立 Agent；共享文件可以承载多个相关模块。
- `models.py` 当前定义 **29 张业务 ORM 表**；LangGraph 在独立 checkpoint schema 中管理的表不计入这 29 张表。
- [2026 修订参考架构图](architecture-diagrams/enterprise-support-agent-architecture-2026-10-12.png) 是目标设计，不能当作当前功能完成清单。

## 2. 技术栈、目录与依赖方向

| 单元 | 技术与职责 | 主要入口 |
| --- | --- | --- |
| `apps/web_chat` | React 19、TypeScript、Vite、React Router、Tailwind、shadcn/Radix、Redux Toolkit、Recharts | `src/router/index.tsx`、`src/api.ts`、`src/types.ts` |
| `apps/api` | FastAPI、Pydantic、JWT 身份依赖、REST、Fetch SSE 所需事件接口 | `src/supportops_api/main.py`、`routes/` |
| `apps/agent_worker` | Redis 消费、Agent Run 执行调度、服务心跳、恢复扫描与周期清理 | `src/supportops_worker/main.py` |
| `packages/support_core` | 身份、配置、ORM、领域服务、模型适配、Agent 与 Worker 执行实现 | `src/supportops_core/` |
| `migrations` | Alembic 业务结构与数据迁移、字段注释、软删除迁移 | `versions/`、`env.py` |
| `deploy` | 本地 PostgreSQL/pgvector、Redis 的 Docker Compose | `docker-compose.yml` |
| `tests` | API、Worker、真实 PostgreSQL 集成用例 | `api/`、`worker/`、`postgres/` |
| `openspec` | 产品基线、风险政策、场景、评测口径和变更规格 | `specs/`、`changes/` |
| `docs` | 实现架构、运行链路、数据模型、接口和模块说明 | `README.md`、`00-architecture.md`、`status.md` |
| `architecture-diagrams` | 目标架构图，必须与实际实现状态区分 | `enterprise-support-agent-architecture-2026-10-12.png` |

Python 使用 **3.12+ 与 uv workspace**；前端使用 **pnpm@11.10.0**，本地 Node.js 使用 README 约定的 22+。准确依赖版本以 `uv.lock`、`pnpm-lock.yaml` 为准，不根据文档自行升级依赖。

依赖方向：Web → HTTP API；API/Worker → `supportops_core`；核心包不反向依赖 Web 或 API 应用。API 处理控制面、产品数据和模型端点探测；聊天 Agent 推理由 Worker 执行。

```text
Web 管理台 / 聊天工作台
          │ JWT + REST / Fetch SSE
          ▼
FastAPI API ───────── PostgreSQL：配置、身份、消息、Run、事件、审计
          │                               ▲
          │ Redis：Run ID 通知             │
          ▼                               │
Agent Worker ─────────────────────────────┘
          │ 固定 Agent/模型版本 + 本轮凭据 revision
          ▼
AgentFactory / create_agent
          ├── ModelAdapter → OpenAI 或兼容模型端点
          ├── 静态注册且按绑定/角色过滤的本地工具
          └── LangGraph PostgreSQL checkpoint

知识生命周期 API → 知识版本 / ACL / 发布快照 / 知识 Outbox
                    当前尚无消费索引与 Agent 检索链路
```

## 3. 十二个模块的实现明细

### M01 — Web 产品入口与交互工作台：已实现

- **入口**：`apps/web_chat/src/router/index.tsx`、`layouts/MainLayout.tsx`、`pages/`、`api.ts`、`types.ts`。
- **现有页面**：登录；授权 Agent 目录；Agent/模型列表与详情；工具目录；公司、用户、部门；知识列表与详情；Analytics 概览、排名、Agent 状态/详情、运行与错误；普通和独立聊天页面。
- **路由事实**：管理页面采用 `/agent-management/*`、企业页面采用 `/enterprise/*`、分析页面采用 `/analytics/*`。根路径 `/` 转到 `/agents`，未登录由路由守卫进入 `/login`；聊天为 `/agents/:agentId/chat` 及其 `/c/:conversationId` 子路由，独立模式增加 `/standalone`。
- **交互实现**：详情页复用页面专属 hooks/组件；聊天通过 Fetch ReadableStream 读取 SSE，直接合并 `text.delta`、替换 `text.replace`；恢复快照后从游标继续订阅。公司资料驱动品牌展示，部门树使用全局 Redux slice。
- **身份存储**：访问令牌保存在浏览器 localStorage；`withRefreshedToken` 名称虽含 refreshed，但目前只在 401 时清理令牌并通知守卫，没有 refresh-token 流程。
- **边界**：旧管理 Dashboard 路由有重定向；没有 `/admin/models`、`/admin/agents` 页面，也没有旧 `/chat` 入口。存在页面不代表背后的知识检索或工单能力已完成。登录页当前存在固定预填值，生产使用前需要移除；不得在文档中复制这些值。
- **验证入口**：`apps/web_chat/e2e/analytics.spec.ts`、`chat.spec.ts`、`routes.spec.ts`；场景使用 API 路由桩，不能视为真实后端或上游模型的全链路验收。

### M02 — API 接入、契约与公共基础：已实现

- **入口**：`apps/api/src/supportops_api/main.py`、`dependencies.py`、`schemas.py`、`pagination.py`、`middleware.py`、`errors.py`、`routes/`。
- **实现**：应用生命周期创建数据库/Redis 客户端并进行身份 bootstrap；请求依赖解析可信身份；CORS、关联 ID、统一业务异常响应、Pydantic 请求/响应模型、OpenAPI 文档。
- **接口分组**：`auth`、`admin_identity`、`admin_models`、`admin_agents`、`admin_tools`、`admin_knowledge`、`admin_dashboard`、`agents`、`conversations`、`runs`、`health`。
- **分页规范与现状**：共用参数为 `page >= 1`、`page_size` 默认 20 且最大 100，分页响应有 `items/total/page/page_size/pages`。Agent、模型、用户、会话、知识文档、运行/错误等主要列表已使用数据库分页；部门完整树、整体授权集合和事件流按各自完整性语义处理。工具目录、知识来源仍返回整集 `items`，知识版本直接返回整集数组，并非所有列表都已符合子目录的普通分页规范；后续变更应明确整集语义或补齐契约，不宣称全量分页已完成。
- **健康接口**：`/health/live` 为进程存活；`/health/ready` 检查 PostgreSQL 与 Redis。它们不等同于模型可用、业务工具健康或端到端 Agent 成功。
- **边界**：部分企业管理、Dashboard 聚合逻辑当前位于路由文件中；新增复杂逻辑应优先复用或下沉核心服务，不为了目录形式引入无必要抽象。
- **验证入口**：`tests/api/test_pagination_contract.py`、`test_auth.py` 及各业务 API 用例。

### M03 — 身份、租户与企业组织：已实现

- **入口**：`supportops_core/auth.py`、`passwords.py`、`bootstrap.py`；API `routes/auth.py`、`admin_identity.py`。
- **实现**：邮箱密码登录、scrypt 密码哈希、带 issuer/audience/过期校验的 HS256 JWT；JWT `sub` 为 User UUID，身份必须对应当前租户内的 active 用户。API 每次解析后以数据库最新角色和显示名为准。
- **角色**：`platform_admin` 管理控制面；`employee`、`agent_user`、`customer` 可在命中显式 Agent 授权后使用 Agent。平台管理员也不能仅凭角色跳过 Agent 使用授权。
- **企业能力**：当前租户公司资料与品牌、用户创建/资料/角色/状态、管理员重置密码、部门树及直属人数、部门管理约束。首次 bootstrap 仅在用户表为空且开关开启时创建身份，不覆盖已有账号。
- **数据**：`tenants`、`users`、`organization_units`。
- **边界**：尚无企业 SSO、外部 subject 自动建号、委托 OAuth 身份或跨企业身份联邦。API 的实时身份检查不代表排队 Run 已具备调用时重新授权，见第 6 节。
- **验证入口**：`test_auth.py`、`test_passwords.py`、`tests/api/test_identity_management.py`。

### M04 — 模型端点控制面与能力验证：已实现

- **入口**：`supportops_core/model_config.py`、`model_services.py`；API `routes/admin_models.py`；Web `pages/agent-management/models/`。
- **实现**：供应商预设、远端模型发现、端点配置、同一端点多个模型选择、版本历史、凭据轮换、启停、使用关系与软删除限制。
- **协议**：OpenAI 官方或 OpenAI-compatible，支持 Responses 与 Chat Completions；版本保存模型名称、协议、生成默认值、扩展参数、能力声明和定价信息。
- **验证链**：URL/DNS/私网策略检查、TLS/认证与最小模型请求；对声明的 streaming、tool calling、structured output 做能力探测。测试结果/历史及凭据变化后的 stale 状态供管理台使用；能力验证不通过的固定版本不能发布给 Agent 使用。
- **执行位置**：模型发现与连接测试由 API 路由/服务执行，部分测试使用 FastAPI `BackgroundTasks`；它们不是 Worker Run 队列中的业务 Agent 推理。
- **数据**：`model_endpoints`、`model_endpoint_versions`、`model_endpoint_models`、`model_endpoint_test_runs`、`model_credentials`、`model_credential_revisions`。
- **边界**：这是模型配置与固定端点适配能力，不是动态 Model Router；fallback 配置当前限制为空，未实现跨模型自动切换。API 后台探测也不应描述为持久化工作流。
- **验证入口**：`packages/support_core/tests/test_model_services.py`、`tests/api/test_agent_management.py` 中的模型契约用例。

### M05 — Agent 控制面、版本与授权：已实现

- **入口**：`supportops_core/agent_config.py`、`agent_services.py`；API `routes/admin_agents.py`、`agents.py`；Web `pages/agent-management/agents/`、`pages/agents/`。
- **实现**：Agent 基础资料/slug/欢迎语/建议问题、草稿 revision 乐观并发控制、严格配置解析与 SHA-256 摘要、发布新版本、激活历史版本以回滚、停用、用户或角色授权整体替换、控制面审计。
- **发布校验**：模型固定版本及其验证状态、凭据引用、模型能力、运行参数平台上限、已注册且允许绑定的工具、高风险工具的审批配置声明。
- **运行契约**：会话创建时固定 `AgentVersion`；该版本固定 `ModelEndpointVersion` 与解析后的工具 ID。之后发布/激活不会重写旧会话版本。本轮凭据 revision 在创建 Run 时固定，不是会话创建时固定。
- **数据**：`agents`、`agent_drafts`、`agent_versions`、`agent_access_grants`、`agent_audit_events`。
- **边界**：配置可发布不代表外部动作已受到实际审批控制；历史迁移占位 Agent 有只读限制。没有用户自定义任意执行图、上传工具代码或独立专业 Agent 编排。
- **验证入口**：`test_agent_config.py`、`tests/api/test_agent_management.py`、`test_conversations.py`。

### M06 — 受控工具目录与内置工具：部分实现

- **入口**：`supportops_core/tool_services.py`、`agent_config.py` 的 `TOOL_REGISTRY`、`agent_runtime.py` 的 `RUNTIME_TOOLS/filter_runtime_tools`；API `routes/admin_tools.py`。
- **实现**：租户工具目录自动补齐；名称、描述、角色、`low/medium/high` 风险、启停和元数据版本管理；只允许绑定服务端注册的工具实现。
- **实际工具**：`current_identity_summary` 返回服务端注入的身份摘要；`support_ticket_lookup` 是明确说明未连接工单系统的占位工具，没有真实工单查询结果。
- **运行过滤**：Agent 版本已解析工具 ID、配置 enabled、静态注册表和 Run 的角色快照取交集；`ToolRuntime[SupportContext]` 注入 tenant/user/run/conversation/correlation 上下文。
- **数据**：`tool_catalog_entries`；Agent 工具绑定与允许 ID 保存于版本配置。
- **边界**：未实现 MCP Client/Server 连接、MCP Registry 服务、统一工具执行网关、业务资源级授权、外部工具凭据委托、动作幂等记录、审批凭证或业务结果对账。目录 `is_enabled` 当前控制新版本绑定，不能当作运行时紧急撤销开关。
- **验证入口**：`tests/api/test_tool_catalog.py`、`test_agent_runtime.py` 的工具过滤与 Factory 用例。

### M07 — 知识生命周期与访问控制：基础已实现

- **入口**：`supportops_core/knowledge_services.py`、`knowledge_content.py`；API `routes/admin_knowledge.py`；Web `pages/knowledge/`。
- **实现**：租户来源与文档、治理元数据/筛选/归档、内容摘要与段落锚点、新建不可变内容版本、提交审核状态、发布、失效、从旧版本生成回滚版本、用户/角色/部门 ACL 整体替换及生命周期审计。
- **有效正文/快照**：读取时校验租户、当前发布版本、生效时间与 ACL；无 ACL 默认拒绝。发布保存内容摘要、版本、有效期、锚点和 ACL 摘要，并在同一业务事务中写知识 Outbox 事件。
- **Mock 边界**：按租户来源和 external key 幂等导入基线；production 拒绝 Mock 来源发布。当前 API 位于管理员路由下，不是员工运行时检索接口。
- **数据**：`knowledge_sources`、`knowledge_documents`、`knowledge_versions`、`knowledge_acl_entries`、`knowledge_snapshots`、`knowledge_outbox_events`；审计复用 `agent_audit_events`。
- **边界**：审核状态不是独立审批人分离的审批系统。知识 Outbox 尚无索引消费器；没有 embedding、向量/混合检索、重排、Agent 检索工具、引用事实校验、自动更新或外部 CMS 适配。pgvector 镜像存在不代表这些能力存在。
- **验证入口**：`test_knowledge_services.py`、`test_knowledge_content.py`、`tests/api/test_knowledge.py`；不能据此宣称完整 RAG 验收。

### M08 — 会话、消息与用户可见状态：已实现

- **入口**：`supportops_core/services.py`、`agent_services.py`；API `routes/conversations.py`；Web `pages/chat/`、`pages/agents/`。
- **实现**：按被授权 Agent 创建固定版本会话，首次发送前可只停留在新对话 UI；按租户、用户、Agent 查询历史，重命名、置顶、软删除，读取消息与最新 Run 的正文快照/事件游标。
- **发送事务**：必填 `Idempotency-Key`；同一用户/会话中的重复键返回同一个 Message/Run，相同键不同正文报冲突。事务内原子创建用户消息、Run 和 `run.queued` 事件，事务外通知 Redis。
- **删除与取消**：删除会话保留消息、事件和诊断事实；取消 queued Run 直接进入 cancelled，running Run 进入 cancelling。不是所有上游请求都能立即停止，Worker 在执行输出或终态边界观察取消。
- **数据**：`conversations`、`messages`、`agent_runs`、`run_events`。
- **边界**：Message 当前保存回答正文，没有持久化完整的 `citations/suggested_actions/escalation_recommended` 产品结果；消息幂等不等于外部写工具幂等；暂无同会话执行互斥保证。
- **验证入口**：`tests/api/test_conversations.py`、`test_runs.py`、`test_soft_delete.py`、Web `chat.spec.ts`。

### M09 — Agent 推理运行时与短期记忆：已实现基础主链路

- **入口**：`supportops_core/agent_runtime.py`、`agent_config.py`。
- **模型适配**：`ModelAdapter` 按固定端点版本构造 `ChatOpenAI`，映射协议、能力、生成参数、超时、重试和供应商 `extra_body`；调用前重新检查模型出站地址。
- **执行图**：`AgentFactory` 用 `create_agent` 构建统一单 Agent 图，注入系统 Prompt、可信 `SupportContext`、过滤后的工具和 `SupportAnswer`；支持 `ToolStrategy` 与 `ProviderStrategy`，配置模型/工具调用总量和工具并行上限。
- **短期记忆**：PostgreSQL checkpointer 的 thread ID 包含 tenant/conversation。已有 checkpoint 时追加本轮输入；不存在时从产品消息重建。非 PostgreSQL 测试环境用内存 saver，不具备跨进程持久化。checkpoint 定期按保留期清理，产品消息不随之删除。
- **输出**：`StructuredAnswerStream` 从结构化模型增量中仅提取 answer；工具参数、隐藏推理和其他结构字段不进入正文流。最终必须通过 `SupportAnswer` 校验；模型/工具 callback 收集观测与 provider 元数据。
- **边界**：没有显式 Knowledge/Diagnostic/Ticket 专业执行图或业务 Orchestrator；无历史摘要、证据上下文管理、长期记忆 Store。`max_input_tokens/max_cost_usd` 当前仅做本轮输入与单次输出估算，不是覆盖完整历史和多次调用的累计硬预算。
- **验证入口**：`test_agent_runtime.py`；`tests/postgres/test_checkpoint.py` 验证真实 checkpoint 重连与 thread 隔离。

### M10 — 异步 Run、队列、事件与恢复：基础已实现

- **入口**：`apps/agent_worker/src/supportops_worker/main.py`、`supportops_core/queue.py`、`worker_runtime.py`、`services.py`；API `routes/runs.py`。
- **队列与领取**：Redis `RPUSH/BLPOP` 传 Run UUID；数据库行锁只允许 queued Run 转 running，重复投递不会再次领取同一 Run。当前每个 Worker 消费循环等待一个 Run 执行结束，可通过多个进程增加总体并行，但没有同会话执行锁。
- **执行快照**：加载并交叉校验租户、会话、Agent/模型版本、config digest、输入消息、用户和固定凭据 revision；整个 Agent 执行有超时。仅在助手消息与完成事件的产品事务成功后标记 completed。
- **流式事实**：`_AnswerEventWriter` 约每 50ms 合并后独立提交正文事件；正文延长使用 `text.delta`，改写使用 `text.replace`。取消状态检查阻止继续写增量；SSE 按 Run 内递增 sequence 从 PostgreSQL 轮询，支持 cursor/Last-Event-ID。
- **异常处理**：稳定错误码、失败/取消观测；执行失败或未提交产品状态时尝试清除会话 checkpoint，后续可从产品消息重建。观测写入使用独立事务，不回滚已成功的产品状态。
- **周期任务**：queued Run 恢复扫描、Worker 服务租约心跳、Agent 健康快照刷新、checkpoint 保留清理。
- **状态集合**：`queued/running/cancelling/completed/failed/cancelled`；没有 `waiting_approval/reconciling` 或业务动作状态。
- **边界**：暂无 Run Outbox、队列 ACK/死信机制、Run 级租约/执行尝试/失效执行者隔离、running 崩溃恢复。服务租约不是 Run 租约；checkpoint 保存不等于调度层自动恢复执行。
- **验证入口**：`tests/worker/test_worker.py`、`tests/api/test_streaming.py`；多数测试使用 SQLite/FakeRedis/模型替身。

### M11 — 运行观测、错误诊断与 Analytics：基础已实现

- **入口**：`supportops_core/agent_runtime.py` 的 callback、`worker_runtime.py`、`health_services.py`；API `routes/admin_dashboard.py`；Web `pages/analytics/`。
- **运行指标**：模型/工具调用摘要、Token、缓存/推理 Token、定价估算成本、重试、finish reason、provider request ID、队列/执行/端到端耗时和首输出耗时；缺失信息保留 unknown/null，不伪造零成本或健康。
- **追踪与错误**：Agent 父 span 与模型/工具子 observation、稳定错误事件、元数据白名单、Agent/模型/知识控制面追加审计、分页错误查询与详情/运行时间线。工具目录编辑当前更新元数据版本，没有独立的调用治理审计链。
- **分析接口**：summary/timeseries/runs/errors/trace，系统 Agent 状态和排行；健康由 Agent 版本、近期执行事实与服务租约推导并可持久化为读模型。
- **数据**：`agent_run_observations`、`agent_error_events`、`agent_runtime_health`、`runtime_service_leases`，关联 Run/Event/Audit。
- **边界**：当前是应用内观测模型，没有 OpenTelemetry exporter 或独立评测执行平台。Worker 首输出时间不等同于客户端实际收到首文本；多轮 checkpoint 累计消息与本轮调用统计边界需验证。服务租约在长任务期间可能过期，不可把 Dashboard 当作已完成生产 SLO 验收。
- **验证入口**：`tests/api/test_dashboard.py`、Worker 观测/健康用例、Web `analytics.spec.ts`。

### M12 — 数据持久化、配置、凭据与基础设施：基础已实现

- **入口**：`supportops_core/models.py`、`db.py`、`config.py`、`secrets.py`、`logging.py`；`migrations/`、`deploy/docker-compose.yml`、workspace 与 Makefile。
- **数据层**：SQLAlchemy async ORM、PostgreSQL/JSONB、Alembic 迁移、索引/约束/数据字典注释；本地 Compose 使用 PostgreSQL 17/pgvector 与 Redis 8，并提供持久卷和健康检查。
- **软删除**：带 `SoftDeleteMixin` 的资源用 `deleted_at`；ORM 默认隐藏已删除行并禁止业务物理删除。授权/ACL 替换保留历史；活跃记录唯一索引允许删除后重建。内部历史查询显式 `execution_options(include_deleted=True)`，客户端不能传绕过参数。
- **凭据**：`SecretProvider` 协议与 `LocalEnvelopeSecretProvider` 的 Fernet 密封；API 返回 masked hint/revision，历史 revision 支持排队 Run 固定引用。没有真实 Vault/KMS 插件、短期委托凭据或连接器进程隔离。
- **配置**：`SUPPORTOPS_` 前缀与 `.env`；production 拒绝默认 JWT/加密密钥/示例管理员密码、私网模型端点、非 PostgreSQL 数据库及缺失模型域名允许列表。
- **边界**：这些配置校验不代表完整生产安全认证；Compose 仅编排基础设施，未提供 API/Worker/Web 的生产部署、备份恢复或高可用交付。
- **验证入口**：`test_secrets.py`、`test_schema_comments.py`、`tests/api/test_soft_delete.py`、`tests/postgres/`；迁移校验必须使用隔离本地/测试 PostgreSQL。

## 4. 数据模型分组与数量

以下仅统计 `supportops_core.models` 的 29 张业务表，不统计 checkpoint 扩展表和 Alembic 版本表。

| 数据域 | 数量 | 表 |
| --- | --- | --- |
| 身份与组织 | 3 | `tenants`、`users`、`organization_units` |
| 模型与凭据 | 6 | `model_endpoints`、`model_endpoint_versions`、`model_endpoint_models`、`model_endpoint_test_runs`、`model_credentials`、`model_credential_revisions` |
| Agent 与工具控制面 | 6 | `agents`、`agent_drafts`、`agent_versions`、`agent_access_grants`、`tool_catalog_entries`、`agent_audit_events` |
| 知识生命周期 | 6 | `knowledge_sources`、`knowledge_documents`、`knowledge_versions`、`knowledge_acl_entries`、`knowledge_snapshots`、`knowledge_outbox_events` |
| 会话、执行与观测 | 8 | `conversations`、`messages`、`agent_runs`、`run_events`、`agent_error_events`、`agent_run_observations`、`agent_runtime_health`、`runtime_service_leases` |

计数用于当前结构说明，新增表或调整领域职责后应同步更新，不将该数量作为架构限制。

## 5. 核心链路与必须保持的不变量

### 5.1 发布与对话执行

1. 管理员配置模型、密封凭据并测试能力；保存 Agent 草稿、校验、发布、激活和显式授权。
2. 用户经 API 认证，创建固定 Agent 版本会话；发送时再次检查当前 Agent 使用授权。
3. API 锁定会话完成 Message/Run/Event 的幂等事务；提交后将 Run UUID 通知 Redis，通知失败保留 queued 事实供扫描。
4. Worker 幂等领取 Run，加载固定快照，构造统一 Agent 图并读取 checkpoint。
5. Agent 调用固定模型及可用本地工具；正文增量落事件表，API SSE 与 Web 恢复游标连接。
6. 回答完整校验后，在同一产品事务保存助手正文、阶段完成和 Run 终态；观测单独提交。错误或取消按状态机处理。

### 5.2 知识发布（尚未连接步骤 4–5）

来源/文档 → 内容版本与锚点 → 提交审核状态 → 发布快照 + 知识 Outbox + 审计 → 按实时 ACL/有效期读取。索引消费、检索、证据进入 Agent 和引用验证均为后续工作。

### 5.3 维护不变量

- PostgreSQL 是事实来源；Redis、健康快照和浏览器状态不能覆盖原始业务事实。
- 不改变旧会话固定的 Agent/模型版本；保留本轮凭据 revision 与事件单调 sequence 的语义。
- 身份、租户与资源范围来自可信服务端上下文；模型参数不能扩权。
- 已删除资源默认不可编辑或发送；审计事实保留，不能引入客户端 `include_deleted` 绕过。
- 回答完成必须晚于产品消息事务提交；Schema 校验只能证明结构，不能宣称事实或业务动作已成功。
- 不把消息幂等、checkpoint、目录审批字段或健康租约描述为已经覆盖外部动作幂等、实际审批或 Run 崩溃恢复。
- 工具参数、密钥、完整令牌、隐藏推理不得进入用户正文；新增日志/观测保持白名单和脱敏。

## 6. 对照 2026 目标设计的差距与实施优先级

这些是待实施项，不能通过更新文档宣称已完成。编号 P0/P1/P2 表示工程推进优先级，不等同于 OpenSpec 产品阶段。

| 优先级 | 目标能力 | 当前差距 | 实施方向 |
| --- | --- | --- | --- |
| P0 | 长任务下可靠的服务健康 | Worker 在 `await execute_agent_run` 后才更新心跳，服务租约为 15 秒 | 心跳与消费/周期维护解耦，准确统计活动 Run |
| P0 | 持久化执行恢复 | 恢复扫描只处理 queued；无 Run owner/租约/attempt | Run 级租约、失效执行者隔离、恢复与失败收敛；恢复前判断副作用 |
| P0 | 同会话串行与上下文隔离 | 领取只锁 Run；多个 Run 共用 checkpoint thread；历史加载不按本轮输入截断 | 会话执行互斥/显式排队、固定本轮上下文边界 |
| P0 | 调用时实时授权 | Worker 使用 `run.identity_roles`，只校验用户存在；目录动态角色/风险未完整进入执行 | 用户状态与授权复检、目录策略统一、每次工具调用资源授权与紧急撤销 |
| P0 | 配置与执行一致 | 单工具 `max_calls_per_run` 未形成独立执行门禁；审批只有发布配置检查 | 补齐单工具限额；未具备实际审批前不接入高风险副作用 |
| P0 | 完整上下文与预算 | 预算仅估算本轮输入与单次输出 | 每次调用前上下文计量、历史/工具结果裁剪、Run 累计预算与超额退出 |
| P1 | 可信知识与证据链 | 生命周期已有，索引/检索/引用缺失 | 消费知识 Outbox；权限和有效期过滤；检索/按需重排；证据快照与引用校验 |
| P1 | 结构化产品结果 | 最终仅保存 answer 正文 | 版本化保存引用、建议动作和升级状态，支持刷新后展示与流程消费 |
| P1 | 业务诊断与工单闭环 | 无诊断模块，工单工具占位 | 选择已确认场景；只读业务连接器；结构化证据与幂等建单/升级包 |
| P1 | 工具执行网关与审批 | 无 MCP 连接、动作记录或等待状态 | 本地/API/MCP 统一调用边界；条件审批持久化、批准后复检、幂等执行与对账 |
| P1 | 观测与可执行评测 | 有观测和文档基线，无独立质量评测闭环 | 真实 PostgreSQL/队列故障验收；版本化场景评测；事实、引用、授权和业务结果门禁 |
| P2 | 按需专业 Agent、模型路由、长期记忆 | 单 Agent 固定模型，配置预留为空 | 先用评测证明收益，再添加隔离上下文、多模型策略和可纠错/删除记忆 |

额外边界：当前 Run 入队没有事务 Outbox；知识 Outbox 的存在不改变这一事实。软删除与 checkpoint 清理不是个人数据保留/擦除政策的完整实现。API 实时身份校验也不能消除已经排队的旧权限快照。

未来外部动作应遵循：**动作提案 → 参数/权限/风险校验 → 必要时持久化等待审批 → 执行前复检 → 幂等执行 → 独立结果复核 → 记录结果**。结果未知先对账，不盲目重试；审批绑定参数，参数变化需重新校验。工具目录位于治理侧，全程监控与审计作为横向能力，专业 Agent 不强制拆成微服务。

风险实现当前是 `low/medium/high`，产品规范 [support-risk-policy](openspec/specs/support-risk-policy/spec.md) 使用 R0–R4；接入真实动作前必须明确映射，不能认为已经统一。

## 7. 项目级开发约定

### 7.1 范围、语言与变更安全

- 用户回复、进度、新增说明默认中文；保留既有代码/API 标识。先阅读受影响实现、调用方、测试和作用域内 AGENTS。
- 以实际代码和检查结果为依据；只读分析不修改文件，不创建额外流程产物。文档更新需区分现状、缺口、目标和本次实际验证。
- 保留工作区已有改动，不使用破坏性 Git 操作，不顺带修改无关功能。默认不创建审查报告、操作日志或上下文 JSON。
- 未获明确授权不提交、推送、部署或发送外部消息；不可恢复删除、覆盖用户修改和破坏性迁移先确认。
- 不读取或传播与任务无关的凭据/个人信息；示例配置只引用变量名，不复制登录页预填值或真实 `.env`。
- 本次任务新增且属于交付的新文件逐个 `git add -- <明确路径>`；交付前检查 `git status --short`。禁止 `git add .`、`git add -A` 或暂存无关文件。

### 7.2 分层、契约与持久化

- 优先复用现有核心服务、Schema、状态机、组件与 SDK，不添加重复工具链或未经论证的生产依赖。
- 公共 API 变更同步检查 Pydantic、`api.ts`、`types.ts`、调用页面、测试及相关 OpenSpec；分页与权限按 API 子目录规则执行。
- 数据结构通过 Alembic 源迁移修改，不手工改生成文件，不把 `Base.metadata.create_all` 的测试方案带入生产启动。
- 严格类型边界，不用无依据的 `any`、`@ts-ignore` 或关闭检查掩盖问题。新增外部输入需校验，业务异常保留稳定错误码。
- 不为了恢复或测试跳过租户过滤、ACL、端点网络策略、秘密密封、能力验证或生产配置约束。
- 业务事实事务、通知/观测事务边界应明确；外部副作用无法与数据库原子提交时，需要显式幂等与对账策略。

### 7.3 前端与依赖

- Python 依赖由 uv workspace 与 `uv.lock` 管理；前端只使用现有 pnpm，不混用 npm/Yarn，不手工编辑锁文件。
- 页面目录、专属组件与 hooks、全局 Redux、shadcn UI、表单校验、列表加载、可访问性和反馈遵循 `apps/web_chat/AGENTS.md`。
- Redux 当前仅注册部门 slice，新增全局服务端状态按共享需求判断；页面 API 数据在 `types.ts` 中明确声明并添加中文字段说明。
- 不将任何服务端凭据放入 Vite 客户端环境变量或源码。认证机制与浏览器令牌存储变更应单独评估契约和安全影响。

## 8. 验证命令与实际验证边界

按改动范围执行已有命令；纯文档改动通常只需链接、命令、模块计数与代码契约核对，不要求重跑整个应用。未运行的检查不得写成通过。

```bash
# 安装与基础设施，仅在需要时执行
uv sync --all-packages
pnpm install
docker compose -f deploy/docker-compose.yml up -d
uv run --package supportops-api alembic upgrade head

# Python 静态检查与测试
uv run ruff check .
uv run mypy
uv run pytest

# 前端类型与构建
pnpm run web:type-check
pnpm run web:build

# 浏览器测试（需要可用的 Playwright Chromium）
pnpm --dir apps/web_chat exec playwright install chromium
pnpm --dir apps/web_chat run e2e

# OpenSpec；具体 change 按本次范围选择
openspec validate establish-enterprise-support-agent-foundation --strict

# 汇总入口
make check
```

- 默认 Python API/Worker 测试使用 SQLite、FakeRedis 和替身；不能证明 PostgreSQL 行锁、多 Worker 并发、真实 Redis 故障、外部模型/工具或线上负载正确。
- `tests/postgres` 需要显式 `SUPPORTOPS_RUN_POSTGRES_TESTS=1`；测试会读取 Settings 的连接配置，没有一概保证“不会连接生产库”的自动隔离机制。执行前必须确认连接的是隔离本地/测试数据库，不输出连接串。
- 在已确认的隔离数据库上运行：`SUPPORTOPS_RUN_POSTGRES_TESTS=1 uv run pytest -q tests/postgres`；迁移结构核验使用 `uv run --package supportops-api alembic check`。
- Web E2E 使用 API 路由桩；模型能力测试会出站请求且使用被测试端点凭据，不能作为纯离线测试随意执行。
- 如检查失败，区分既有失败与本次引入的问题，不删除有效测试或关闭规则。

## 9. 文档维护与参考入口

- `README.md` 面向使用者：产品阶段、现状架构、模块摘要、路由、启动、接口、验证和已知边界。
- 根 `AGENTS.md` 面向贡献者：模块职责/实现细节、数据与调用链、不变量、目标差距及工作规则。
- `docs/` 和 `openspec/` 提供专题实现/产品规格，若与代码不一致应先核验；更新任务只修改授权范围内文档，不批量扩写无关文档。
- 行为、路由、表结构、模块状态或运行方式变化时，同步更新相关 README/AGENTS 项，不把未来能力提前写成已实现。
- 当前专题文档中的凭据固定时点、“工具风险已全链路校验”等概括应结合本文和代码理解；缺口以实现事实为准。

主要入口：[实现文档](docs/README.md)、[运行链路](docs/02-runtime-flow.md)、[数据模型](docs/03-data-model.md)、[API 契约](docs/04-api-contracts.md)、[产品范围](openspec/specs/support-product-scope/spec.md)、[风险规范](openspec/specs/support-risk-policy/spec.md)、[场景基线](openspec/changes/archive/2026-09-14-define-support-product-baseline/scenario-catalog.md)、[评测口径](openspec/changes/archive/2026-09-14-define-support-product-baseline/evaluation-baseline.md)。
