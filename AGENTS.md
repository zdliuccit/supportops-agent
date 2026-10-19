# AGENTS.md — SupportOps Agent 项目工作指南

本文适用于整个仓库，供 AI 编码助手和项目贡献者建立实现上下文。`apps/api/AGENTS.md`、`apps/web_chat/AGENTS.md` 补充各自目录的约定；修改相应目录前应阅读这些文件，并按指令优先级与作用域执行。

实现快照日期：**2026-10-17**。以当前代码为事实来源，包含当前工作区已有的未提交实现；这里的“已实现”表示存在实现，不表示本次文档更新重新运行了全部测试，也不表示已经通过生产验收。

## 1. 产品定位与当前阶段

SupportOps Agent 面向企业员工、客服和技术支持，目标是完成“理解问题 → 获取知识与业务证据 → 诊断 → 回答或升级 → 受控业务动作 → 结果复核”的支持闭环。

当前已形成 **企业 Agent 控制面 + 员工聊天工作台 + 单 Agent 异步执行底座**，具备关键词知识证据链、隔离 Mock 429 诊断与 R2 动作账本/独立复核。隔离 Mock 持久化审批、显式人员/项目/上下文授权、业务等待与客服接管工作台已接通；真实业务连接器、外部工单、远程 MCP 仍待接入。

- 当前部署边界：**3 个应用 + 1 个共享 Python 核心包**，属于 API 与 Worker 分进程的模块化单体。
- 本文按职责划分 **12 个逻辑能力模块（M01–M12）**。模块不等于目录、微服务或独立 Agent；共享文件可以承载多个相关模块。
- `models.py` 当前定义 **66 张业务 ORM 表**；LangGraph checkpoint 表不计入。新增治理、知识、结果、审核、调度、探测、动作、客服、告警、登录会话、发布评测、保留预览、导入账本与工具发现结构需依次应用 `20261012_0018` 至 `20261019_0051`；本次未执行迁移，已部署库数量取决于迁移状态。升级 0028 前须停止旧 API 的 BackgroundTasks 探测者，新 Worker 只领取持久化探测。
- [2026 修订参考架构图](architecture-diagrams/enterprise-support-agent-architecture-2026-10-12.png) 是目标设计，不能当作当前功能完成清单。

## 2. 技术栈、目录与依赖方向

| 单元 | 技术与职责 | 主要入口 |
| --- | --- | --- |
| `apps/web_chat` | React 19、TypeScript、Vite、React Router、Tailwind、shadcn/Radix、Redux Toolkit、Recharts | `src/router/index.tsx`、`src/api.ts`、`src/types.ts` |
| `apps/api` | FastAPI、Pydantic、JWT 身份依赖、REST、Fetch SSE 所需事件接口 | `src/supportops_api/main.py`、`routes/` |
| `apps/agent_worker` | Redis 消费、Agent Run 执行调度、服务心跳、恢复扫描与周期清理 | `src/supportops_worker/main.py` |
| `packages/support_core` | 身份、配置、ORM、领域服务、模型适配、Agent 与 Worker 执行实现 | `src/supportops_core/` |
| `migrations` | Alembic 业务结构与数据迁移、字段注释、软删除迁移 | `versions/`、`env.py` |
| `deploy` | 本地 PostgreSQL/pgvector、Redis Compose；API/Worker/Web 独立构建/运行与显式迁移模板 | `docker-compose.yml`、`runtime/README.md` |
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
- **现有页面**：登录、本人登录会话与数据申请；授权 Agent 目录；Agent/模型列表与详情（含版本门禁和灰度控制）；工具目录；公司、用户、部门；知识列表与详情、本人管理员导入任务；审批工作台、隔离Mock工单/客服接管；Analytics 概览、排名、Agent 状态/详情、运行、错误与告警；普通和独立聊天页面。
- **路由事实**：管理页面采用 `/agent-management/*`、企业页面采用 `/enterprise/*`、分析页面采用 `/analytics/*`。根路径 `/` 转到 `/agents`，未登录由路由守卫进入 `/login`；聊天为 `/agents/:agentId/chat` 及其 `/c/:conversationId` 子路由，独立模式增加 `/standalone`。
- **交互实现**：详情页复用页面专属 hooks/组件；聊天通过 Fetch ReadableStream 读取 SSE，直接合并 `text.delta`、替换 `text.replace`；恢复快照后从游标继续订阅。公司资料驱动品牌展示，部门树使用全局 Redux slice。
- **身份存储**：访问令牌仅保存在 sessionStorage，旧 localStorage 令牌清理且不迁移；localStorage 仅传递不含令牌的同源注销信号。默认 60 分钟、5–480 分钟可配，到期重新登录，无 refresh-token。`withAuthenticatedToken` 处理对应令牌的 401，旧 helper 名称仅兼容；迟到响应不能清掉新登录，不重放业务写请求。守卫在聚焦/可见及每分钟复检，网络错误阻止业务展示但保留重试；`/account/sessions` 支持本人分页查询/撤销。sessionStorage 可被同源脚本读取，不代表防止 XSS 或阻止有效 Bearer 重放。
- **边界**：旧管理 Dashboard 路由有重定向；没有 `/admin/models`、`/admin/agents` 页面，也没有旧 `/chat` 入口。存在页面不代表背后的知识检索或工单能力已完成。登录页账号/密码初始为空，源码与重新生成的客户端产物已移除旧固定预填值；本地身份 bootstrap 仍由服务端配置管理。
- **验证入口**：`apps/web_chat/e2e/analytics.spec.ts`、`chat.spec.ts`、`routes.spec.ts`；场景使用 API 路由桩，不能视为真实后端或上游模型的全链路验收。

### M02 — API 接入、契约与公共基础：已实现

- **入口**：`apps/api/src/supportops_api/main.py`、`dependencies.py`、`schemas.py`、`pagination.py`、`middleware.py`、`errors.py`、`routes/`。
- **实现**：应用生命周期创建数据库/Redis 客户端并进行身份 bootstrap；请求依赖解析可信身份；CORS、关联 ID、统一业务异常响应、Pydantic 请求/响应模型、OpenAPI 文档。
- **错误兼容**：OpenAPI 统一 ErrorResponse 与稳定业务码，健康 503 保留健康模型；HTTP/领域资源隐藏均使用固定 404 NOT_FOUND 文案。job 租约/授权、通知/恢复、动作/审批、证据/配额分清失败域；登录/会话 401 使用固定码/no-store/Bearer challenge。Web 检查 Run 快照/回执/详情/取消/SSE 已知状态，未知契约阻止新提交并要求刷新，不映射为业务终态；取消意图仅在服务器确认后显示 cancelled。相关测试未运行。
- **接口分组**：`auth`、`admin_identity`、`admin_models`、`admin_agents`、`admin_tools`、`admin_knowledge`、`admin_knowledge_index`、`admin_knowledge_ingestion`（本人管理员任务元数据/取消/显式到期）、`admin_dashboard`、`admin_run_attempts`、`admin_run_notifications`、`admin_runtime_alerts`、`data_governance`（同租户草稿/本人申请）、`agents`、`conversations`、`runs`、`actions`、`approvals`、`mock_tickets`、`health`。
- **分页规范与现状**：共用参数为 `page >= 1`、`page_size` 默认 20 且最大 100，分页响应有 `items/total/page/page_size/pages`。工具目录、知识来源、知识版本和运行通知现已与 Agent、模型、用户、会话、知识文档、运行/错误列表一样采用数据库 count/offset/limit 与稳定唯一排序。来源支持名称搜索，文档响应批量携带来源摘要；Web 工具/版本历史使用服务端分页。知识版本由数组升级为分页对象，前后端需要同步发布。部门完整树、整体替换授权集合和事件流仍保留完整语义。
- **健康接口**：`/health/live` 为进程存活；`/health/ready` 检查 PostgreSQL 与 Redis。它们不等同于模型可用、业务工具健康或端到端 Agent 成功。
- **边界**：部分企业管理、Dashboard 聚合逻辑当前位于路由文件中；新增复杂逻辑应优先复用或下沉核心服务，不为了目录形式引入无必要抽象。
- **验证入口**：`tests/api/test_pagination_contract.py`、`test_auth.py` 及各业务 API 用例。

### M03 — 身份、租户与企业组织：已实现

- **入口**：`supportops_core/auth.py`、`passwords.py`、`bootstrap.py`；API `routes/auth.py`、`admin_identity.py`。
- **实现**：邮箱密码登录、scrypt 密码哈希、带 issuer/audience/过期校验的 HS256 JWT；JWT `sub` 为 User UUID，身份必须对应当前租户内的 active 用户。API 每次解析后以数据库最新角色和显示名为准。
- **登录会话**：JWT 必须引用持久化 AuthSession 的 jti/用户/租户/许可代次。登录锁用户后签发；单次/全部注销永久撤销，密码重置与角色/状态变化同事务原子递增 User.auth_generation 并撤销旧记录，重新启用不复活旧会话。本人分页/撤销不提供管理员隐式查看；审计只存 ID/原因，无令牌/IP/设备指纹。SSE 每轮复检并发送连接级 auth.expired，不改 Run 终态；注销不取消已提交业务。0040 未执行，旧 JWT 与直接签发的测试 fixture 需按新契约更新，API/Web 同步升级后重新登录。
- **角色**：`platform_admin` 管理控制面；`employee`、`agent_user`、`customer` 可在命中显式 Agent 授权后使用 Agent。平台管理员也不能仅凭角色跳过 Agent 使用授权。
- **企业能力**：当前租户公司资料与品牌、用户创建/资料/角色/状态、管理员重置密码、部门树及直属人数、部门管理约束。首次 bootstrap 仅在用户表为空且开关开启时创建身份，不覆盖已有账号。
- **数据**：`tenants`、`users`、`organization_units`、`auth_sessions`。
- **边界**：尚无企业 SSO、外部 subject 自动建号、委托 OAuth 身份或跨企业身份联邦。排队Run领取、续租、模型/工具调用及产品提交已有数据库当前身份/授权复检；真实业务目标的资源/字段范围仍需接入获批连接器，不能把平台角色视为目标系统许可。
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

- **凭据撤销交互（W20.9）**：面板按端点重挂载并固定登录会话，确认冻结目标ID/revision与打开时当前revision；已知变化不提交，延迟查询和迟到结果按会话/scope/序号复核。同步防重复提交，匹配目标回执及撤销时间/人员才提示成功；失败/未知关闭旧确认、清说明并只读刷新，再写须重新确认。详情/凭据两列表GET与撤销成功响应no-store，Web对应禁缓存，不新增持久化令牌/秘密字段或改变原授权/事务；未运行测试、类型检查、构建、浏览器或数据库验收。

### M05 — Agent 控制面、版本与授权：已实现

- **入口**：`supportops_core/agent_config.py`、`agent_services.py`；API `routes/admin_agents.py`、`agents.py`；Web `pages/agent-management/agents/`、`pages/agents/`。
- **实现**：Agent 基础资料/slug/欢迎语/建议问题、草稿 revision 乐观并发控制、严格配置解析与 SHA-256 摘要、发布新版本、激活历史版本以回滚、停用、用户或角色授权整体替换、控制面审计。
- **发布校验**：模型固定版本及其验证状态、凭据引用、模型能力、运行参数平台上限、已注册且允许绑定的工具、高风险工具的审批配置声明。
- **运行契约**：会话创建时固定 `AgentVersion`；该版本固定 `ModelEndpointVersion` 与解析后的工具 ID。之后发布/激活不会重写旧会话版本。本轮凭据 revision 在创建 Run 时固定，不是会话创建时固定。
- **发布清单**：新 AgentVersion 同事务保存 agent_release_manifest_v1 和摘要，关联 Prompt/图/源码/Schema/依赖/固定模型/工具/目录/授权/审批/检索策略。知识仅固定 keyword_v1 与当前 ACL 检索策略，Run 证据定位实际快照；发布凭据 revision 仅作历史记录。清单保持发布时 not_assessed，GET 校验完整性、旧版本不补造。固定版本 comparison 区分相同/变化/增加/移除/未知，清单及比较不批准发布。
- **发布门禁**：release_gate_services/0042 的追加式 AgentReleaseEvaluation 绑定固定清单、当前源码/模型请求/凭据/人员/项目授权/知识快照/ACL/索引/自然有效期及批准评测集/负载/执行器/阈值。只消费最新结果，不回退旧通过记录；过期/篡改/范围变化失效，元数据超限拒绝。功能/安全/质量/完整 USD 成本/客户端延迟按已有基线，staging/production 强制；开发环境显式激活仍标未验收且已知功能/安全失败拒绝。激活使用固定模型/工具当前校验，正式新会话再次检查；API/Web 保存候选再检查显式激活，兼容旧 activate=true 的失败整体回滚。门禁/分页历史仅管理员查看，没有报告上传/手填通过接口。回滚撤回退出的活动版本，withdrawn_at/by 不可清除或覆盖；阻止新激活和原提案/执行来源的新写动作，目标Mock事务与回滚串行，已有未知结果保留只读复核/对账。W19执行器和实测仍待；灰度实现见下，0042至0044及测试/类型检查/构建未执行。
- **灰度发布**：release_rollout_contracts/services/observations 与0044固定基线/更新候选/租户主体/策略摘要，新会话按tenant/Agent/rollout/user稳定哈希选版并保存轮次/组别/桶；旧会话不变，不授予权限。每Agent最多一个未停止灰度，初始≤10%、每次增加≤10个百分点、最高90%，全量需显式激活。服务器批准策略摘要默认空，两版本须完整passed，开发豁免不适用；无实测不能启动。启动/恢复/扩量重新观察；有界两组元数据检查足量样本、无等待/取消、完整费用/API首正文上界/完整响应、结构化结果绑定、授权拒绝及相对退化；不推断业务质量或客户端SLO。Worker默认30秒/10条轮转复检，90秒过期关闭新候选，失败/观察期满缺失自动暂停，事件区分人工/系统。暂停只影响新选版；停止曾启动候选永久撤回新写许可，激活同事务关闭灰度并按需撤回，原未知结果保留只读对账。管理台历史版本可保存草稿、查看门禁/观测/审计并显式控制；迁移/浏览器验收未执行。
- **数据**：`agents`、`agent_drafts`、`agent_versions`、`agent_access_grants`、`agent_audit_events`、`agent_release_evaluations`、`agent_release_rollouts`、`agent_release_rollout_events`。
- **边界**：配置可发布不代表外部动作已受到实际审批控制；历史迁移占位 Agent 有只读限制。没有用户自定义任意执行图、上传工具代码或独立专业 Agent 编排。
- **验证入口**：`test_agent_config.py`、`tests/api/test_agent_management.py`、`test_conversations.py`。

### M06 — 受控工具目录与本地执行网关：基础治理已实现

- **入口**：`tool_services.py`、`tool_policy.py`、`tool_gateway.py`、`resource_policy.py`、`quota_services.py`；API `admin_tools.py`；Web 工具管理。
- **目录与策略**：静态实现/固定 Agent 绑定/当前目录取交集，is_enabled 限制历史版本后续调用，独立 is_revoked 紧急撤销；解除要求 expected_version，更改与撤销有追加审计。low→R1、medium→R3、high/未知→R4，静态风险不能被目录降低，批量/破坏性参数提升风险。
- **实时授权**：领取、续租、每次模型/工具调用及提交前以数据库当前用户/租户/角色/Agent grant 判定；资源级接口使用服务端事实，未知资源默认拒绝，管理员没有隐式业务越权。
- **网关**：严格 Schema/参数大小、权限、单工具/整个 Run 次数、租户每日/并发额度、风险和审批门禁；执行前持久化 ToolInvocation，失败/取消消费许可，同 call_id 不重复执行。返回前复检策略并脱敏/限制大小，只存参数与结果摘要。
- **出站基础**：outbound_http为服务端固定HTTPS目标提供精确域名/端口、全部DNS地址检查/固定IP连接/原主机TLS，可选确切IP集合，私网仅逐个批准RFC1918/ULA，回环/link-local等始终拒绝；禁代理/环境CA/重定向/压缩/隐式重试，有限请求/响应和整体超时。DNS用无应用环境/可终止标准库子进程，错误仅固定码。已接OTLP维护发送，无真实业务API/MCP接线或实际网络验收，不替代网关授权/输出Schema/连接器OS隔离，不更改原模型SDK专属网络策略。
- **响应过滤补强**：所有已注册输出及身份摘要使用严格JSON封闭Schema，重复键/非有限数/非对象/超过16层或4096节点/运输与领域状态不一致拒绝；全部文本字段递归脱敏再复检，完整认证/Cookie头、多行PEM及本轮已知模型密钥/JWT/密封主密钥遮蔽。秘密只在可信进程内用于替换，不进入账本/信封；来源ID/digest/key需脱敏则整份拒绝。知识content_is_redacted区分本次脱敏与预算excerpt，旧未知为null，原分块摘要不改；不截断JSON或保留含原输入异常链。网关已移除重复字节截断分支，未知远程Schema仍拒绝，不声明通用语义注入检测；测试/构建未执行。
- **输出协议**：tool_result_services对已注册工具声明封闭输出字段/类型/长度、有限数值和状态/事实约束；未知Schema/额外字段拒绝。逐字段脱敏后完整序列化，不截断JSON；知识原始片段空白保留，工具任意artifact/响应元数据不透传。模型边界使用model_tool_data_v1信封绑定调用/实现/目录/接收时间，data为不可信观察、没有执行许可，未检查不表示无冲突；内部诊断继续消费领域契约。知识预留信封预算，超限拒绝，不删除来源ID/摘要后伪称完整。tool_context_services以同租户/Run/attempt/call ID和实际账本摘要核对ToolMessage，ContextSnapshot保存已用来源并集；模型前后、正文写入和最终提交复查目录/绑定/实现/角色/配置/Mock开关、本人提案及工单当前授权/版本。动态Mock观察按原观察时间最多五分钟，裁剪/物理重试不移除已用证明，不按重连接收延寿；变化以TOOL_CONTEXT_AUTHORIZATION_CHANGED停止。SDK回答Schema重试与业务工具分开，旧无信封工具消息拒绝，不从用户同形JSON建来源。诊断只比较同请求已声明公共标量及显式冲突，追加TOOL_SOURCE_CONFLICT/未完成标记，模型不能择一确认；不声称通用语义冲突检测。测试/性能验收未运行。
- **实际工具**：`current_identity_summary` 使用当前身份；`support_ticket_lookup` v2 只查询本人或显式人员授权的隔离 Mock 工单，未启用明确 unavailable；`search_knowledge` 在网关内部检索当前主体获准的有效知识。工单旧 v1 绑定需重新发布，不静默替换历史版本。
- **动作**：BusinessAction/ActionAttempt/ActionReceipt 与 Run/RunAttempt 分开；业务键使用可信主体/资源/issue，不含 Run 或模型调用 ID。create_mock_ticket 只冻结服务端派生提案，静态最低 R2，模型不能覆盖主体/参数；执行意图由独立接口当前授权后提交。Worker 以动作 owner/generation/lease 执行隔离目标事务，再独立验证；submitted 不是业务成功，丢响应/超时/过期保留 unknown，只有 verified 或目标封锁旧请求后的 not_executed 才完成/同键重试。有限对账与人工追加一次查询许可保留累计次数，冲突进入 needs_attention。
- **补偿提案**：compensation_contracts/services与0046保存独立不可变ActionCompensationProposal，仅支持本人关闭已定位Mock工单意向。当前已完成且关联的Run、当前grant/固定工具/模型/凭据/版本撤回、原动作冻结参数及当前generation独立verify/reconcile回执均复检；unknown/submitted无确定目标不生成。固定当前本人/项目/业务键/目标版本/参数摘要及回执/上下文摘要，SHA256陈旧范围拒绝，同范围复用，不改原动作/回执/批准；最低R3，原动作或当前策略R4保持R4。GET分开历史完整性与当前适用性，变化只标记、不覆盖。本人SQL分页/详情、readiness和显式保存API，聊天抽屉可查看/确认与分页历史，控制面/Run追加最小审计。仅proposed、无批准/排队/执行入口，未来执行须新动作独立非本人审批及重新授权/目标复检，不能用原建单批准或客服权限直接关闭。0046/测试/构建未执行，不支持真实补偿或资金操作。
- **审批与客服**：MockResourceGrant 显式人员/项目/权限；ApprovalRequest/Decision/Consumption 冻结范围、不可覆盖决定、禁止自审、服务器期限、一次消费，Run 等待释放模型 Worker，批准仅恢复动作阶段。Mock 工单工作台支持认领/备注/回复/关闭/升级/释放，同键命令与目标版本校验；MockSupportCase 在提交边界冻结接管问题/来源会话的 Agent 写动作。TicketEscalationPackage 保存有界脱敏上下文，客服另需 support_context_read，知识 ACL 独立复检。
- **计时**：SDK 首个非空输出、API 首次读取已提交正文（轮询/重连上界）、本页提交至 React 首正文提交使用独立来源和时钟；客户端观测按主体/正文事件验证且去重，不能决定业务状态，不跨时钟推算精确传输耗时。Trace 保留缺失指标，后台/刷新不补造客户端计时。
- **边界**：默认 business_action_mock_enabled=false，自动建单开关也默认 false，production 禁止；没有真实 MCP/API、委托凭据或自动补偿。审批消费仅支持隔离 Mock，真实 R3/R4 仍拒绝；Mock 封锁迟到请求能力不得外推为真实 API 保证。迁移和新功能测试未执行。
- **实现版本**：发布快照 resolved_tool_versions 固定工具实现，目录版本与实现版本分开；旧已知 v1 兼容，未知/不可用实现不能静默使用新版。只读恢复证明按工具 ID + 实现版本判断。网关独立写调用/拒绝/检索追踪，SDK callback 不重复计数，观测失败不回滚调用事实。
- **隔离诊断**：额外注册 get_request_logs/get_current_user_profile/get_model_route_status/list_active_incidents，结合 search_knowledge 为 5 项核心能力。默认 diagnostic_mock_enabled=false，production 禁止启用；仅本人服务器生成的模拟项目，request ID 必须由用户提供，查询窗口带时区且不超过 15 分钟。`diagnostic_workflow` 将 S01–S06 接入十节点/五查询上限的固定流程，事实按工具/字段白名单保存到调用账本；查询事实与根因候选分开，唯一跨源一致候选还需当前获准版本化知识声明才确认模拟根因。缺信息追问，冲突/失败/预算不足生成未确认事项和接管包，不伪称建单或真实生产结论。
- **工具发现治理（W8.6）**：tool_discovery_contracts/services及0051以追加修订保存来源指纹/显式描述摘要allowlist/期限、固定目录和listChanged/手工失效。按租户/来源/远端名消歧并固定输入/输出Schema与目录摘要；当前pending修订才接收有界目录，旧record_id/catalog_digest在失效提交后拒绝。五个同租户管理员API与Web API/types已接，execution_enabled恒false；不保存地址/凭据、不授予绑定或执行，不代表MCP协议/网络客户端、通知接线或连接UI已完成。当前66表，迁移与测试未执行。
- **来源管理（W8.10部分）**：新增同租户当前来源SQL标准分页API，单项/列表返回完整policy和独立connection_status=not_connected。Web /agent-management/tool-sources及管理员菜单提供新建/编辑批准/启停、显式目录失效和当前固定定义抽屉；未知契约禁写、旧token/目标响应丢弃、未知写入只查询原来源。无真实连接测试/熔断/恢复，8.10整体未完成；无新表/迁移/依赖，测试/类型检查/构建/浏览器验收未执行。
- **隔离MCP协议（W8.5部分）**：mcp_isolated_contracts/runtime/client/server/cli提供固定SDK1.30.0、协议2025-11-25的stdio发现及固定无参数只读Mock状态调用。SDK在mcp-isolated可选组，默认API/Worker不安装/加载；独立Dockerfile/Compose固定10002 UID、network none、无应用卷/env_file、清空环境及资源限制，入口复检基本隔离前提，不视作完整OS隔离认证。来源/描述/Schema与结果固定，通知使当前观察失效；无业务库/模型/任意命令或目标、无网关/Agent执行接线、无治理写入或真实HTTP授权。仅uv生成锁文件，未构建/启动/运行发现调用或测试验收，8.5整体未完成。
- **数据**：`tool_catalog_entries`、`tool_discovery_revisions`、`tool_invocations`、RunEvent/Audit；新功能测试按当前约定暂缓。

### M07 — 知识生命周期、关键词检索与证据链：基础已实现

- **导入持久化**：knowledge_ingestion_contracts/services和0047四表实现固定任务/状态/摘要、本人幂等、owner/revision/generation/lease/有限重试/取消/超时收敛、稳定对象绑定/草稿物化去重/不可变页回执。当前仅本地清单的一页initial→complete，远程游标秘密存储和删除处理仍待；默认无服务端来源策略则拒绝。当前身份/管理员、同租户来源/申请人、对象/负责人/ACL映射/期限复检，文档UUID锁序下原子物化并与回执/审计/计数/检查点同事务，同页重放先查回执再检查新执行围栏；历史取代/删除候选不自动复活。PG唯一/绑定/不可变/终态和降级保护已写未执行，源码纳入发布摘要、保留预览计入关联。当前65表/0048迁移源；本人任务管理API/UI已接，内部单次读取到草稿执行库已接，运维文件批准提供者和默认关闭的人工API/UI已接，实际来源配置/Worker接线仍待，未运行测试/构建/迁移或实际导入。
- **本人导入管理**：admin_knowledge_ingestion提供/v1/admin/knowledge/ingestion-jobs的标准SQL分页/详情、严格expected_revision取消/显式超时收敛，当前管理员仅限本人申请，跨范围固定404；默认关闭的来源readiness/显式创建/原请求键查询/单次执行入口已接，配置与边界见下条。封闭knowledge_ingestion_job_view_v1不含正文/路径/秘密游标/完整scope，坏记录unknown并隐藏范围/执行字段、操作false，GET不修复或伪造终态。Web /knowledge/ingestion-jobs从知识列表进入，筛选/分页/详情/显式确认/错误重试、300ms加载、请求序号/主体重挂载/当前token核对及未知契约禁写；写失败只刷新查询不自动重放。knowledge_ingestion_review及/{id}/drafts标准SQL分页只列本任务首次创建的物化ID/时间；单项comparison绑定回执/意向/当前最新版本/负责人与ACL映射，文档锁下复用当前知识ACL/有效期，权限不命中或首次草稿无有效发布则隐藏正文。获准时提供读取时当前发布版的64KiB/每侧4000行有界纯文本替换差异，超限不截断；页面展示待审/负责人ID/已有复核时间，冲突状态隐藏范围，不授予分配/审批/发布。独立governance GET/PATCH提供本人最新候选的负责人/复核日期元数据，首次无发布/ACL也可管理但不读正文。严格治理摘要绑定当前文档/候选/审核/ACL，锁内复检同租户active负责人、当前会话/管理员；变更复用文档更新并追加最小关联审计，不改变ACL/审核/索引。页面有效用户选择、带时区日期/显式清除与服务端到期提示已接，保存时清旧比较、禁关闭/重复提交，失败只GET确认。本人可通过/withdraw在显式原因和当前治理摘要下撤回自己未发布的最新候选；候选retired并增加审核修订，保留历史/原回执，双审计与状态同事务，不变更正式版本/ACL/有效期/索引。candidate_withdrawn须未发布退役状态与唯一真实本人Job/物化/当前修订审计匹配，旧退役不补造，撤回后隐藏正文并禁写；未知响应只GET核对。W27.6在当前本地单页模式的界面实现已完成，人工执行入口已接，实际来源配置和API/浏览器验收仍待。
- **候选文件预览**：knowledge_markdown_source提供显式目录/精确.md清单的只读内存预览，最多100文件/单个1MiB/批次4MiB；固定范围摘要、逐级NOFOLLOW、拒绝软硬链接/特殊文件/别名、读取前后事实与超时/取消保护，严格UTF-8和有限章节锚点复用现有逻辑。读取器没有默认目录/递归扫描/外链读取/正文执行/ACL推断或草稿写入；预览读取与正文解析使用独立进程并共享截止时间，解析复用摘要核验的knowledge_content部署源码、禁pyc并设CPU/FD/core及8MiB输出限制；父进程只校验预览响应/摘要，知识写入仍保留领域校验。同OS用户且无UID/挂载/网络/内存完整隔离，不能当作沙箱验收。读取/解析manifest已升v2/v3，旧批准/Job须重新确认，不自动迁移。knowledge_ingestion_runtime已将读取器接入内部单次草稿事务，默认无可信批准提供者则拒绝；固定当前清单/策略，领取与读取前后/提交边界复检，来源IO不持数据库锁。当前执行才可记录固定失败，领取结果不明不读来源，页面结果不明只查原任务/回执不重读或重放。knowledge_ingestion_approval提供运维保护文件的当前批准查询，逐层NOFOLLOW/可信父目录与文件权限/128KiB/重复JSON及范围摘要检查，每个边界重读。默认knowledge_ingestion_enabled=false且仅隔离local/dev/test可开启，staging/production拒绝；人工readiness仅单项元数据，创建只接摘要和UUID幂等键并固定600秒/一次尝试，未知创建按原键查询，详情另行确认execute；running含过期租约和终态不从人工入口重领。Web来源状态/创建与独立执行确认、原键查询、未知不重放和token/目标绑定均已接。创建前按tenant/user向sessionStorage保存并回读最小请求键/来源ID，失败不发送POST，已有未确认或坏记录不覆盖；关闭/刷新后恢复只查原请求，来源不可用仍可查，404不清除，当前回执匹配才清记录，不保存令牌/路径/正文或恢复执行许可。无实际批准文件/目录配置或Worker导入执行器/自动调度，本机API人工CLI见下条，W27.3–27.5部分实现；未实际读取来源或运行测试，设计第14–15、20–24节记录边界。

- **读取/解析资源回收**：两个固定程序共用CPU软2/硬3秒、最多32FD、core0限制，读取/解析manifest现为reader_v2/plaintext_v3，旧批准/Job不自动升级。每进程最多两个未回收程序、不排队，满额PROGRAM_LIMIT_REACHED映射已有rate_limited；独立监督任务强引用/shield及停止信号处理重复取消，启动中已收到停止则取得句柄后不发送输入并回收，确认进程退出才归还额度，未知回收保留占用，不继续下一阶段或写业务。不代表跨副本/完整内存/OS沙箱或强制停机硬保证，未运行进程、测试或来源读取。
- **人工导入事务预算**：readiness/创建、执行初读/领取、目标复检、失败记录和最终提交统一整体10秒等待，覆盖连接池/累计SQL/边界配置查询/提交；执行阶段按原租约/Job/批准剩余时间裁剪，源IO仍最多5秒且不持事务。停止失效来源许可不要求旧批准仍有效，当前本人/围栏仍复检；创建/领取/提交超时结果未知只查询，失败记录超时不伪造终态。取消/回滚/清理可额外耗时，不代表硬截止或实际验收；无新表/迁移/依赖。
- **在线配置读取预算**：提供者改用独立daemon读取线程，每进程最多两个未结束读取、不排队或占默认executor；等待仍限1秒，超时/取消直到实际读取结束才归还额度，未开始且已确认取消则不读，迟到结果不缓存/复用。满额/启动或读取失败沿用固定批准无法确认错误；不强杀内核IO、不承诺整体硬截止或跨副本限流，离线预检不使用在线额度。未启动线程、读取实际配置/来源或运行故障验收，无新表/迁移/依赖。
- **离线配置预检**：knowledge_ingestion_preflight增加仅配置CLI，不是导入执行器；显式批准文件路径/预期范围摘要，共用运行时受保护读取与严格解析，核对当前实现/范围/期限/600秒窗口。封闭JSON和固定退出码，不输出路径/清单/人员/原文/异常，不读.env/来源、不连接数据库或启动导入，不生成批准或提供执行许可；同步配置IO无硬截止承诺。源码纳入发布清单，无新依赖/表/迁移；实际配置/来源及测试仍待。
- **本机人工CLI**：knowledge_ingestion_client/cli仅通过现有本机HTTP API查询readiness/原请求/Job、显式创建/单次执行/封锁原创建/取消及到期收敛。目标仅字面127.0.0.1或::1，无任意路径/代理/重定向/自动重试；令牌仅从有界非终端stdin读入，真实会话/管理员/本人/来源/ACL及围栏由API验证。写命令须confirm，创建相关命令须本人预存UUID键，最多一次POST；来源/范围/revision/预算/回执绑定，不重领running或终态。写调用后任意失败保守outcome_uncertain/退出4，仅显式查询；不直连数据库/读取来源或批准文件、生成批准/新键、保存令牌或自动调度。严格64KiB/16层/4096节点回执、固定JSON/退出码不含名称/URL/正文/原错误，客户端时钟与API观察分开。源码进发布清单，未运行CLI/API/网络/来源或测试/类型检查/构建，实际配置与完整OS隔离仍待，27.3未完成。
- **入口**：`knowledge_services.py`、`knowledge_content.py`、`knowledge_index_services.py`、`knowledge_retrieval.py`；API `admin_knowledge.py/admin_knowledge_index.py`；Web 知识详情索引面板。
- **生命周期**：来源/不可变版本/审核状态/发布快照/失效/回滚/归档/用户角色部门 ACL；生命周期和 ACL 使用文档锁，同事务产生发布、取代、失效、归档、权限变化与到期 Outbox。
- **索引**：Worker 独立消费知识 Outbox，短事务领取、幂等收敛、保存点回滚、有限退避/死信；管理员分页查看元数据、重建或新建有限恢复任务，保留旧死信及次数。旧当前快照由迁移排重建，未构建不伪装 ready。
- **分块与检索**：keyword_v1 按章节和 1600 字符边界分块，记录偏移/原文摘要/确定性 chunk_key；英文数字词项/中文二元组、转义通配符和数量上限，以 SQL 先筛租户/主体/当前版本/有效期，再锁文档复检后读正文。不缓存正文或权限。
- **证据**：服务端生成 evidence ID，绑定 user/Run/attempt/snapshot/chunk/digest/时间/关键词分数；工具返回、最终提交和用户展开均复查权限与当前版本。自造 URL/未检索引用拒绝提交；失效或撤权隐藏标题/快照/摘要，保留可审计记录。
- **数据**：原 6 张知识表 + `knowledge_index_generations`、`knowledge_chunks`、`knowledge_evidence`及4张导入账本表、1张创建请求停止事实，共14张。
- **显式读取与拒绝审计**：引用ACL前只读快照定位四列，实际分块/标题SQL再次限定当前租户/发布版/有效期/用户角色部门ACL。snapshot/本人Run evidence接口在共同文档锁内投影，读事务5秒/PG语句4秒/锁1秒，成功读取响应及知识专属拒绝no-store；DB失败用既有KNOWLEDGE_CONFLICT，不记权限拒绝。已认证的两显式入口拒绝后结束原事务，独立2秒/PG语句1秒/锁250ms尝试既有ACCESS_DENIED审计，只固定操作/结果/requested_only和tenant域内请求目标/Run/关联值摘要，actor取不可变principal，无原始目标ID/正文/查询/秘密或存在推断。审计失败只固定可用性告警，不恢复读取/提交原事务或承诺必达，不覆盖全部工具/审核/身份前401；内部lock=False保留原边界，无新表/迁移，测试和实际并发仍待。
- **审核门禁**：送审只写 submitted_by/at 和 pending，独立 review API 写实际批准/拒绝及 revision；创建人/提交人禁止自审。批准摘要固定内容、标题、有效期和 ACL，发布复检范围与审核人当前管理员权限；新版本或范围变化须复审。旧审核中字段迁移为提交事实，不补造批准；管理台决策弹窗与发布/重新送审分开。
- **边界**：按用户 2026-10-13 选择暂不启用 embedding、向量/混合搜索、重排；关键词分数不是语义相似度。外部 CMS、完整证据冲突流程及检索评测仍待实施；测试/迁移验收暂缓。

- **未知创建处置**：0048新增追加式knowledge_ingestion_creation_stops；本人单项创建请求状态为unobserved/recorded/stopped，独立确认stop在原任务不存在时保存停止事实，拒绝同键迟到创建；原任务已存在则仅返回，不取消。创建/停止使用同主体锁，PostgreSQL触发器要求READ COMMITTED并双向互斥，不外推SQLite并发保证。来源未配置或撤销也可处理旧请求；Web只有核对到原任务或停止事实才清本地定位，未知提交不自动重发。保留预览计入本人关联，未提供删除许可。当前65表/0048源码，未运行迁移或并发/API/页面验收。
- **读取并发与管道边界**：IO前记录固定对象/绑定、文档治理、最新候选/当前发布版的元数据摘要；版本查询不读正文，IO释放锁，提交先在UUID文档锁序下核对，一致才物化，明确变化可由当前有效执行记录commit_conflict，不自动重读。创建/执行/封锁实际要求PostgreSQL/READ COMMITTED，配置开启拒绝非PG，服务独立复检；历史查询与取消/到期处置保留。管道最多64KiB分段接收，读取6,000,000字节/解析8MiB超限即停止，失败/取消回收协程、终止进程并丢弃剩余管道字节，避免暂停读取后仅wait。设计第23–25节记录边界；完整OS隔离和并发/性能验收仍待，未运行测试或实际来源。

- **原请求查询恢复**：导入页独立只读入口支持输入原UUID查询本人unobserved/recorded/stopped及打开原任务重新读详情，来源未配置或sessionStorage定位丢失/损坏仍可用，不清/替换会话记录、不创建/停止/执行任务。本人任务投影creation_request_key仅原键已是规范UUID且完整性通过时返回，其他旧字符串为空、不转换/透出；回执绑定Job内原键。输入change/提交校验、300ms Loading、AbortController/请求序号/当前token/主体重挂载及错误/未知状态处理已接，关闭只取消查询。设计第26节记录边界；API/Web同步，仍65表/0048，未运行测试/类型检查/构建/浏览器或实际来源读取。

### M08 — 会话、消息与用户可见状态：已实现

- **入口**：`supportops_core/services.py`、`agent_services.py`；API `routes/conversations.py`；Web `pages/chat/`、`pages/agents/`。
- **实现**：按被授权 Agent 创建固定版本会话，首次发送前可只停留在新对话 UI；按租户、用户、Agent 查询历史，重命名、置顶、软删除，读取消息与最新 Run 的正文快照/事件游标。
- **发送事务**：必填 `Idempotency-Key`；同一用户/会话中的重复键返回同一个 Message/Run，相同键不同正文报冲突。事务内原子创建用户消息、Run、`run.queued` 事件与唯一 Run 通知 Outbox，提交后 API 尽力即时投递；投递失败保留通知供 Worker 退避重试，不新建业务输入。
- **删除与取消**：删除会话保留消息、事件和诊断事实；取消 queued Run 直接进入 cancelled，running Run 进入 cancelling。不是所有上游请求都能立即停止，Worker 在执行输出或终态边界观察取消。
- **数据**：`conversations`、`messages`、`agent_runs`、`run_events`。
- **产品结果**：Message 保存版本 1 SupportResult（Run/attempt、答案、摘要、合法引用、未执行建议、升级意向）；旧消息为空。result.ready 和终态同事务，刷新与展开复查引用当前权限，建议/升级不伪称已执行。input_sequence 与有效 attempt 许可保证同会话串行；消息幂等仍不代表外部动作幂等。
- **预算失败升级**：耗尽时只保存当前获准 Mock 提案及有界来源，保存点失败不会回滚 Run 终态，不重跑模型、不赋予失败 Run 写动作/审批许可。聊天提供独立入口并支持刷新恢复，不伪造 SupportResult。升级包以 action/来源 Run/source_digest 去重（排除捕获时间），来源变化追加历史，重复打开不新建同内容；0038 未执行。
- **验证入口**：`tests/api/test_conversations.py`、`test_runs.py`、`test_soft_delete.py`、Web `chat.spec.ts`。

### M09 — Agent 推理运行时与短期记忆：已实现基础主链路

- **入口**：`supportops_core/agent_runtime.py`、`agent_config.py`。
- **模型适配**：`ModelAdapter` 按固定端点版本构造 `ChatOpenAI`，映射协议、能力、生成参数、超时、重试和供应商 `extra_body`；调用前重新检查模型出站地址。
- **执行图**：`AgentFactory` 用 `create_agent` 构建统一单 Agent 图，注入系统 Prompt、可信 `SupportContext`、过滤后的工具和 `SupportAnswer`；支持 `ToolStrategy` 与 `ProviderStrategy`，配置模型/工具调用总量和工具并行上限。
- **短期记忆**：新执行以 tenant/conversation/run/attempt 唯一 thread 隔离，从产品消息按本轮 input_sequence 重建；checkpoint 指针只在产品事务成功后确认，失败尝试隔离保留，迟到旧尝试不能写产品事实。非 PostgreSQL 内存 saver 不能跨进程恢复；保留期清理不删除产品消息。
- **输出**：`StructuredAnswerStream` 从结构化模型增量中仅提取 answer；工具参数、隐藏推理和其他结构字段不进入正文流。最终必须通过 `SupportAnswer` 校验；模型/工具 callback 收集观测与 provider 元数据。
- **上下文与预算**：ContextSnapshot 保存当前问题摘要/输入边界/有限历史/证据时间/确认事实约束/冲突/未完成事项；数据库只加载配置的有限旧轮次及首轮最多两条来源。历史源保存 message/role/sequence/digest，摘录最多 12 条，采用 extractive_history_v1（用户摘录/已有答案 summary/答案片段），脱敏限长并保留旧 evidence 定位、冲突/未完成标记，不声称已确认事实。逐模型调用完整计量后裁完整旧轮次，剩余预算允许时插入首个问题与最近来源的 400/160/64 字符摘录；装不下则省略，不删除当前输入/本轮工具轨迹。调用前后、正文提交和最终落库复检来源/当前证据/诊断/动作授权；失效或损坏旧结果不退回原文，历史与本轮证据统一文档锁序。UTF-8 保守估算不等于精确 tokenizer，无新增总结模型调用/长期记忆；实际性能/安全验收暂缓。
- **调用账本**：ModelCallLedger 在 Run/租户锁下预留完整输入/输出费用、次数，重试/恢复共用；正式执行关闭 SDK 隐式重试并对每次物理重试记账。实际 usage 释放未消费预留，未知/取消/崩溃保留预留；硬预算要求可信 USD 定价，缓存/推理不重复累加。供应商扩展不能覆盖输出/模型/重试/治理字段。Trace 使用账本，不累加父子成本。
- **诊断流程**：隔离 Mock 的 429 使用服务端固定事实工作流，不产生虚构模型调用或 checkpoint；节点/report 进入 ContextSnapshot、阶段 SSE 和完整产品结果。恢复仅复用同 Run/参数/版本/当前策略、五分钟内的成功只读事实；原消费保留，知识引用重新检索。详情和历史结果当前授权投影隐藏失效事实/候选/接管包。
- **边界**：没有 Diagnostic/Ticket 专业多 Agent 或通用业务 Orchestrator；真实业务诊断、额外模型语义总结、长期记忆与真实 tokenizer/供应商预算验收仍待实施，摘录可能不完整、保守估算不代表精确用量。大量历史来源的权限复检有查询成本，性能验收未运行。
- **验证入口**：`test_agent_runtime.py`；`tests/postgres/test_checkpoint.py` 验证真实 checkpoint 重连与 thread 隔离。

### M10 — 异步 Run、队列、事件与恢复：基础已实现

- **入口**：`apps/agent_worker/src/supportops_worker/main.py`、`supportops_core/queue.py`、`worker_runtime.py`、`services.py`；API `routes/runs.py`。
- **队列与领取**：Redis `RPUSH/BLPOP` 传 Run UUID 作通知；默认先锁共享轮转游标，再锁会话/Run，仅成功通知且同会话最早未结束的 queued Run 可转 running，重复通知不再次执行。Worker 有可配置并发（默认 1）与容量背压，领取成功才计入活动 Run；输入序号、会话有效 attempt 许可和只读有限恢复已接入。
- **通知 Outbox**：`run_outbox_services.py` 先只读发现next_attempt_at/id稳定排序的有限候选（默认20，配置1–100，调用limit只能降低），逐条新事务先Run再Outbox且均SKIP LOCKED，锁内复检绑定/当前状态/到期，不持整批锁。忙或陈旧候选不消费次数，继续本批下一项，空领取不当作Run删除；新到期留下一次，不承诺整队无饥饿或表锁/整体IO硬截止。每条独立事务、有限Redis超时，采用至少一次通知；queued未领取可有限再次通知，前轮阻塞不消耗后轮次数。默认每周期最多8次/24小时，退避后进入dead_letter；管理员按租户分页查询/按revision显式恢复有限窗口，原Run/输入/累计尝试/固定版本不变，已取消/执行Run不复活。分发源码纳入发布摘要，重复/多Worker验收未运行。
- **执行快照**：加载并交叉校验租户、会话、Agent/模型版本、config digest、输入消息、用户和固定凭据 revision；整个 Agent 执行有超时。仅在助手消息与完成事件的产品事务成功后标记 completed。
- **流式事实**：`_AnswerEventWriter` 约每 50ms 合并后独立提交正文事件；正文延长使用 `text.delta`，改写使用 `text.replace`。取消状态检查阻止继续写增量；SSE 按 Run 内递增 sequence 从 PostgreSQL 轮询，支持 cursor/Last-Event-ID。
- **异常处理**：RunAttempt owner/generation/lease；过期只读尝试有限恢复，权限/配置硬错误不重试，未知副作用隔离等待对账。旧执行者事件、工具返回和最终提交经过许可判定；观测独立落库且带 attempt。
- **周期任务**：服务心跳、通知分发、旧 queued 恢复扫描、健康刷新和 checkpoint 清理独立运行；各操作有超时/失败重试，不阻塞消费。维护异常在租约显示 degraded；连续心跳失败或后台任务意外退出会停止领取并报错退出。队列消息深度、数据库 queued Run 数和真实活动数分开记录。
- **停机**：停止领取，宽限期保持心跳；超时协作取消当前尝试，副作用未知隔离。未确认取消保留 degraded/过期租约并报错，失败 checkpoint 不复用。
- **状态集合**：`queued/running/cancelling/waiting_approval/waiting_action/completed/failed/cancelled`；业务动作另有 `unknown/reconciling/needs_attention`，不混入 Run 状态。等待释放模型 Worker，恢复不重跑模型；Web 未知状态阻止新提交，取消 ACK 不能提前当作 cancelled，迟到 ACK 不能覆盖已收到的终态事件。
- **公平调度**：默认 tenant_round_robin；共享 RunSchedulerState 持久化租户游标并串行原子领取，跨 Worker/重启有效，租户内稳定排序。Redis 只唤醒，数据库只有已成功通知/无会话许可/最早输入/剩余租户容量的 queued Run 可被选中；死信不被迟到通知绕过。所有共享队列 Worker 需同名/同策略；fifo 是显式兼容选项，不具备租户轮转保证。
- **边界**：通知至少一次不表示执行成功；服务租约与执行租约独立。只读已知工具可有限崩溃恢复，旧无 owner 执行需管理员确认停止，未知副作用不能直接重跑；已提供 waiting_approval/waiting_action 与独立动作对账，真实外部恢复和测试验收暂缓。
- **验证入口**：`tests/worker/test_worker.py`、`tests/api/test_streaming.py`；多数测试使用 SQLite/FakeRedis/模型替身。

M08–M10 的隔离审批扩展：`approval_services.py`、API `approvals.py`、Web `/approvals`。管理员显式配置 Mock 项目人员权限，独立批准绑定指定非本人、参数/资源/固定版本/凭据 revision 与期限；消费和 ActionAttempt 同事务，出站再复检。唯一明确审批者时，模型回答/结果/checkpoint 与 Run `waiting_approval` 同事务保存并释放 Worker；批准进入 `waiting_action`，独立动作消费者完成执行/查询，绝不重跑模型。等待不计活跃模型许可，同会话后续输入排队；取消只终止未发出的动作，运行中/未知副作用仍需对账。客户端/API/Worker 必须同步升级才能启用新状态，真实审批与验收仍待处理。

### M11 — 运行观测、错误诊断与 Analytics：基础已实现

- **入口**：`supportops_core/agent_runtime.py` 的 callback、`worker_runtime.py`、`health_services.py`；API `routes/admin_dashboard.py`；Web `pages/analytics/`。
- **运行指标**：模型/工具调用摘要、Token、缓存/推理 Token、定价估算成本、重试、finish reason、provider request ID、队列/执行/端到端耗时和首输出耗时；缺失信息保留 unknown/null，不伪造零成本或健康。
- **追踪与错误**：Agent 父 span 与模型/工具子 observation、稳定错误事件、元数据白名单、Agent/模型/知识控制面追加审计、分页错误查询与详情/运行时间线。目录/撤销审计与带 attempt 的 ToolInvocation/RunEvent 治理记录已增加。
- **阶段时间线**：business_phases_v1 从持久化 RunAttempt 诊断阶段、审批/决定/消费、恢复间隔、按本 Run started 事件定位的动作 generation 和回执投影；每类最多 100 条并明确 truncated，执行/等待分开、关联父 Span，不复制正文或累计父子费用。未结束/缺失/逆序耗时为空，投影故障不改业务事实。
- **安全审计**：目录策略与撤销、审批决定保留追加控制面审计；Run security-events 接口/Trace 面板按租户管理员权限分页查询拒绝/审批/动作追加事实，动作结果绑定实际 attempt/generation/receipt/status/摘要/版本。字段白名单与脱敏限长，不返回业务正文、理由或原始响应，不补造历史关联。
- **分析接口**：summary/timeseries/runs/errors/trace，系统 Agent 状态和排行；健康由 Agent 版本、近期执行事实与服务租约推导并可持久化为读模型。
- **筛选与下钻**：共用 dashboard_query_services/dashboard_filters 的 SQL 租户/当前生命周期/健康/历史版本/模型/用户/当前部门/Run 状态范围；Web 共用筛选与 URL 区间/分页，刷新不移动范围，筛选重置页码。timeseries 补 bucket_end 并裁切首尾桶，图表/桶选择、排行/状态下钻保留有效条件；完整健康分布不由第一页推断。非法字段拒绝，请求序号与权限失败清空旧数据；健康固定窗口/用户与新增会话/全局服务口径明确区分，未做正式浏览器验收。
- **指标排行**：元数据 SQL 聚合补齐八指标：调用、活跃用户、失败率、Token、估算费用、失败调用、P95、工具许可。失败率排除取消/等待；P95 用终态非负耗时/至少 3 样本/nearest-rank。费用/Token 不完整、工具账本缺失或不一致保留 null，已知优先、UUID 平局、Top 10；Web 指标保存 URL，未知不绘制成零值柱。仅读元数据，不将许可数当业务成功，测试验收未执行。
- **完整卡片/桶计量**：dashboard_run_metrics 用 SQL 聚合完整范围，空集合已知 0，任一 Run usage/费用缺失则对应总量 null；source/sample_count/missing_count 贯通界面。P50/P95/P99 取终态非负端到端耗时至少 3 样本，包含此前等待，不冒充模型时间。14 项卡片和区间末 5 分钟语义已补齐；共享服务的有效 degraded 租约仍存活，通知队列按固定名最新有效 LLEN 去重，未知/stale 不能填零。新代码未完成正式验收。
- **运行告警**：Worker 独立有界轮转扫描八类规则（SDK 首输出慢、预算、租约、恢复、恢复需处理、对账、授权拒绝、缺失模型观测）。只读指标元数据，独立保存和已阅审计，不改变业务状态；API 租户管理员 SQL 筛选分页，Web `/analytics/alerts` 可查看/已阅及关联 Trace。默认首输出阈值未配置，缺失宽限 60 秒、stale 300 秒；空列表不证明健康、已阅不证明修复。扫描是采样，未提供外部渠道、自动告警清理或已批准 SLO；0039/测试未执行。
- **数据**：`agent_run_observations`、`agent_error_events`、`agent_runtime_health`、`runtime_service_leases`、`runtime_alerts`、`runtime_alert_scan_states`，关联 Run/Event/Audit。运行口径与留存边界见 `docs/modules/observability/overview.md`。
- **健康口径**：24 小时按 Run 创建时间统计完成/失败，取消排除，错误率/P95 至少 3 个样本；活跃请求另按全时段排队和当前有效许可统计，旧 owner/过期/死信/待对账不假装正在执行。共享 degraded Worker 仍存活，说明维护或排空；模型停用、固定工具实现/撤销/Mock flag 可使健康降级。缺读模型不以最近 20 条另造健康或单次失败降级。
- **快照新鲜度**：状态 GET 不重算快照，computed_at/observed_at 缺失为空；任一时间超过默认 90 秒或超前超过 5 秒则 health_stale/unknown 并隐藏过期错误率/P95。Web 展示时间、原因和按当前草稿摘要计算的 pending_publish；当前执行许可独立查询，列表刷新不制造健康观测。
- **运行维护**：`docs/modules/observability/operations-runbook.md` 编制发布顺序、非破坏性回滚、死信/attempt、模型/检索/工具、审批/动作对账和泄露响应；依据实际角色/版本/权限接口，不执行或宣称完成生产部署、备份恢复和故障演练。
- **OTLP 导出**：`telemetry_services/telemetry_export` 提供默认关闭的独立维护命令，固定 `supportops_otel_v1`，按显式允许租户/带时区≤24小时完成窗口有界只读映射；默认仅预览数量/摘要，`--send` 才发出 HTTPS 请求。关联ID租户域HMAC假名化、父关系核对，标量白名单不透传自由文本/正文/凭据；父聚合不重复携带用量，缺失不填零，客户端耗时保持不可信。确切IP允许集合/DNS固定/TLS校验/禁代理与重定向/大小及整体超时/部分成功数量处理齐全，无自动重试/后台调度/可靠投递承诺，不替代数据库审计；命令、接收端联调与测试均未执行。
- **边界**：仍无独立评测执行平台。OTLP只投影已持久化观测，业务阶段仍查应用Trace；迟到数据及父关系补写须人工重扫，接收端去重和永久存储未保证。Worker 首输出时间不等同于客户端实际收到首文本；多轮 checkpoint 累计消息与本轮调用统计边界需验证。心跳现已与长任务解耦，真实基础设施/停机验收仍暂缓，不可把 Dashboard 当作已完成生产 SLO 验收。
- **验证入口**：`tests/api/test_dashboard.py`、Worker 观测/健康用例、Web `analytics.spec.ts`。

### M12 — 数据持久化、配置、凭据与基础设施：基础已实现

- **保留与本人申请**：`retention_contracts/services/inventory` 和0045定义不可变 RetentionPolicy 草稿、本人 ErasureRequest、未来 DeletionTombstone 结构。管理员仅管理同租户策略草稿，保留周期/法律保留未确认保持null，候选复核人不产生授权。本人申请固定创建时类别/原因/策略/截止时间/摘要，同键同范围复用、异范围409；预览/撤回使用expected_revision，报告一次生成不可覆盖，撤回保留历史。16类固定目录只统计本人/参与/共享关联元数据并包含软删除历史，不读取正文或解封凭据；各类保留未枚举依赖null、共享引用和法律保留阻塞，计数不构成可删除集合。预览额外统计当前未结束Run/动作，整体10秒、PG语句2.5秒/锁等待1秒，失败回滚不存半份报告。GET核对范围/报告摘要，坏报告隐藏；未来复核须绑定申请/策略/预览并独立非本人、重新盘点。用户菜单 `/account/data-requests` 提供本人分页/新建/一次预览/详情/撤回，管理员无他人申请入口。没有实际批准、清理、擦除或备份恢复重施，tombstone当前无写入口；业务ORM删除保护保留，0045/测试/构建未执行。
- **入口**：`supportops_core/models.py`、`db.py`、`config.py`、`secrets.py`、`logging.py`；`migrations/`、`deploy/docker-compose.yml`、workspace 与 Makefile。
- **数据层**：SQLAlchemy async ORM、PostgreSQL/JSONB、Alembic 迁移、索引/约束/数据字典注释；本地 Compose 使用 PostgreSQL 17/pgvector 与 Redis 8，并提供持久卷和健康检查。
- **软删除**：带 `SoftDeleteMixin` 的资源用 `deleted_at`；ORM 默认隐藏已删除行并禁止业务物理删除。授权/ACL 替换保留历史；活跃记录唯一索引允许删除后重建。内部历史查询显式 `execution_options(include_deleted=True)`，客户端不能传绕过参数。
- **凭据**：`SecretProvider` 协议与 Fernet 本地密封；create_secret_provider 工厂统一 API、推理 Worker 和独立探测策略，SUPPORTOPS_SECRET_PROVIDER 显式固定 local_envelope_v1，未知实现不能降级。API 返回 masked hint/revision，历史 revision 支持排队 Run 固定引用；没有真实 Vault/KMS、短期委托凭据或连接器进程隔离。
- **隔离设计**：总规划design第12节明确未来外部连接器使用独立OS身份/认证IPC/受限broker，封闭一次调用请求不携带应用环境/主密钥/平台JWT，目标由服务端配置、网络默认拒绝、输出仍经网关治理，写结果未知保留对账。W20.4仅设计完成；provider/短期凭据/IPC与OS网络实现/故障验收仍待，现有Mock与OTLP维护程序不等于连接器沙箱。
- **凭据撤销**：ModelCredentialRevision 固定密封 provider/历史提示及永久撤销时间/人员/说明。撤销和轮换共用凭据锁，决定不可覆盖或解除；轮换产生新 revision，不替换 Run/探测的原引用。轮换锁查询显式排除软删除并刷新预加载的当前元数据，再递增revision；配置保存使用轮换返回值，不读旧密文，数据库竞争验收未执行。解封前后、后续调用和最终提交复检，已发请求不保证撤回。实际 API/Worker 解封前提和访问审计分开，独立审计短事务提交后才把密钥返回受控 SDK；审计不含密文或正文。管理员可分页查询修订和访问元数据；0037 及新功能验收未执行。
- **凭据读取范围（W20.3部分）**：共同assert_credential_revision_active只查询修订ID/端点/provider/撤销时间，显式可信tenant/credential_id/revision及父子未删除，返回不可变元数据，不加载历史密文或可延迟读取的ORM实体。解封入口先核对端点/provider，密文SQL再次限制原修订ID和同范围/未撤销未删除，返回前复核原ID/端点/provider；失败不换修订或重读密文。provider普通异常和非字符串/空白结果统一UNSEAL_FAILED并隐藏原异常链，取消仍传播，原独立审计保留。仅约束共同检查与解封入口，不宣称其他调用方全局无密文读取/OS隔离或已发请求可撤回；无新文件/依赖/表/API/迁移，未实际解封或运行测试/构建/数据库。
- **默认密文加载保护（W20.3部分）**：ModelCredential及ModelCredentialRevision.encrypted_secret设置ORM deferred/raiseload，默认实体查询不携带密文，未加载属性误读直接拒绝、不隐式SQL。创建端点/连接配置与轮换分别复用一次seal局部结果写当前值/新revision，不回读实体密文；解封仍显式列查询并复检原范围。显式可信SQL/undefer及已赋值/已加载对象不受此加载策略隔离。models/model_services进入新发布摘要，无新文件/表/依赖/API/迁移；旧test_agent_management直接读取修订密文字段待恢复测试后适配，本轮不修改或运行测试，也未执行类型检查/构建/数据库或实际seal/open，20.3仍部分完成。
- **解封审计等待（W20.3部分）**：访问审计统一单调2秒工作等待，独立PG事务语句1秒/锁250毫秒，退出后检查截止，迟到完成也不返回密钥。普通异常/超时为CredentialAuditUnavailable/MODEL_CREDENTIAL_AUDIT_UNAVAILABLE，隐藏原SQL/参数/异常链，不自动重读/解封/审计重放；外部取消传播，未知提交不推断未记录。API该子类503/no-store，其余凭据拒绝403/no-store，现有Run/探测保留码且不自动恢复。无工厂兼容核心调用仅限定原flush等待，不提交/回滚或改调用者配置；清理不保证硬截止。无新文件/表/依赖/迁移，未执行测试/构建/数据库/实际审计，20.3仍未完成。
- **密封写入边界（W20.3部分）**：secrets统一8–20000字符边界，API写Key与核心/本地密封去除首尾空白后复检；编辑null/未提供保留，显式空字符串不忽略，非法输入422不调用provider。创建和轮换统一_seal_model_credential，provider普通异常或非字符串/空白/原样输入结果固定MODEL_CREDENTIAL_SEAL_FAILED，隐藏原异常链，依赖错误（含原审计不可用）503/no-store；其余凭据拒绝403/no-store。先取得结果再改凭据/revision，一次结果写当前与历史，API事务失败回滚；不自动重试或借旧Key保存，结构检查不证明通用加密正确。编辑连接名称/预设在可选轮换成功后赋值，避免本次编辑提前待写；表单保存/保存并使用/保存测试配置共用码点长度校验与错误aria，编辑留空保留原Key，模型发现契约不变。secrets加入新发布摘要，无新文件/表/依赖/迁移；未运行测试/类型检查/构建/浏览器/数据库或实际密封解封，20.3仍待企业provider/身份。
- **历史引用保护（W21.6部分）**：reference_history接入db的before_flush，拒绝历史凭据固定列与知识证据/分块的实际列改写，首次完整撤销和单向软删除保留，旧状态未加载时拒绝。0049另加三表PG行UPDATE/DELETE及语句TRUNCATE保护并ALWAYS启用，有任意历史拒绝降级；不回填或扫描旧内容。批量/原始SQL保护须应用0049，SQLite仅普通ORM实体保护，数据库所有者/超级用户及独立维护分权仍是边界；保留字节不绕过撤销/ACL、不提供实际擦除或恢复许可。新源码/db纳入发布清单，无新表，0049/测试/构建未执行，真实销毁及tombstone仍待。
- **固定引用影响说明（W21.6部分）**：本人预览按截止时间内会话/Run统计固定Agent/模型版本、所关联凭据及Run真实credential_id/revision组合，知识按本人Run证据统计快照/分块集合；共享实体去重、含软删除历史、不读正文/密文/他人引用明细。既有reason_code给出六类说明，已知非零引用追加审查阻塞，不取消原未知/法律保留/不可擦除标记；无创建时间的分块仅按证据检索截止限定。Web折叠表格显示范围/数量/依赖，不透内部集合名/资源ID，旧未记录/未知计数不当零，切换申请即隐藏旧详情；原在途计数独立，旧报告不回填或重算。治理源码纳入发布清单，无新字段/路由/表/迁移，API/Web/测试/类型检查/构建/浏览器验收均未执行。
- **证据上游保护（W21.6部分）**：reference_history扩展KnowledgeVersion原文/标题/摘要/锚点/创建定位、KnowledgeSnapshot固定列及KnowledgeIndexGeneration快照/算法/摘要/编号定位；原审核字段、首次完整发布、已发布版本退役缩短有效期、版本/快照单向软删除和索引构建状态保留。已有发布事实不能覆盖，有效期不能延长/清除，所需旧状态未加载时拒绝且不自动读正文。新增0050为新三表安装ALWAYS行UPDATE/DELETE及TRUNCATE保护，复用0049拒绝函数，不改旧内容；降级先排他锁三表再判空，有历史拒绝。无新表/依赖/API，迁移/测试/构建/实际操作均未执行，独立擦除和恢复tombstone仍待，21.6不勾选。
- **历史保护专项预检（W22.4部分）**：reference_history_schema在现有只读共享锁事务中，当前v2/named_0049_0050_guards核对0049/0050六表十二触发器/三函数的形状、ALWAYS、依表OID绑定、invoker/固定配置及原迁移函数体摘要，目录上限4函数/13触发器、每体16KiB；只返回schema.reference_history的范围/计数，漂移/不可读固定拒绝，不调用函数/读业务行或自动修复。其他触发器/权限/历史内容/跨查询原子快照和未来DDL阻止仍false，通用triggers_verified仍false。源码已在发布清单范围，无新表/依赖；预检/数据库/测试/构建均未执行。
- **配置**：`SUPPORTOPS_` 前缀与 `.env`；production 拒绝默认 JWT/加密密钥/示例管理员密码、私网模型端点、非 PostgreSQL 数据库及缺失模型域名允许列表。
- **应用交付模板**：`deploy/runtime` 用批准的基础镜像 digest、uv frozen/pnpm 固定版本构建 API/Worker/Web，非 root/只读容器、外部 DB/Redis、独立维护 profile；Web 同源代理、loopback HTTP、TLS 外层示例和关闭 SSE 缓冲/75 秒超时。迁移持 PostgreSQL 会话 advisory lock、Alembic 复用连接、随后初始化独立 checkpoint；拒绝空库/未知/0017 前的基线，避免跨越含清空数据的历史 0005。应用启动不自动执行业务迁移，所有执行者需使用相同锁入口；旧进程先停止，0040 后重新登录。镜像构建/配置/迁移/真实代理验收均未运行。
- **部署预检**：显式只读preflight profile校验期望环境/非占位秘密/关闭bootstrap与Mock/provider构造、迁移head/业务表/列/checkpoint、有限地址/DNS策略及Redis ping。schema_structure只读批量核对ORM列类型/明确长度/nullable与PK/普通UNIQUE/FK列/目标/动作/NULL语义/时机；查validated、PG18+enforced、支撑索引valid/ready/live/unique/立即性及内部外键触发器启用、会话origin。实际路径只允许public或pg_catalog/public，迁移共用路径规则；同原key的共享事务锁与模板迁移独占锁互斥，不等待，READ COMMITTED取锁后查新快照。SQL5秒/锁1秒/整体60秒，DNS以-I/-B空环境/关闭FD/3秒固定子进程运行。报告仅固定码/计数/版本及明确未核对项，未执行、不解封凭据或调用模型。
- **具名CHECK预检（W22.4部分）**：schema_structure同时核对ORM表级/列内具名CHECK在同public表下存在及类型/validated/PG18+enforced/时机，缺失或无名固定拒绝；目录总上限仍20000。v2新增具名CHECK计数与状态核对标志，当前v3保留；表达式语义仍未验证，不读表达式/业务行或自动DDL。schema_structure纳入新发布源码摘要，旧清单不回填；源码静态检索50处具名声明不代表实际数据库具备，预检/数据库/测试/构建未执行。
- **声明唯一索引预检（W22.4部分）**：同public表/名称核对直接键列顺序、NULL唯一性/部分标志和unique/valid/ready/live/immediate，不支持声明明确拒绝；声明/目录各限1000。v3新增具名索引计数/状态标志，完整定义仍false，WHERE/实际方法/排序/操作符类/collation/include以及非唯一/迁移独有索引未核对，不读表达式原文或业务行、不自动重建。静态8处声明及迁移名称仅源码证据，未运行预检、数据库、测试或构建。
- **维护入口与表定位**：除显式有效路径，还检查包含隐式schema的current_schemas(true)恰为pg_catalog/public，已有pg_temp拒绝。实际操作中preflight/migrate共用public路径与session_replication_role=origin检查，迁移在取锁/DDL前拒绝不符且不自动SET角色。两个入口仍先分派参数，帮助只打印固定说明、未知或混合参数不加载配置/连接依赖并退出2；无参数才实际执行，DNS保留固定内部模式。人工中断固定退出130，不打印原始异常；迁移中断不能推断未提交或自动降级/重放，须独立核对head/checkpoint。工作等待时限不承诺清理硬截止，路径均未运行验收。
- **边界**：结构核对不是完整schema或生产安全认证；CHECK表达式、部分/其他索引、除0049/0050具名保护外的业务触发器体、默认/生成策略、其他迁移独有保护、额外对象、checkpoint结构细节与权限仍未核对，不自动DDL/修复/改search_path。仍缺批准环境/容量、隔离smoke、完整秘密/外层TLS与实际约束验收、企业provider、备份恢复和HA。共享advisory锁不阻止绕过协议的DDL，所有维护者仍用统一入口并停止旧执行者。Worker无虚构HTTP ready，API ready仅DB/Redis且不保证schema head；checkpoint运行时仍调用setup，严格DDL分权未实施。
- **验证入口**：`test_secrets.py`、`test_schema_comments.py`、`tests/api/test_soft_delete.py`、`tests/postgres/`；迁移校验必须使用隔离本地/测试 PostgreSQL。
- **备份入口（W22.6部分）**：独立`deploy/runtime/backup.compose.yml`/`backup.sh`及连接/恢复引用示例，默认帮助、显式单次custom归档同库全部schema；独立非root维护身份，不注入应用秘密，产物manifest/摘要及未完成标记，`pg_restore`仅列目录，无恢复/调度/删除。实际目标/存储/外部文件和密钥引用/保留及RTO/RPO仍待，脚本/容器/备份恢复及测试未运行，不能当已可恢复。
- **离线产物核验**：`deploy/runtime/backup_inspect.py`只用标准库按显式预期核对固定三文件/清单/完成标记/长度/流式SHA-256和前后变化；独立Compose禁网络、根及产物只读，无连接/应用秘密挂载。只报告artifact_matched，不证明恢复/来源真实性/可解压/密钥或外部文件齐备；不执行归档SQL或修改产物，CLI/容器及测试均未运行。
- **恢复引用配置检查**：同一入口显式references模式及独立Compose只读挂载一份私有清单，核对独立摘要/精确字段/类型/版本digest/不透明登记号、外部文件/记忆/法律保留/RTO/RPO关联；缺项返回固定字段，配置完整不表示批准、目标存在、密钥可恢复、加密或实际操作授权，不解引用/读取秘密或修改备份产物。示例保持未知，实际策略及CLI/容器/备份恢复/测试仍待，W22.6保持部分实现。

## 4. 数据模型分组与数量

以下仅统计 `supportops_core.models` 的 66 张业务表，不统计 checkpoint 扩展表和 Alembic 版本表；AgentReleaseEvaluation 为第54张，灰度控制与追加事件为第55、56张，保留草稿/本人申请/未来删除事实结构为第57–59张，受控补偿提案为第60张，导入任务/对象/物化/页回执为第61–64张，创建请求停止事实为第65张，工具发现追加修订为第66张。

| 数据域 | 数量 | 表 |
| --- | --- | --- |
| 身份与组织 | 4 | `tenants`、`users`、`organization_units`、`auth_sessions` |
| 模型与凭据 | 7 | `model_endpoints`、`model_endpoint_versions`、`model_endpoint_models`、`model_endpoint_test_runs`、`model_credentials`、`model_credential_revisions`、`model_probe_jobs` |
| Agent 与工具控制面 | 10 | `agents`、`agent_drafts`、`agent_versions`、`agent_access_grants`、`tool_catalog_entries`、`tool_discovery_revisions`、`agent_audit_events`、`agent_release_evaluations`、`agent_release_rollouts`、`agent_release_rollout_events` |
| 知识与证据 | 14 | `knowledge_sources`、`knowledge_documents`、`knowledge_versions`、`knowledge_acl_entries`、`knowledge_snapshots`、`knowledge_outbox_events`、`knowledge_index_generations`、`knowledge_chunks`、`knowledge_evidence`、`knowledge_ingestion_jobs`、`knowledge_ingestion_objects`、`knowledge_ingestion_materializations`、`knowledge_ingestion_pages`、`knowledge_ingestion_creation_stops` |
| 会话、执行与观测 | 15 | `conversations`、`messages`、`agent_runs`、`run_events`、`run_notification_outbox`、`agent_error_events`、`agent_run_observations`、`agent_runtime_health`、`runtime_service_leases`、`run_attempts`、`tool_invocations`、`model_call_ledger`、`run_scheduler_states`、`runtime_alerts`、`runtime_alert_scan_states` |
| 动作、审批与隔离目标 | 13 | `business_actions`、`action_attempts`、`action_receipts`、`mock_remote_operations`、`mock_support_tickets`、`mock_resource_grants`、`approval_requests`、`approval_decisions`、`approval_consumptions`、`ticket_escalation_packages`、`mock_support_cases`、`mock_ticket_commands`、`action_compensation_proposals` |
| 数据保留与本人申请 | 3 | `retention_policies`、`erasure_requests`、`deletion_tombstones`（当前仅结构，不写实际删除事实） |

计数用于当前结构说明，新增表或调整领域职责后应同步更新，不将该数量作为架构限制。

## 5. 核心链路与必须保持的不变量

### 5.1 发布与对话执行

1. 管理员配置模型、密封凭据并测试能力；保存 Agent 草稿、校验、发布、激活和显式授权。
2. 用户经 API 认证，创建固定 Agent 版本会话；发送时再次检查当前 Agent 使用授权。
3. API 锁定会话完成 Message/Run/Event/通知 Outbox 的幂等事务；提交后 API/Worker 有限分发 Run UUID，失败退避/死信并保留 queued 事实。
4. Worker 幂等领取 Run，加载固定快照，构造统一 Agent 图并读取 checkpoint。
5. Agent 调用固定模型及可用本地工具；正文增量落事件表，API SSE 与 Web 恢复游标连接。
6. 回答完整校验后，在同一产品事务保存助手正文、阶段完成和 Run 终态；观测单独提交。错误或取消按状态机处理。

### 5.2 知识发布与运行检索

来源/文档 → 内容版本与锚点 → 提交/独立非本人审核 → 发布快照 + 知识 Outbox + 审计 → Worker消费关键词分块索引 → search_knowledge按当前ACL/有效期筛选 → Run/attempt绑定证据 → 模型/正文/最终引用复检与产品结果。以上关键词主链路已有实现；embedding/向量、外部CMS与正式检索评测仍待。

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
| P0 | 同会话串行与上下文隔离 | 输入序号、最早轮次门禁、attempt checkpoint、完整预算和来源摘录已有实现 | 长对话/并发/恢复验收暂缓 |
| P0 | 调用时实时授权 | 当前主体/grant/目录策略/撤销/知识 ACL 已复检 | 接入真实业务资源与字段范围 |
| P0 | 配置与执行一致 | 单工具/Run/租户许可、固定实现、Mock审批/一次消费/动作账本已有 | 真实连接器与其授权/幂等契约，不能继承Mock保证 |
| P0 | 完整上下文与预算 | 完整输入保守计量/裁剪/来源摘录、工具证明/时效/字段限制/声明冲突及物理账本已有 | 精确tokenizer/供应商计量、冲突语义及性能验收暂缓 |
| P1 | 可信知识与证据链 | 关键词索引、ACL/有效期、evidence/引用和知识冲突降级已有 | 质量/恶意输入/生命周期安全评测，向量暂缓 |
| P1 | 结构化产品结果 | 消息保存完整结果，刷新可显示引用/建议/升级意向 | 补真实动作/升级流程查询与验收 |
| P1 | 业务诊断与工单闭环 | 固定十节点429 Mock、知识规则、动作/升级包、工单全流程和客服接管已有 | 真实日志/工单获批目标、字段和幂等/回调契约；Mock默认关闭 |
| P1 | 工具执行网关与审批 | 本地网关、持久化显式非本人审批、业务等待、独立动作/对账已有 | MCP/API及真实审批/凭据/故障契约，测试验收暂缓 |
| P1 | 观测与可执行评测 | 有观测和文档基线，无独立质量评测闭环 | 真实 PostgreSQL/队列故障验收；版本化场景评测；事实、引用、授权和业务结果门禁 |
| P1 | 发布治理与灰度 | manifest/固定比较/多维评测门禁/稳定新会话灰度/巡检暂停/停止撤回已有 | W19可信执行器/适用实测及安全/灰度验收，默认不启用灰度 |
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
- Mock 审批和客服接管按用户纠正后的推荐方案：明确授权人员及其隔离项目范围；管理员角色不自动获得业务处理权限。审批指定非本人，客服处理需独立 `ticket_handle`，上下文读取另需 `support_context_read`；知识证据同时校验读取者和原主体当前 ACL，不得扩大申请者权限。

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
