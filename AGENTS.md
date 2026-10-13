# AGENTS.md — SupportOps Agent 项目工作指南

本文适用于整个仓库，供 AI 编码助手和项目贡献者建立实现上下文。`apps/api/AGENTS.md`、`apps/web_chat/AGENTS.md` 补充各自目录的约定；修改相应目录前应阅读这些文件，并按指令优先级与作用域执行。

实现快照日期：**2026-10-13**。以当前代码为事实来源，包含当前工作区已有的未提交实现；这里的“已实现”表示存在实现，不表示本次文档更新重新运行了全部测试，也不表示已经通过生产验收。

## 1. 产品定位与当前阶段

SupportOps Agent 面向企业员工、客服和技术支持，目标是完成“理解问题 → 获取知识与业务证据 → 诊断 → 回答或升级 → 受控业务动作 → 结果复核”的支持闭环。

当前已形成 **企业 Agent 控制面 + 员工聊天工作台 + 单 Agent 异步执行底座**，具备关键词知识证据链、隔离 Mock 429 诊断与 R2 动作账本/独立复核。真实业务连接器、外部工单、远程 MCP、持久化人工审批与客服接管仍待接入。

- 当前部署边界：**3 个应用 + 1 个共享 Python 核心包**，属于 API 与 Worker 分进程的模块化单体。
- 本文按职责划分 **12 个逻辑能力模块（M01–M12）**。模块不等于目录、微服务或独立 Agent；共享文件可以承载多个相关模块。
- `models.py` 当前定义 **43 张业务 ORM 表**；LangGraph checkpoint 表不计入。新增执行治理、知识、结果、审核、调度、探测与动作字段需依次应用 `20261012_0018` 至 `20261013_0030`；本次未执行迁移，已部署库数量取决于迁移状态。升级 0028 前须停止旧 API 的 BackgroundTasks 探测者，新 Worker 只领取持久化探测。
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
                    关键词索引消费 → 当前 ACL 检索 → 证据/引用复检
```

## 3. 十二个模块的实现明细

### M01 — Web 产品入口与交互工作台：已实现

- **入口**：`apps/web_chat/src/router/index.tsx`、`layouts/MainLayout.tsx`、`pages/`、`api.ts`、`types.ts`。
- **现有页面**：登录；授权 Agent 目录；Agent/模型列表与详情；工具目录；公司、用户、部门；知识列表与详情；Analytics 概览、排名、Agent 状态/详情、运行与错误；普通和独立聊天页面。
- **路由事实**：管理页面采用 `/agent-management/*`、企业页面采用 `/enterprise/*`、分析页面采用 `/analytics/*`。根路径 `/` 转到 `/agents`，未登录由路由守卫进入 `/login`；聊天为 `/agents/:agentId/chat` 及其 `/c/:conversationId` 子路由，独立模式增加 `/standalone`。
- **交互实现**：详情页复用页面专属 hooks/组件；聊天通过 Fetch ReadableStream 读取 SSE，直接合并 `text.delta`、替换 `text.replace`；恢复快照后从游标继续订阅。公司资料驱动品牌展示，部门树使用全局 Redux slice。
- **身份存储**：访问令牌保存在浏览器 localStorage；`withRefreshedToken` 名称虽含 refreshed，但目前只在 401 时清理令牌并通知守卫，没有 refresh-token 流程。
- **边界**：旧管理 Dashboard 路由有重定向；没有 `/admin/models`、`/admin/agents` 页面，也没有旧 `/chat` 入口。存在页面不代表背后的知识检索或工单能力已完成。登录页账号/密码初始为空，源码与重新生成的客户端产物已移除旧固定预填值；本地身份 bootstrap 仍由服务端配置管理。
- **验证入口**：`apps/web_chat/e2e/analytics.spec.ts`、`chat.spec.ts`、`routes.spec.ts`；场景使用 API 路由桩，不能视为真实后端或上游模型的全链路验收。

### M02 — API 接入、契约与公共基础：已实现

- **入口**：`apps/api/src/supportops_api/main.py`、`dependencies.py`、`schemas.py`、`pagination.py`、`middleware.py`、`errors.py`、`routes/`。
- **实现**：应用生命周期创建数据库/Redis 客户端并进行身份 bootstrap；请求依赖解析可信身份；CORS、关联 ID、统一业务异常响应、Pydantic 请求/响应模型、OpenAPI 文档。
- **接口分组**：`auth`、`admin_identity`、`admin_models`、`admin_agents`、`admin_tools`、`admin_knowledge`、`admin_dashboard`、`admin_run_notifications`、`agents`、`conversations`、`runs`、`health`。
- **分页规范与现状**：共用参数为 `page >= 1`、`page_size` 默认 20 且最大 100，分页响应有 `items/total/page/page_size/pages`。工具目录、知识来源、知识版本和运行通知现已与 Agent、模型、用户、会话、知识文档、运行/错误列表一样采用数据库 count/offset/limit 与稳定唯一排序。来源支持名称搜索，文档响应批量携带来源摘要；Web 工具/版本历史使用服务端分页。知识版本由数组升级为分页对象，前后端需要同步发布。部门完整树、整体替换授权集合和事件流仍保留完整语义。
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
- **执行位置**：模型发现与旧兼容基础连接探测仍由 API 服务执行；逐模型流式探测由 API 同事务创建 TestRun/ModelProbeJob、Worker 独立消费者执行，无 BackgroundTasks。任务固定端点/模型/凭据 revision，独立 owner/lease/generation，每个写入边界复检当前管理员和固定引用，默认三次/十分钟有界恢复；取消原子 fencing，旧执行者不能覆盖模型验证状态。
- **数据**：`model_endpoints`、`model_endpoint_versions`、`model_endpoint_models`、`model_endpoint_test_runs`、`model_credentials`、`model_credential_revisions`、`model_probe_jobs`。
- **边界**：这是模型配置与固定端点适配能力，不是动态 Model Router；fallback 当前限制为空，无跨模型切换。ModelProbeJob 不使用聊天 Run/checkpoint；旧活跃探测不补造 owner/次数，迁移为明确失败后需重新发起。管理台只读恢复/取消/有限重试提示已接入，真实网络/故障验收未执行。
- **验证入口**：`packages/support_core/tests/test_model_services.py`、`tests/api/test_agent_management.py` 中的模型契约用例。

### M05 — Agent 控制面、版本与授权：已实现

- **入口**：`supportops_core/agent_config.py`、`agent_services.py`；API `routes/admin_agents.py`、`agents.py`；Web `pages/agent-management/agents/`、`pages/agents/`。
- **实现**：Agent 基础资料/slug/欢迎语/建议问题、草稿 revision 乐观并发控制、严格配置解析与 SHA-256 摘要、发布新版本、激活历史版本以回滚、停用、用户或角色授权整体替换、控制面审计。
- **发布校验**：模型固定版本及其验证状态、凭据引用、模型能力、运行参数平台上限、已注册且允许绑定的工具、高风险工具的审批配置声明。
- **运行契约**：会话创建时固定 `AgentVersion`；该版本固定 `ModelEndpointVersion` 与解析后的工具 ID。之后发布/激活不会重写旧会话版本。本轮凭据 revision 在创建 Run 时固定，不是会话创建时固定。
- **数据**：`agents`、`agent_drafts`、`agent_versions`、`agent_access_grants`、`agent_audit_events`。
- **边界**：配置可发布不代表外部动作已受到实际审批控制；历史迁移占位 Agent 有只读限制。没有用户自定义任意执行图、上传工具代码或独立专业 Agent 编排。
- **验证入口**：`test_agent_config.py`、`tests/api/test_agent_management.py`、`test_conversations.py`。

### M06 — 受控工具目录与本地执行网关：基础治理已实现

- **入口**：`tool_services.py`、`tool_policy.py`、`tool_gateway.py`、`resource_policy.py`、`quota_services.py`；API `admin_tools.py`；Web 工具管理。
- **目录与策略**：静态实现/固定 Agent 绑定/当前目录取交集，is_enabled 限制历史版本后续调用，独立 is_revoked 紧急撤销；解除要求 expected_version，更改与撤销有追加审计。low→R1、medium→R3、high/未知→R4，静态风险不能被目录降低，批量/破坏性参数提升风险。
- **实时授权**：领取、续租、每次模型/工具调用及提交前以数据库当前用户/租户/角色/Agent grant 判定；资源级接口使用服务端事实，未知资源默认拒绝，管理员没有隐式业务越权。
- **网关**：严格 Schema/参数大小、权限、单工具/整个 Run 次数、租户每日/并发额度、风险和审批门禁；执行前持久化 ToolInvocation，失败/取消消费许可，同 call_id 不重复执行。返回前复检策略并脱敏/限制大小，只存参数与结果摘要。
- **实际工具**：`current_identity_summary` 使用当前身份；`support_ticket_lookup` 明确未连接工单系统；`search_knowledge` 在网关内部检索当前主体获准的有效知识。既有 Agent 要显式发布绑定新工具，不自动改写历史版本。
- **动作**：BusinessAction/ActionAttempt/ActionReceipt 与 Run/RunAttempt 分开；业务键使用可信主体/资源/issue，不含 Run 或模型调用 ID。create_mock_ticket 只冻结服务端派生提案，静态最低 R2，模型不能覆盖主体/参数；执行意图由独立接口当前授权后提交。Worker 以动作 owner/generation/lease 执行隔离目标事务，再独立验证；submitted 不是业务成功，丢响应/超时/过期保留 unknown，只有 verified 或目标封锁旧请求后的 not_executed 才完成/同键重试。有限对账与人工追加一次查询许可保留累计次数，冲突进入 needs_attention。
- **边界**：默认 business_action_mock_enabled=false，production 禁止；没有真实 MCP/API、委托凭据、持久化审批或自动补偿。R3/R4/需审批继续拒绝，Mock 目标封锁迟到请求的能力不得外推为真实 API 的保证；当前元数据/本人模拟项目不代表真实工单集成。
- **实现版本**：发布快照 resolved_tool_versions 固定工具实现，目录版本与实现版本分开；旧已知 v1 兼容，未知/不可用实现不能静默使用新版。只读恢复证明按工具 ID + 实现版本判断。网关独立写调用/拒绝/检索追踪，SDK callback 不重复计数，观测失败不回滚调用事实。
- **隔离诊断**：额外注册 get_request_logs/get_current_user_profile/get_model_route_status/list_active_incidents，结合 search_knowledge 为 5 项核心能力。默认 diagnostic_mock_enabled=false，production 禁止启用；仅本人服务器生成的模拟项目，request ID 必须由用户提供，查询窗口带时区且不超过 15 分钟。`diagnostic_workflow` 将 S01–S06 接入十节点/五查询上限的固定流程，事实按工具/字段白名单保存到调用账本；查询事实与根因候选分开，唯一跨源一致候选还需当前获准版本化知识声明才确认模拟根因。缺信息追问，冲突/失败/预算不足生成未确认事项和接管包，不伪称建单或真实生产结论。
- **数据**：`tool_catalog_entries`、`tool_invocations`、RunEvent/Audit；新功能测试按当前约定暂缓。

### M07 — 知识生命周期、关键词检索与证据链：基础已实现

- **入口**：`knowledge_services.py`、`knowledge_content.py`、`knowledge_index_services.py`、`knowledge_retrieval.py`；API `admin_knowledge.py/admin_knowledge_index.py`；Web 知识详情索引面板。
- **生命周期**：来源/不可变版本/审核状态/发布快照/失效/回滚/归档/用户角色部门 ACL；生命周期和 ACL 使用文档锁，同事务产生发布、取代、失效、归档、权限变化与到期 Outbox。
- **索引**：Worker 独立消费知识 Outbox，短事务领取、幂等收敛、保存点回滚、有限退避/死信；管理员分页查看元数据、重建或新建有限恢复任务，保留旧死信及次数。旧当前快照由迁移排重建，未构建不伪装 ready。
- **分块与检索**：keyword_v1 按章节和 1600 字符边界分块，记录偏移/原文摘要/确定性 chunk_key；英文数字词项/中文二元组、转义通配符和数量上限，以 SQL 先筛租户/主体/当前版本/有效期，再锁文档复检后读正文。不缓存正文或权限。
- **证据**：服务端生成 evidence ID，绑定 user/Run/attempt/snapshot/chunk/digest/时间/关键词分数；工具返回、最终提交和用户展开均复查权限与当前版本。自造 URL/未检索引用拒绝提交；失效或撤权隐藏标题/快照/摘要，保留可审计记录。
- **数据**：原 6 张知识表 + `knowledge_index_generations`、`knowledge_chunks`、`knowledge_evidence`，共 9 张。
- **审核门禁**：送审只写 submitted_by/at 和 pending，独立 review API 写实际批准/拒绝及 revision；创建人/提交人禁止自审。批准摘要固定内容、标题、有效期和 ACL，发布复检范围与审核人当前管理员权限；新版本或范围变化须复审。旧审核中字段迁移为提交事实，不补造批准；管理台决策弹窗与发布/重新送审分开。
- **边界**：按用户 2026-10-13 选择暂不启用 embedding、向量/混合搜索、重排；关键词分数不是语义相似度。外部 CMS、完整证据冲突流程及检索评测仍待实施；测试/迁移验收暂缓。

### M08 — 会话、消息与用户可见状态：已实现

- **入口**：`supportops_core/services.py`、`agent_services.py`；API `routes/conversations.py`；Web `pages/chat/`、`pages/agents/`。
- **实现**：按被授权 Agent 创建固定版本会话，首次发送前可只停留在新对话 UI；按租户、用户、Agent 查询历史，重命名、置顶、软删除，读取消息与最新 Run 的正文快照/事件游标。
- **发送事务**：必填 `Idempotency-Key`；同一用户/会话中的重复键返回同一个 Message/Run，相同键不同正文报冲突。事务内原子创建用户消息、Run、`run.queued` 事件与唯一 Run 通知 Outbox，提交后 API 尽力即时投递；投递失败保留通知供 Worker 退避重试，不新建业务输入。
- **删除与取消**：删除会话保留消息、事件和诊断事实；取消 queued Run 直接进入 cancelled，running Run 进入 cancelling。不是所有上游请求都能立即停止，Worker 在执行输出或终态边界观察取消。
- **数据**：`conversations`、`messages`、`agent_runs`、`run_events`。
- **产品结果**：Message 保存版本 1 SupportResult（Run/attempt、答案、摘要、合法引用、未执行建议、升级意向）；旧消息为空。result.ready 和终态同事务，刷新与展开复查引用当前权限，建议/升级不伪称已执行。input_sequence 与有效 attempt 许可保证同会话串行；消息幂等仍不代表外部动作幂等。
- **验证入口**：`tests/api/test_conversations.py`、`test_runs.py`、`test_soft_delete.py`、Web `chat.spec.ts`。

### M09 — Agent 推理运行时与短期记忆：已实现基础主链路

- **入口**：`supportops_core/agent_runtime.py`、`agent_config.py`。
- **模型适配**：`ModelAdapter` 按固定端点版本构造 `ChatOpenAI`，映射协议、能力、生成参数、超时、重试和供应商 `extra_body`；调用前重新检查模型出站地址。
- **执行图**：`AgentFactory` 用 `create_agent` 构建统一单 Agent 图，注入系统 Prompt、可信 `SupportContext`、过滤后的工具和 `SupportAnswer`；支持 `ToolStrategy` 与 `ProviderStrategy`，配置模型/工具调用总量和工具并行上限。
- **短期记忆**：新执行以 tenant/conversation/run/attempt 唯一 thread 隔离，从产品消息按本轮 input_sequence 重建；checkpoint 指针只在产品事务成功后确认，失败尝试隔离保留，迟到旧尝试不能写产品事实。非 PostgreSQL 内存 saver 不能跨进程恢复；保留期清理不删除产品消息。
- **输出**：`StructuredAnswerStream` 从结构化模型增量中仅提取 answer；工具参数、隐藏推理和其他结构字段不进入正文流。最终必须通过 `SupportAnswer` 校验；模型/工具 callback 收集观测与 provider 元数据。
- **上下文与预算**：ContextSnapshot 保存当前问题摘要/输入边界/有限历史/证据时间/确认事实约束/冲突/未完成事项；数据库只加载有限旧轮次，失效证据派生的旧答案不再复用。逐模型调用计量完整系统/历史/工具/结构化输出，采用 UTF-8 保守估算与完整旧轮次裁剪，保留当前输入/本轮工具轨迹，未知多模态拒绝。
- **调用账本**：ModelCallLedger 在 Run/租户锁下预留完整输入/输出费用、次数，重试/恢复共用；正式执行关闭 SDK 隐式重试并对每次物理重试记账。实际 usage 释放未消费预留，未知/取消/崩溃保留预留；硬预算要求可信 USD 定价，缓存/推理不重复累加。供应商扩展不能覆盖输出/模型/重试/治理字段。Trace 使用账本，不累加父子成本。
- **诊断流程**：隔离 Mock 的 429 使用服务端固定事实工作流，不产生虚构模型调用或 checkpoint；节点/report 进入 ContextSnapshot、阶段 SSE 和完整产品结果。恢复仅复用同 Run/参数/版本/当前策略、五分钟内的成功只读事实；原消费保留，知识引用重新检索。详情和历史结果当前授权投影隐藏失效事实/候选/接管包。
- **边界**：没有 Diagnostic/Ticket 专业多 Agent 或通用业务 Orchestrator；真实业务诊断、语义摘要、长期记忆与真实 tokenizer/供应商预算验收仍待实施，保守估算不代表精确用量。
- **验证入口**：`test_agent_runtime.py`；`tests/postgres/test_checkpoint.py` 验证真实 checkpoint 重连与 thread 隔离。

### M10 — 异步 Run、队列、事件与恢复：基础已实现

- **入口**：`apps/agent_worker/src/supportops_worker/main.py`、`supportops_core/queue.py`、`worker_runtime.py`、`services.py`；API `routes/runs.py`。
- **队列与领取**：Redis `RPUSH/BLPOP` 传 Run UUID 作通知；默认先锁共享轮转游标，再锁会话/Run，仅成功通知且同会话最早未结束的 queued Run 可转 running，重复通知不再次执行。Worker 有可配置并发（默认 1）与容量背压，领取成功才计入活动 Run；输入序号、会话有效 attempt 许可和只读有限恢复已接入。
- **通知 Outbox**：`run_outbox_services.py` 分批按 `SKIP LOCKED` 领取通知，每条独立事务、有限 Redis 超时；采用至少一次通知。queued 未领取可有限再次通知，前轮阻塞不消耗后轮次数；默认每周期最多 8 次/24 小时，退避后进入 dead_letter。管理员按租户分页查询状态，凭 revision 显式恢复有限窗口；原 Run、输入、累计尝试与固定版本不变，已取消/执行 Run 不复活。
- **执行快照**：加载并交叉校验租户、会话、Agent/模型版本、config digest、输入消息、用户和固定凭据 revision；整个 Agent 执行有超时。仅在助手消息与完成事件的产品事务成功后标记 completed。
- **流式事实**：`_AnswerEventWriter` 约每 50ms 合并后独立提交正文事件；正文延长使用 `text.delta`，改写使用 `text.replace`。取消状态检查阻止继续写增量；SSE 按 Run 内递增 sequence 从 PostgreSQL 轮询，支持 cursor/Last-Event-ID。
- **异常处理**：RunAttempt owner/generation/lease；过期只读尝试有限恢复，权限/配置硬错误不重试，未知副作用隔离等待对账。旧执行者事件、工具返回和最终提交经过许可判定；观测独立落库且带 attempt。
- **周期任务**：服务心跳、通知分发、旧 queued 恢复扫描、健康刷新和 checkpoint 清理独立运行；各操作有超时/失败重试，不阻塞消费。维护异常在租约显示 degraded；连续心跳失败或后台任务意外退出会停止领取并报错退出。队列消息深度、数据库 queued Run 数和真实活动数分开记录。
- **停机**：停止领取，宽限期保持心跳；超时协作取消当前尝试，副作用未知隔离。未确认取消保留 degraded/过期租约并报错，失败 checkpoint 不复用。
- **状态集合**：`queued/running/cancelling/completed/failed/cancelled`；没有 `waiting_approval/reconciling` 或业务动作状态。
- **公平调度**：默认 tenant_round_robin；共享 RunSchedulerState 持久化租户游标并串行原子领取，跨 Worker/重启有效，租户内稳定排序。Redis 只唤醒，数据库只有已成功通知/无会话许可/最早输入/剩余租户容量的 queued Run 可被选中；死信不被迟到通知绕过。所有共享队列 Worker 需同名/同策略；fifo 是显式兼容选项，不具备租户轮转保证。
- **边界**：通知至少一次不表示执行成功；服务租约与执行租约独立。只读已知工具可有限崩溃恢复，旧无 owner 执行需管理员确认停止，未知副作用不能直接重跑；waiting_approval/reconciling 仍待实施，测试验收暂缓。
- **验证入口**：`tests/worker/test_worker.py`、`tests/api/test_streaming.py`；多数测试使用 SQLite/FakeRedis/模型替身。

### M11 — 运行观测、错误诊断与 Analytics：基础已实现

- **入口**：`supportops_core/agent_runtime.py` 的 callback、`worker_runtime.py`、`health_services.py`；API `routes/admin_dashboard.py`；Web `pages/analytics/`。
- **运行指标**：模型/工具调用摘要、Token、缓存/推理 Token、定价估算成本、重试、finish reason、provider request ID、队列/执行/端到端耗时和首输出耗时；缺失信息保留 unknown/null，不伪造零成本或健康。
- **追踪与错误**：Agent 父 span 与模型/工具子 observation、稳定错误事件、元数据白名单、Agent/模型/知识控制面追加审计、分页错误查询与详情/运行时间线。目录/撤销审计与带 attempt 的 ToolInvocation/RunEvent 治理记录已增加。
- **分析接口**：summary/timeseries/runs/errors/trace，系统 Agent 状态和排行；健康由 Agent 版本、近期执行事实与服务租约推导并可持久化为读模型。
- **数据**：`agent_run_observations`、`agent_error_events`、`agent_runtime_health`、`runtime_service_leases`，关联 Run/Event/Audit。
- **健康口径**：24 小时按 Run 创建时间统计完成/失败，取消排除，错误率/P95 至少 3 个样本；活跃请求另按全时段排队和当前有效许可统计，旧 owner/过期/死信/待对账不假装正在执行。共享 degraded Worker 仍存活，说明维护或排空；模型停用、固定工具实现/撤销/Mock flag 可使健康降级。缺读模型不以最近 20 条另造健康或单次失败降级。
- **边界**：当前是应用内观测模型，没有 OpenTelemetry exporter 或独立评测执行平台。Worker 首输出时间不等同于客户端实际收到首文本；多轮 checkpoint 累计消息与本轮调用统计边界需验证。心跳现已与长任务解耦，真实基础设施/停机验收仍暂缓，不可把 Dashboard 当作已完成生产 SLO 验收。
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

以下仅统计 `supportops_core.models` 的 43 张业务表，不统计 checkpoint 扩展表和 Alembic 版本表。

| 数据域 | 数量 | 表 |
| --- | --- | --- |
| 身份与组织 | 3 | `tenants`、`users`、`organization_units` |
| 模型与凭据 | 7 | `model_endpoints`、`model_endpoint_versions`、`model_endpoint_models`、`model_endpoint_test_runs`、`model_credentials`、`model_credential_revisions`、`model_probe_jobs` |
| Agent 与工具控制面 | 6 | `agents`、`agent_drafts`、`agent_versions`、`agent_access_grants`、`tool_catalog_entries`、`agent_audit_events` |
| 知识与证据 | 9 | `knowledge_sources`、`knowledge_documents`、`knowledge_versions`、`knowledge_acl_entries`、`knowledge_snapshots`、`knowledge_outbox_events`、`knowledge_index_generations`、`knowledge_chunks`、`knowledge_evidence` |
| 会话、执行与观测 | 13 | `conversations`、`messages`、`agent_runs`、`run_events`、`run_notification_outbox`、`agent_error_events`、`agent_run_observations`、`agent_runtime_health`、`runtime_service_leases`、`run_attempts`、`tool_invocations`、`model_call_ledger`、`run_scheduler_states` |
| 动作与隔离目标 | 5 | `business_actions`、`action_attempts`、`action_receipts`、`mock_remote_operations`、`mock_support_tickets` |

计数用于当前结构说明，新增表或调整领域职责后应同步更新，不将该数量作为架构限制。

## 5. 核心链路与必须保持的不变量

### 5.1 发布与对话执行

1. 管理员配置模型、密封凭据并测试能力；保存 Agent 草稿、校验、发布、激活和显式授权。
2. 用户经 API 认证，创建固定 Agent 版本会话；发送时再次检查当前 Agent 使用授权。
3. API 锁定会话完成 Message/Run/Event/通知 Outbox 的幂等事务；提交后 API/Worker 有限分发 Run UUID，失败退避/死信并保留 queued 事实。
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
| P0 | 长任务下可靠的服务健康 | 独立心跳/维护/活动计数/监督/停机/持久化租户轮转已有实现，真实验收暂缓 | 后续执行长任务、心跳故障、多 Worker 轮转与停机验收 |
| P0 | 持久化执行恢复 | owner/租约/attempt 与只读有限恢复已实现，验收暂缓 | 后续崩溃/租约竞争验收；副作用先对账 |
| P0 | 同会话串行与上下文隔离 | 输入序号、最早轮次门禁、attempt checkpoint 隔离已实现 | 补完整预算/摘要及并发验收 |
| P0 | 调用时实时授权 | 当前主体/grant/目录策略/撤销/知识 ACL 已复检 | 接入真实业务资源与字段范围 |
| P0 | 配置与执行一致 | 单工具/Run/租户原子许可已有，需审批行为拒绝 | 补审批凭证、动作账本与连接器 |
| P0 | 完整上下文与预算 | 完整输入保守计量/裁剪及 Run/租户物理账本已有 | 补语义摘要、关键事实/冲突策略与正式计量验收 |
| P1 | 可信知识与证据链 | 关键词索引、ACL 检索、evidence 与引用校验已有 | 补证据上下文/冲突处理及评测；向量暂缓 |
| P1 | 结构化产品结果 | 消息保存完整结果，刷新可显示引用/建议/升级意向 | 补真实动作/升级流程查询与验收 |
| P1 | 业务诊断与工单闭环 | 已有 4 个默认关闭的隔离只读 Mock，尚无完整诊断图/工单 | 选择已确认场景；只读业务连接器；结构化证据与幂等建单/升级包 |
| P1 | 工具执行网关与审批 | 本地网关已有，MCP/动作记录/等待状态缺失 | 本地/API/MCP 统一调用边界；条件审批持久化、批准后复检、幂等执行与对账 |
| P1 | 观测与可执行评测 | 有观测和文档基线，无独立质量评测闭环 | 真实 PostgreSQL/队列故障验收；版本化场景评测；事实、引用、授权和业务结果门禁 |
| P2 | 按需专业 Agent、模型路由、长期记忆 | 单 Agent 固定模型，配置预留为空 | 先用评测证明收益，再添加隔离上下文、多模型策略和可纠错/删除记忆 |

额外边界：Run 通知 Outbox 与知识 Outbox 是两条独立链路，通知分发不代替知识索引或执行恢复。软删除与 checkpoint 清理不是个人数据保留/擦除政策的完整实现。Worker 领取、调用及提交边界重查当前身份/权限；在途外部动作仍需专门取消/对账。

未来外部动作应遵循：**动作提案 → 参数/权限/风险校验 → 必要时持久化等待审批 → 执行前复检 → 幂等执行 → 独立结果复核 → 记录结果**。结果未知先对账，不盲目重试；审批绑定参数，参数变化需重新校验。工具目录位于治理侧，全程监控与审计作为横向能力，专业 Agent 不强制拆成微服务。

目录保留 low/medium/high 兼容展示，执行保守映射 R0–R4；风险升级、审批拒绝及撤销由服务端治理，Prompt 不能授权。

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

### 8.1 当前阶段：实现优先，测试任务暂缓

- 知识方案按用户 2026-10-13 选择：暂不启用向量检索，优先关键词检索、权限和证据链；embedding/向量/混合检索任务保持未完成，不能用关键词代替勾选。
- Mock 审批和客服接管按用户纠正后的推荐方案：明确授权人员及其隔离项目范围；管理员角色不自动获得业务处理权限。审批指定非本人，客服处理需独立 `ticket_handle` 授权，不得扩大申请者权限或覆盖知识 ACL。

- 根据用户当前要求，先推进功能实现，暂不编写或执行测试相关任务，包括单元测试、集成测试、E2E、性能测试、故障演练和测试验收；用户明确要求恢复测试时再执行。
- 不主动运行测试套件、`make check` 或仅为交付验收而执行的 lint、类型检查和构建。实现所需的启动、依赖安装与构建仍可按需执行；遇到报错时，执行定位和修复该错误所需的最小范围检查或测试，不自动扩展到全量回归。
- OpenSpec 中纯测试任务暂时跳过并保持未勾选，不删除或标记完成。混合包含实现与测试的任务先完成实现部分，并记录测试部分待处理；整项要求未满足前保持未勾选。
- 保留已有测试、运行时校验和安全约束；测试暂缓不代表可以关闭检查规则、删除有效测试或绕过权限控制。交付时如实说明未执行测试，不将实现完成等同于测试通过。
- 本约定是用户对当前阶段的项目级执行要求，适用于整个仓库；根目录或子目录既有规则中要求修改后立即测试、类型检查或验收构建的条款，当前阶段按本约定暂缓执行，其余开发约定继续适用。

### 8.2 按需使用的验证命令

以下命令供报错排查或用户恢复测试后按范围选用，不作为当前阶段的必执行清单。纯文档改动可核对内容和差异；未运行的检查不得写成通过。

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

补充：关键词证据在模型调用和正文写入边界也复查；输出按完整 JSON 预算裁片，content_is_excerpt 不表示完整原文。引用关联成功工具调用。无证据/超时由服务器生成未确认与追问/升级意向；模型标记冲突须有不同且获准的证据，结果降级，不自选根因。语义正确性尚未正式评测。新增代码未运行测试，所有迁移仍待执行。
