## Context

`establish-enterprise-support-agent-foundation` 已提供租户、用户、会话、消息、Agent Run、Run Event、JWT 身份、Redis 队列、Worker 和 React Chat，但系统只有一个隐式的确定性占位执行器。当前 `/chat` 与 `/chat/c/:conversationId` 已经是“某个具体 Agent 的员工使用工作台”，只是这个 Agent 尚未被显式建模；创建会话不选择 Agent，Worker 也没有可追溯的配置版本。后续若直接在 Prompt、LangGraph、知识或工具模块中各自保存配置，会失去统一的发布、授权、回滚和审计边界。

本 change 同时影响数据库、API、Worker 和 Web，是控制面与运行面的交叉改造。它遵循产品基线的租户隔离、最小权限和确定性服务控制原则。虽然数据模型与目录允许一个租户配置多个 Agent，但一次会话只绑定一个 Agent，不实现 Agent 间自治编排；P0 仍可只发布一个 AI/API 支持 Agent。

利益相关者包括平台管理员、员工/最终用户、后续知识管理员、工具管理员、审计员，以及维护 API、Worker 和 Web 的开发者。

## Goals / Non-Goals

**Goals:**

- 建立 Agent 从草稿、校验、发布、激活、回滚到停用的完整生命周期。
- 让每次会话和 Run 都能回答“使用了哪个 Agent、哪个不可变版本、由谁在何时发布”。
- 提供服务端强制的租户隔离、管理权限和 Agent 使用授权。
- 提供管理员控制台和员工 Agent 目录，打通配置到对话的纵向链路。
- 独立管理 OpenAI 官方与 OpenAI-compatible 中转站模型端点、密钥引用、能力和连接状态。
- 将 Agent 基础信息与版本化运行配置分离，并允许 Agent 绑定已验证模型和已注册工具。
- 使用 LangChain `create_agent` 和 LangGraph 打通真实模型、工具循环、结构化输出、持久化检查点和流式事件。
- 为知识库、更多业务工具、审批和长期记忆提供稳定、可扩展、版本化的配置入口。

**Non-Goals:**

- 不设计可视化任意 LangGraph 图编程器；运行图采用平台维护的 `create_agent` 模板。
- 不允许管理员上传或执行任意 Python 工具代码；工具只能来自受控注册目录。
- 不实现知识摄取、向量检索、长期记忆读写或完整工具网关管理面。
- 不支持任意供应商私有协议；首版只支持 OpenAI 官方和符合 OpenAI API 的兼容端点。
- 不接入企业 OIDC/SSO，不实现组织目录同步；继续复用可信 JWT Principal。
- 不实现 Agent 之间的委派、协作、自动路由或自治编排。
- 不实现工单、人工接管、审批和高风险业务写操作。
- 不把 Prompt 或 Agent 配置当作权限、安全策略或工具授权的替代品。

## Current Baseline and Target Architecture

本 change 不推倒或重写现有聊天交互。现有会话列表、消息提交、SSE 事件和 Worker 队列继续作为运行面骨架；页面改为由路由中的 Agent ID 驱动，并在其外建立模型管理、Agent 控制面、版本边界和员工目录。

```text
管理员  /admin/models ──▶ ModelEndpoint / Version / SecretRef
              │                        │
              │ 选择已验证模型          │ 固定模型端点版本
              ▼                        ▼
       /admin/agents ───────▶ Agent / Draft / Version / Grant / Audit
                                      │
                                      │ 发布并激活不可变 AgentVersion
                                      ▼
员工  /agents ──选择 Agent──▶ /agents/:agentId/chat
                                      │ 首次发言创建会话
                                      ▼
                         /agents/:agentId/chat/c/:conversationId
                                      │
                                      │ Conversation/Run 固定 AgentVersion
                                      ▼
                       ┌────────────────────────────────┐
                       │ Shared Worker                  │
                       │ resolve model + tools + prompt │
                       │ LangChain create_agent         │
                       │ LangGraph Postgres checkpointer│
                       └────────────────────────────────┘
```

规范入口是 `/agents/:agentId/chat`，具体会话是 `/agents/:agentId/chat/c/:conversationId`。旧 `/chat` 与 `/chat/c/:conversationId` 路由直接删除，不提供别名、重定向或默认 Agent 解析；应用根路由 `/` 进入 `/agents`。具体会话 API 必须校验路由 `agentId` 与 `Conversation.agent_id` 一致，避免伪造 URL 混淆上下文。

不采用裸 `/:agentId/chat`：它会占用顶级动态段，与 `/admin`、`/agents` 及未来 `/settings` 等路径产生匹配和演进冲突。`/agents/:agentId/chat` 多一个稳定命名空间，但路由语义、权限守卫和日志检索都更清晰。

### Control Plane and Runtime Plane Boundary

- 控制面负责模型端点、Agent 资料、草稿编辑、配置校验、版本发布、激活、停用、授权和审计。
- 运行面负责会话、消息、Run、事件流和共享 Worker 执行。
- 控制面只发布声明式配置，不直接启动独立进程或执行用户请求。
- 运行面只读取不可变 `AgentVersion` 与其固定的 `ModelEndpointVersion`；不能读取草稿，也不能把请求内配置当作可信配置。
- LangGraph、知识检索、工具和长期记忆作为版本化绑定接入，而不是绕开 AgentVersion 建立第二套配置来源。

## Decisions

### 1. 分离 Agent 基础资料、可编辑运行草稿和不可变发布版本

采用以下核心模型：

- `Agent`：租户内稳定身份、唯一 slug、生命周期状态、动态基础资料和当前激活版本。
- `AgentDraft`：每个 Agent 当前可编辑配置，包含单调递增 revision，用于乐观并发控制。
- `AgentVersion`：发布时生成的不可变配置快照、版本号、配置 Schema 版本、内容摘要和发布者。
- `AgentAccessGrant`：Agent 对主体或角色的使用授权。
- `AgentAuditEvent`：追加写的控制面变更证据。

Agent 基础资料包括 `name`、`logo_asset_id`、`description`、`welcome_message` 和可选建议问题，保存于 `Agent`/`AgentProfile` 关系字段，修改后立即用于目录和聊天展示，但必须审计。基础资料不参与模型执行；历史会话默认显示 Agent 当前品牌资料。

运行配置使用带 `schema_version` 的 JSONB 保存需要整体复现的 Prompt、模型绑定、工具绑定、输出策略和运行限制。发布时解析并固定所选 `ModelEndpointVersion` 和 ToolDefinition 版本，不允许 Worker 任意解释未知字段。运行行为只能通过发布并激活新 AgentVersion 改变。

推荐的首版关系字段如下：

| 模型 | 关键字段 | 约束与用途 |
|---|---|---|
| `agents` | `id`, `tenant_id`, `slug`, `name`, `logo_asset_id`, `description`, `welcome_message`, `status`, `active_version_id` | `(tenant_id, slug)` 唯一；每个 Agent 独立进入自己的聊天路由 |
| `model_endpoints` | `id`, `tenant_id`, `name`, `logo_asset_id`, `status`, `active_version_id` | Agent 编辑器可选择的稳定模型资源 |
| `model_endpoint_versions` | `endpoint_id`, `version_number`, `provider_kind`, `api_protocol`, `base_url`, `remote_model_name`, `capabilities`, `defaults`, `secret_ref_id` | 非秘密配置不可变；AgentVersion 固定引用 |
| `model_credentials` | `id`, `tenant_id`, `provider`, `secret_locator`, `masked_hint`, `revision` | 明文只写入 Secret Provider；API 不可读回 |
| `agent_drafts` | `agent_id`, `revision`, `schema_version`, `config`, `updated_by` | 每个 Agent 一份当前草稿；revision 单调递增 |
| `agent_versions` | `agent_id`, `version_number`, `schema_version`, `config`, `config_digest`, `published_by` | `(agent_id, version_number)` 唯一；发布后不可修改 |
| `agent_access_grants` | `agent_id`, `subject_type`, `subject_id`, `created_by` | 主体首版为 `user` 或 `role`；租户内唯一 |
| `agent_audit_events` | `agent_id`, `event_type`, `actor_user_id`, `metadata`, `correlation_id` | 追加写，不存储秘密或完整系统指令 |
| `conversations` | 新增 `agent_id`, `agent_version_id` | 会话创建时固定版本 |
| `agent_runs` | 新增 `agent_id`, `agent_version_id`, `model_endpoint_version_id`, `config_digest` | 执行与审计的最终依据 |

替代方案是直接修改一行 Agent 配置。它更简单，但无法审计历史 Run、可靠回滚或复现实验结果。另一个方案是所有字段完全关系化；它在后续配置快速演进时会产生大量耦合迁移，因此采用关系字段加版本化快照的混合方式。

### 2. 发布与激活分离，并把“启动 Agent”定义为激活版本

发布操作在事务中校验草稿、分配递增版本号、生成规范化配置摘要并写入不可变 `AgentVersion`。激活操作将 `Agent.active_version_id` 切换到指定已发布版本；回滚就是重新激活历史版本。UI 可以提供“发布并激活”的组合操作，但服务端仍保留两个明确状态转换。

Agent 不拥有独立常驻进程。共享 Worker 根据 Run 固定的版本加载配置，因此“启动”意味着 Agent 处于 active 状态且存在激活版本。停用后不再出现在员工目录，不允许创建新会话或提交新消息；已经运行中的 Run 不被隐式终止，管理员仍可使用既有取消能力处置。

替代方案是每个 Agent 启动独立进程。该方式会造成资源浪费、部署数量随配置增长，并使版本切换与故障恢复复杂化，首期不采用。

### 3. 草稿修改使用 revision 乐观并发控制

管理员读取草稿时获得 revision，保存时必须提交期望 revision。服务端以条件更新保证只有最新 revision 能成功，冲突返回稳定错误码和服务端当前 revision。客户端保留本地内容并提示刷新或对比，不静默覆盖另一位管理员的修改。

替代方案是数据库悲观锁或最后写入获胜。前者不适合跨页面长时间编辑，后者会丢失企业配置修改。

### 4. Agent 授权由服务端默认拒绝并与租户绑定

平台管理员角色可以管理当前租户内 Agent。员工使用权由 `AgentAccessGrant` 明确授予，首版支持用户主体和角色主体；所有授权目标必须属于或来源于当前租户的可信身份上下文。目录和详情 API 都应用相同过滤，客户端隐藏不是安全边界。

后续企业身份 change 可以在不改变 Agent 授权服务接口的情况下增加用户组主体。模型、Prompt、请求体中的角色或 tenant_id 不参与授权决策。

替代方案是租户内全部 Agent 默认可见。它便于演示，但无法满足部门专属 Agent、灰度发布和敏感知识边界，因此不采用。

### 5. 会话和 Run 双重固定 Agent 版本

创建会话请求必须携带路由中已选择的 `agent_id`。服务端在同一事务内校验 Agent active、存在激活版本且当前主体有使用权，然后将 `agent_id` 和 `agent_version_id` 写入会话。该会话后续消息始终沿用固定版本；发布新版本不会改变已有会话，员工需要创建新会话才使用新版本。

创建 Run 时再次复制 `agent_id`、`agent_version_id` 和该版本固定的 `model_endpoint_version_id`，Worker 只按 Run 上的版本 ID 加载快照。这样即使 Agent 或模型端点随后发布新版本，历史执行仍可审计。Run 事件可以携带非敏感的 Agent/模型版本标识，但不得暴露完整系统指令或秘密引用。

替代方案是 Worker 每次读取 Agent 当前版本。它会让同一会话在管理员发布后无提示改变行为，并使重放结果不稳定，因此不采用。

### 6. 控制面 API 与运行面 API 分区

模型管理接口使用 `/v1/admin/model-endpoints` 前缀，Agent 管理接口使用 `/v1/admin/agents` 前缀。员工接口使用 `/v1/agents` 提供授权目录和详情。既有 `/v1/conversations` 接口增加必填 `agent_id`，不保留任何隐式 Agent 选择。Run 查询继续复用现有路径并扩展 Agent 与模型版本元数据。

API 返回稳定的字段级校验问题和冲突错误码。所有控制面写操作生成关联 ID 并追加审计事件，审计内容记录变更类型、主体、目标和前后版本标识，不记录 JWT、数据库连接串或未来工具凭据。

### 7. 单一 Web 应用提供角色化管理控制台和员工工作台

继续使用现有 React/Vite 应用，增加基于服务端能力判断的导航和路由：

- `/admin/agents`：Agent 列表和状态。
- `/admin/agents/:id`：草稿编辑、校验、版本、发布、激活、授权和审计摘要。
- `/admin/models`：模型端点列表、创建、连接测试、版本和停用。
- `/admin/models/:id`：模型端点资料、域名/协议、远端模型名称、能力、参数和密钥轮换。
- `/agents`：员工可用 Agent 目录。
- `/agents/:agentId/chat`：所选 Agent 的新建对话状态，首次发言后创建会话并进入会话 URL。
- `/agents/:agentId/chat/c/:conversationId`：具体会话工作台。
- `/`：进入员工 Agent 目录 `/agents`。

首次发送消息时，Web 先创建固定 AgentVersion 的 Conversation，再立即切换到 `/agents/:agentId/chat/c/:conversationId` 并保持乐观消息与流式状态，不能等待完整回答后才切页。删除当前会话后回到 `/agents/:agentId/chat`。

`AgentChatLayout` 必须先根据路由 `agentId` 加载公开 Agent 资料，再渲染侧栏品牌、页面标题、空状态 Logo、Agent 名称、描述、欢迎语和建议问题，不保留硬编码 `SupportOps` 文案。加载期间使用稳定骨架，避免先闪现旧版硬编码资料。左侧“最近”按当前 Agent 过滤；用户主动回到 `/agents` 才切换 Agent。管理入口可根据角色隐藏，但 API 权限校验始终执行。

替代方案是立即拆成两个前端应用。当前规模下会重复身份、API 客户端和设计系统，待独立部署或组织边界出现后再拆分更合理。

### 8. 控制面审计与 Run Event 分开保存

`RunEvent` 描述一次运行过程，`AgentAuditEvent` 描述配置和授权变更，两者生命周期、查询主体和保留策略不同。控制面审计采用追加写，应用不提供更新或删除接口。二者共享 correlation ID，未来可以在审计查询中串联“版本发布—员工选择—具体 Run”。

### 9. 迁移现有会话到同租户迁移 Agent

数据库迁移先创建模型和 Agent 表及可空关联，并只为实际存在无 Agent 历史数据的租户创建 `supportops-migrated` 迁移 Agent 和只读迁移版本。历史会话与 Run 关联到该版本，但不得伪造曾经发生过真实模型调用。新租户不自动创建 Agent，必须由管理员创建、配置、发布和授权。

开发环境可以没有历史数据，但迁移仍按可保留数据设计。若回滚应用版本，保留新表和关联数据，仅恢复兼容读取路径；除非已确认无新增数据，不执行删除表或清空版本历史的自动回滚。

### Configuration Ownership

| 配置层 | 管理字段 | 不应放入该层的内容 |
|---|---|---|
| ModelEndpoint 基础资料 | 名称、Logo、状态、备注 | Agent Prompt、用户权限 |
| ModelEndpointVersion | 官方/中转站类型、协议、Base URL、远端模型名、组织/项目标识、受控 Header、能力、上下文窗口、参数范围、限流/并发、可选价格元数据 | API Key 明文、业务 Prompt |
| ModelCredential | API Key、受控秘密 Header、credential revision | Base URL、Agent 配置、可读回秘密 |
| Agent 基础资料 | 名称、Logo、slug、描述、欢迎语、建议问题 | Prompt、模型密钥、工具实现 |
| AgentVersion | system prompt、主模型绑定、工具绑定、生成参数、输出策略、运行上限、未来知识/记忆引用 | 明文密钥、任意 Python/JSON Schema |
| 平台强制策略 | RBAC/ABAC、Secret Provider、checkpointer、SSRF 防护、硬性调用/成本上限、PII、HITL、审计 | 可被 Agent Prompt 或管理员关闭的开关 |

首版不启用自动 fallback 模型，以免在未验证能力一致性时悄悄改变回答质量和工具语义；Schema 可预留 `fallback_model_endpoint_ids`，后续启用时必须固定每个 fallback 的端点版本、验证工具/结构化输出兼容性，并在 Run 中记录实际选中的模型。

### 10. Agent 运行配置采用严格的版本化契约

Agent 基础资料不进入运行 JSON。首版运行配置采用以下逻辑结构；引用的精确版本由发布服务解析后写入 AgentVersion，客户端不能自行指定：

```json
{
  "schema_version": "2",
  "prompt": {
    "system_prompt": "你是企业 AI/API 技术支持 Agent……"
  },
  "model": {
    "model_endpoint_id": "uuid-selected-by-admin",
    "fallback_model_endpoint_ids": [],
    "generation": {
      "temperature": 0.2,
      "max_output_tokens": 4096,
      "timeout_seconds": 60,
      "max_retries": 2
    }
  },
  "tools": [
    {
      "tool_id": "support_ticket_lookup",
      "enabled": true,
      "max_calls_per_run": 4,
      "approval_policy": "none"
    }
  ],
  "runtime": {
    "engine": "langchain_create_agent_v1",
    "context_schema": "support_context_v1",
    "response_schema": "support_answer_v1",
    "response_strategy": "tool",
    "checkpointer": "postgres",
    "model_call_limit": 6,
    "tool_call_limit": 8,
    "run_timeout_seconds": 120,
    "max_parallel_tools": 2
  },
  "knowledge": {
    "knowledge_base_ids": []
  },
  "memory": {
    "long_term_policy_id": null
  }
}
```

首版启用 `prompt`、`model`、受控 `tools` 和 `runtime`；`knowledge` 与长期 `memory` 只能为空，留给独立 change。模型 temperature、最大输出、超时和重试是 Agent 级覆盖，但必须落在 ModelEndpointVersion 声明的允许区间内。服务端拒绝未知字段、不可用模型、能力不兼容、未注册工具、跨租户引用、危险审批策略或超出平台上限的调用额度。

`context_schema` 和 `response_schema` 只能选择平台代码注册的稳定标识，不能从管理 UI 上传 Python 类型或任意 JSON Schema。可信用户、租户、角色、会话和 correlation ID 通过 `SupportContext` 在调用时注入，不进入模型可自由填写的工具参数。

### 11. 模型端点独立管理并版本化

一个 `ModelEndpoint` 表示 Agent 编辑器里可选择的模型资源，包含动态名称、Logo 和状态；每次保存有效连接配置产生不可变 `ModelEndpointVersion`。版本字段包括：

- `provider_kind`：`openai_official` 或 `openai_compatible`。
- `api_protocol`：`responses` 或 `chat_completions`；兼容中转站默认 `chat_completions`。
- `base_url`：官方端点使用平台预设值；中转站必须显式填写并规范化。
- `remote_model_name`：传给供应商的真实模型标识，例如供应商暴露的 model ID。
- `capabilities`：streaming、tool calling、structured output、parallel tool calls、vision 等经过验证的能力。
- `defaults/limits`：temperature、max output、timeout、retry、上下文窗口、请求/Token 限流、最大并发和允许覆盖范围。
- `request_metadata`：可选 OpenAI organization/project 标识及受控 Header 引用；禁止管理员注入 Host、Content-Length 或任意转发 Header。
- `pricing`：可选输入/输出 token 单价和币种，用于预算估算；缺失时只能执行调用次数/Token 上限，不能伪造成本。
- `secret_ref_id`：只指向凭据，不保存 API Key 明文。

API Key 写入专用 Secret Provider；首期本地部署可以使用由环境主密钥进行 envelope encryption 的数据库实现，生产环境通过统一接口切换到云 Secret Manager。读取接口只返回是否已配置、脱敏尾号、凭据 revision 和最近轮换时间。轮换 Key 不要求重新发布 Agent，但 Run 要记录实际使用的 credential revision；修改域名、协议、模型名称或能力必须创建新的 ModelEndpointVersion，并由新 AgentVersion 显式采用。

连接测试分为基础连通和能力验证：先验证 TLS、认证与模型可用，再用无业务数据的最小请求验证 streaming、工具调用和结构化输出。`GET /models` 不是所有中转站都可靠支持，不能把它作为唯一验证方式。中转站 URL 必须实施 HTTPS、DNS/IP 解析、私网与保留地址拒绝、重定向限制和出口 allowlist；仅本地开发模式可显式允许 localhost。

### 12. `create_agent` 参数由受控配置映射，不暴露任意代码

Worker 中的 AgentFactory 将不可变配置映射为 LangChain `create_agent`：

| `create_agent` 参数 | 配置来源 | 设计约束 |
|---|---|---|
| `model` | 固定的 ModelEndpointVersion + SecretRef | 通过模型适配器构造，官方与兼容端点不泄露差异到业务层 |
| `tools` | AgentVersion 工具绑定 + 当前用户权限过滤 | 只来自服务端 Tool Registry，不执行 UI 上传代码 |
| `system_prompt` | AgentVersion.prompt | 发布前校验并做秘密检测 |
| `context_schema` | 固定注册项 `support_context_v1` | 可信上下文由运行时注入，模型不能覆盖 |
| `checkpointer` | 平台配置 `postgres` | 生产不使用 `InMemorySaver`；`thread_id` 由 tenant + conversation ID 派生 |
| `response_format` | `support_answer_v1` + strategy | 中转站优先 `ToolStrategy`；仅验证支持时允许 ProviderStrategy |
| `middleware` | Agent limits + 平台强制策略 | 调用上限、超时、重试、PII、审批和可观测性不可被 Prompt 关闭 |
| `name` | 稳定 Agent slug 派生 | 用于图标识、追踪和指标，不允许冲突 |

用户示例中的 `model_call_limit=6` 和 `tool_call_limit=8` 作为默认值保留，但成为有平台上下限的 Agent 配置。建议同时增加总运行超时、最大并行工具数、模型/工具重试预算、token/成本预算、上下文摘要阈值、PII 防护以及高风险工具 Human-in-the-Loop；其中安全中间件由平台强制，管理员只能在允许范围内收紧。

LangGraph PostgreSQL checkpointer 保存图执行状态和工具消息，现有 `messages` 表仍是用户可见会话的产品事实来源。二者使用 `conversation_id`/`run_id` 关联：每轮只把新的用户输入提交给图，最终可见回答和必要状态写回 `messages`，内部工具轨迹按独立保留策略保存，避免把两套消息历史无约束地相互覆盖。

## API Contract

建议的 API 边界如下，具体分页与错误信封沿用现有 API 约定：

### 管理员控制面

| Method | Path | 用途 |
|---|---|---|
| `GET/POST` | `/v1/admin/model-endpoints` | 查询或创建租户模型端点 |
| `GET/PATCH` | `/v1/admin/model-endpoints/{endpoint_id}` | 查询或更新模型展示资料 |
| `POST` | `/v1/admin/model-endpoints/{endpoint_id}/versions` | 保存新的不可变连接/模型配置版本 |
| `GET` | `/v1/admin/model-endpoints/{endpoint_id}/versions` | 查询模型端点版本历史 |
| `PUT` | `/v1/admin/model-endpoints/{endpoint_id}/credential` | 写入或轮换密钥，只返回脱敏结果 |
| `POST` | `/v1/admin/model-endpoints/{endpoint_id}/test` | 执行连通性与能力验证 |
| `POST` | `/v1/admin/model-endpoints/{endpoint_id}/disable` | 停用并阻止新 Agent 版本绑定 |
| `GET` | `/v1/admin/agents` | 查询本租户 Agent 列表与状态 |
| `POST` | `/v1/admin/agents` | 创建 Agent 及 revision 1 草稿 |
| `GET` | `/v1/admin/agents/{agent_id}` | 查询详情、激活版本和草稿摘要 |
| `GET` | `/v1/admin/agents/{agent_id}/draft` | 读取草稿和 revision |
| `PATCH` | `/v1/admin/agents/{agent_id}/draft` | 按 expected revision 更新草稿 |
| `POST` | `/v1/admin/agents/{agent_id}/draft/validate` | 返回字段级校验结果，不发布 |
| `POST` | `/v1/admin/agents/{agent_id}/versions` | 从指定 revision 发布不可变版本 |
| `GET` | `/v1/admin/agents/{agent_id}/versions` | 查询版本历史 |
| `POST` | `/v1/admin/agents/{agent_id}/versions/{version_id}/activate` | 激活或回滚到已发布版本 |
| `POST` | `/v1/admin/agents/{agent_id}/disable` | 停用 Agent，阻断新工作 |
| `GET/PUT` | `/v1/admin/agents/{agent_id}/grants` | 查询或替换授权集合 |
| `GET` | `/v1/admin/agents/{agent_id}/audit-events` | 查询控制面审计摘要 |

创建、保存、发布、激活、停用、密钥轮换和授权均需幂等或带预期状态。冲突使用 `409` 和稳定错误码；字段配置错误使用 `422`；跨租户、无授权或资源不可见遵循统一的资源隐藏策略，避免枚举资源 ID。任何响应均不得返回 API Key、Authorization Header 或可用于还原秘密的配置。

### 员工运行面

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/v1/agents` | 返回当前主体有权使用的 active Agent |
| `GET` | `/v1/agents/{agent_id}` | 返回可公开的名称、Logo、描述、欢迎语、建议问题和激活版本号 |
| `GET` | `/v1/conversations?agent_id=...` | 返回当前用户在指定 Agent 下的最近会话 |
| `POST` | `/v1/conversations` | 使用 `agent_id` 创建并固定会话版本 |
| `GET` | `/v1/conversations/{conversation_id}` | 返回消息、动态 Agent 资料及固定 Agent/模型版本元数据 |
| `POST` | `/v1/conversations/{conversation_id}/messages` | 在固定版本上创建 Run |

员工 API 不返回 system prompt、完整配置快照、授权主体列表或未来的知识/工具内部引用。

## Runtime Sequence

```text
Employee      Web                 API/DB                 Queue/Worker
   │           │                    │                          │
   │ 打开 Agent URL                 │                          │
   ├──────────▶│ GET agent          │                          │
   │           ├───────────────────▶│ 校验租户/授权/active     │
   │ 首次发言  │                    │                          │
   ├──────────▶│ POST conversation(agent_id)                     │
   │           ├───────────────────▶│ 固定 AgentVersion        │
   │           │ POST message      │                          │
   │           ├───────────────────▶│ Run 复制模型端点版本      │
   │           │                    ├─────────────────────────▶│
   │           │                    │ 解析 SecretRef 与 Tools  │
   │           │                    │ create_agent + checkpoint│
   │           │◀───────────────────┴──── SSE Run Events ──────┤
   │◀──────────┤ 增量渲染回答                                  │
```

每次提交消息时重新检查 Agent 未停用且用户仍有授权；撤销授权或停用后，历史会话可读但不能继续发送。Worker 已取得的 Run 按固定版本执行，除非通过现有取消机制显式终止。此规则既保持执行可复现，也避免停用操作隐式制造半完成事务。

## Delivery Slices

本 change 的实现顺序应保持每一阶段可验证：

1. **数据与秘密契约**：ModelEndpoint/Version/Credential、Agent/Draft/Version/Grant/Audit 模型、Secret Provider 接口和数据库约束。
2. **模型端点管理**：官方/中转站配置、密钥轮换、连接与能力测试、停用和审计。
3. **Agent 生命周期**：基础资料、运行草稿、并发保存、模型/工具校验、发布、激活、回滚、授权和审计。
4. **绑定现有运行链路**：Conversation/AgentRun 固定 Agent 与模型版本，AgentFactory 构造 `create_agent` 并接入 PostgreSQL checkpointer。
5. **改造聊天路由与动态品牌**：规范 Agent 路由、旧 URL 重定向、Agent 维度最近会话、刷新恢复和首次消息平滑切换。
6. **增加员工 Agent 目录**：只展示授权且 active 的 Agent，所有 Agent 均能进入自己的聊天工作台。
7. **增加管理控制台**：模型管理、Agent 基础资料、运行配置、版本、授权与审计。
8. **端到端验证**：配置模型 → 测试连接 → 创建 Agent → 绑定 Prompt/模型/Tools → 发布授权 → 员工对话 → 新版不影响旧会话 → 轮换密钥 → 撤权/停用。

知识检索、更多受控业务工具、长期记忆和审批仍应拆成独立 change，但统一消费 AgentVersion 中的绑定，不再修改模型凭据或聊天路由基础结构。

## Risks / Trade-offs

- [多 Agent 数据模型可能被误解为 P0 多 Agent 自治编排] → 明确一次会话只绑定一个 Agent，P0 默认只发布一个，跨 Agent 协作不在本 change。
- [管理员希望配置发布立即影响已有会话] → UI 明示会话固定版本，并提供“使用最新版本新建会话”入口，不静默升级。
- [JSONB 配置随能力增加发生 Schema 漂移] → 每个版本携带 `schema_version`，发布前执行严格验证，Worker 拒绝不支持的 Schema。
- [用户或角色授权无法覆盖企业组织结构] → 首版抽象主体类型并默认拒绝，后续增加用户组适配，不用自由文本组名绕过身份系统。
- [管理 UI 隐藏造成权限安全错觉] → 所有授权在 API 服务端执行并增加越权、跨租户和资源枚举测试。
- [停用 Agent 时仍有运行中的 Run] → 停用阻断新工作但不隐式破坏事务，运行中任务通过显式取消和审计处置。
- [Worker 每次读取版本增加数据库查询] → 首期优先一致性；后续可按不可变版本 ID 安全缓存，并以版本摘要校验。
- [中转站 URL 可形成 SSRF 或数据外传通道] → 强制 HTTPS、解析后 IP 检查、重定向限制、出口 allowlist、连接测试审计；开发 localhost 必须显式开启。
- [模型能力由管理员误报导致工具或结构化输出失败] → 发布时要求最近一次能力测试，并在运行时失败关闭，不自动降级到不受控模式。
- [API Key 泄露到 Agent 配置、日志或浏览器] → 独立 Secret Provider、写入后不可读、统一日志脱敏、前端只显示 masked hint。
- [模型端点修改破坏旧 Agent 可复现性] → 域名、协议、模型名和能力变更生成不可变端点版本，AgentVersion 固定引用；只有凭据轮换走稳定 SecretRef。
- [系统指令中可能被管理员粘贴秘密] → UI 和 API 明示禁止，增加敏感模式校验；工具和模型凭据只保存秘密引用，不进入版本快照或日志。
- [产品消息和 LangGraph checkpoint 双写发生偏差] → 明确产品消息为用户可见事实源、checkpoint 为内部执行状态，使用 Run 事务边界、关联 ID 和一致性修复任务。

## Migration Plan

1. 新增 ModelEndpoint、版本、Credential 引用、Agent、草稿、版本、授权和审计表，以及会话/Run 的可空版本关联。
2. 建立 Secret Provider 和本地加密迁移，不从环境变量或旧配置自动复制明文密钥到普通业务表。
3. 仅为包含旧数据的租户建立只读迁移 Agent；回填历史会话与 Run 并验证租户一致性。
4. 管理员创建并验证真实模型端点与业务 Agent 后，再建立新数据的非空与状态约束。
5. 部署模型/Agent 管理 API、LangGraph Worker 和员工目录接口，验证权限、跨租户拒绝、模型失败和秘密脱敏。
6. 部署规范 Agent 路由并直接移除旧 `/chat` 路由；应用根路径进入 `/agents`。
7. 完成真实 PostgreSQL 数据迁移、checkpointer、密钥轮换、回滚演练、API/Worker 集成测试和浏览器关键流程验证。

回滚时优先回滚 Web 和 API 路由，同时保留 Agent 表、版本历史和回填关联。只有在确认新版本未产生控制面数据后，才允许通过独立迁移删除新增结构。

## Open Questions

- 首版 Agent 使用授权是否只支持角色，还是同时支持单个用户；本设计默认两者均支持。
- “平台管理员”的规范角色标识需要在实施前与现有开发令牌角色约定统一。
- 发布是否默认立即激活；本设计保留分离语义，UI 可以提供显式勾选的组合动作。
- 停用 Agent 后已有会话是否允许只读查看；本设计默认允许查看历史，但禁止发送新消息。
- 生产 Secret Provider 选择本地 envelope encryption 还是外部 Secret Manager；接口保持一致，本地开发默认前者。
- 首版中转站是否只开放管理员 allowlist 内域名；本设计建议生产环境必须 allowlist，开发环境可显式放宽。
- `response_strategy` 默认 `tool` 还是根据已验证能力自动选择；本设计建议中转站默认 `tool`，官方端点可在验证后选择 `provider`。

## Technical References

- [LangChain Models](https://docs.langchain.com/oss/python/langchain/models)：统一模型接口、OpenAI-compatible `base_url` 和供应商适配边界。
- [LangChain Agents](https://docs.langchain.com/oss/python/langchain/agents)：`create_agent` 的模型—工具循环、动态工具和结构化输出。
- [LangChain Runtime](https://docs.langchain.com/oss/python/langchain/runtime)：通过 `context_schema` 注入可信运行上下文。
- [LangChain Short-term Memory](https://docs.langchain.com/oss/python/langchain/short-term-memory)：生产使用持久化 checkpointer，而不是 `InMemorySaver`。
- [LangChain Middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)：模型/工具调用上限、重试、PII、Human-in-the-Loop 和上下文管理。
