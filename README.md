# SupportOps Agent

面向企业客户、客服人员和技术支持团队的智能技术支持与工单处置 Agent。

当前版本已经提供企业 Agent 控制面与员工工作台：模型端点及能力测试、Agent 草稿/版本/发布/激活、显式授权、企业组织与动态品牌、固定版本会话、LangChain `create_agent`、LangGraph PostgreSQL checkpoint、可恢复流式回答、工具目录、知识生命周期和 Analytics。

**当前阶段是“控制面 + 单 Agent 执行底座”，尚未完成知识检索、业务诊断与工单处置闭环。** 知识来源、版本、ACL 和发布快照已实现基础管理；向量/混合检索、可信引用、长期记忆、真实业务工具、MCP 网关、人工审批与动作结果复核仍待建设。

本文描述 2026-10-12 当前工作区的实现。详细模块职责、数据模型、约束与演进差距见项目级 [AGENTS.md](AGENTS.md)，专题文档见 [docs/README.md](docs/README.md)。“已实现”表示代码存在，不代表本次文档更新完成了生产验收。

## 当前实现架构

项目采用模块化单体：**3 个应用 + 1 个共享核心包**，API 与 Worker 分进程运行。按职责划分为 **12 个逻辑能力模块**；当前 ORM 定义 **29 张业务表**，不含 LangGraph checkpoint 和 Alembic 管理表。

```text
React 管理台 / 员工工作台
        │ JWT + REST / fetch SSE
        ▼
FastAPI ───── PostgreSQL/pgvector ─── 身份/配置/知识/版本与审计
        │               ▲
        │ Redis queue   │ Run / Event / Message
        ▼               │
Agent Worker ───────────┘
        │ 固定 AgentVersion + ModelEndpointVersion + 本轮 credential revision
        ▼
LangChain create_agent / LangGraph checkpoint / OpenAI-compatible endpoint
```

PostgreSQL 是会话、消息、Run 和事件的事实来源；Redis 只承担任务通知和后续短期协调。重复队列投递由数据库状态机保护，未成功入队的 `queued` Run 可由 Worker 恢复扫描重新投递。

会话创建时固定 Agent 版本，Agent 版本固定模型端点版本；凭据 revision 在每轮 Run 创建时固定。产品消息与图内部 checkpoint 分开保存。API 执行身份、控制面、消息与事件接口及模型端点探测，聊天 Agent 推理由 Worker 执行。

### 十二个模块概览

| 编号 | 模块 | 状态 | 已实现能力与主要边界 |
| --- | --- | --- | --- |
| M01 | Web 产品入口与交互工作台 | 已实现 | 企业管理、Agent/模型/工具/知识页面、Analytics、普通/独立聊天、SSE 恢复；不是真实业务闭环验收 |
| M02 | API 接入与公共契约 | 已实现 | FastAPI、身份依赖、CORS、关联 ID、异常响应、分页、OpenAPI、存活/就绪检查 |
| M03 | 身份、租户与企业组织 | 已实现 | 系统用户登录、JWT、实时用户角色、公司品牌、用户与部门管理；尚无 SSO/委托 OAuth |
| M04 | 模型端点与能力验证 | 已实现 | 多模型端点、发现、版本、凭据轮换、能力探测与测试历史；尚无动态选型和故障切换 |
| M05 | Agent 控制面与授权 | 已实现 | 草稿 revision、严格配置与摘要、发布/激活/回滚、停用、用户/角色授权与审计 |
| M06 | 工具目录与内置工具 | 部分实现 | 服务端注册、目录元数据、绑定/角色过滤、身份工具；工单查询占位，无 MCP 执行网关 |
| M07 | 知识生命周期与 ACL | 基础已实现 | 来源、内容版本、审核状态、发布/失效/回滚、ACL、有效快照、知识 Outbox、Mock 导入；未接入检索 |
| M08 | 会话与产品消息 | 已实现 | 固定版本会话、历史/置顶/重命名/软删除、消息幂等、Run 正文快照；未保存完整引用/升级结果 |
| M09 | Agent 推理与短期记忆 | 基础主链路已实现 | `create_agent`、固定模型适配、结构化回答、工具/模型次数限制、checkpoint；无专业诊断编排 |
| M10 | Worker、队列与事件恢复 | 基础已实现 | Redis 通知、行锁领取、Run 状态机、持久化正文、SSE 游标、queued 扫描；无 running 崩溃恢复 |
| M11 | 观测、健康与 Analytics | 基础已实现 | 模型/工具 Trace、错误、Token、估算成本、延迟、健康读模型与排行；无独立质量评测平台 |
| M12 | 数据、配置、凭据与基础设施 | 基础已实现 | ORM/Alembic、软删除、密钥密封、生产配置约束、本地 PostgreSQL/Redis；无完整生产部署/Vault |

每个模块的代码入口、数据表、实现细节和现有测试文件见 [AGENTS.md 模块明细](AGENTS.md#3-十二个模块的实现明细)。逻辑模块不等于 12 个微服务或 12 个 Agent。

## 前置环境

- Python 3.12+
- uv
- Node.js 22+
- pnpm 11.10.0（以 `packageManager` 为准）
- Docker 与 Docker Compose

## 本地启动

安装依赖并准备配置：

```bash
cp .env.example .env
uv sync --all-packages
pnpm install
pnpm rebuild esbuild
```

启动 PostgreSQL/pgvector 和 Redis，然后执行迁移：

```bash
docker compose -f deploy/docker-compose.yml up -d
uv run --package supportops-api alembic upgrade head
```

默认 PostgreSQL 使用标准地址 `localhost:5432`，本地开发用户名为 `agent`、密码为 `agent_dev`；端口可通过 `SUPPORTOPS_POSTGRES_PORT` 修改。以上凭据仅限本地开发，生产环境必须替换。Redis 默认使用 `6379`。

首次启动 API 时，系统会在数据库没有用户且 `SUPPORTOPS_BOOTSTRAP_ADMIN_ENABLED=true` 时，按 `.env` 中的配置幂等创建默认公司和未分配部门的管理员。密码仅保存为带随机盐的 scrypt 哈希；生产环境会拒绝本地示例密码。

规范角色标识为：

- `platform_admin`：管理模型、Agent、版本、授权和审计。
- `employee`：企业员工，可使用明确授权给本人或该角色的 Agent。
- `agent_user`：可用于面向外部集成的 Agent 使用者角色。
- `customer`：保留的客户角色，同样只有匹配显式授权时才可使用 Agent。

Web 只支持系统用户的邮箱密码登录，不再提供开发令牌、外部 subject 自动映射或兼容入口。登录成功后 JWT 的 `sub` 是 User UUID，用户被停用后已有令牌也会立即失效。

首次使用请按以下顺序操作：

1. 登录后打开 `/agent-management/models`，配置 OpenAI 官方或 OpenAI-compatible 端点，并执行连接测试。
2. 打开 `/agent-management/agents`，填写基础资料、选择已验证模型、编辑 Prompt，创建草稿。
3. 在详情页保存草稿并“发布并启动”；本地界面会授权给 `employee` 角色。
4. 打开 `/agents` 选择 Agent。首次发送时才创建固定 Agent 版本的会话。

左侧“最近”只展示当前 Agent 的会话，并支持重新打开、重命名、置顶和删除。删除当前会话会返回该 Agent 的新对话页。旧 `/chat` 路由已删除，不提供默认 Agent 或兼容重定向。

分别启动 API、Worker 和 Web Chat：

```bash
uv run --package supportops-api supportops-api
uv run --package supportops-agent-worker supportops-worker
pnpm run web:dev
```

打开 `http://localhost:5173` 时，未登录用户会进入企业登录页，登录成功后默认进入 `/agents`。API 文档位于 `http://localhost:8000/docs`。

### Web 路由

Web 页面路径与 REST 的 `/v1/admin/*` 路径不同：

| 功能 | 页面路径 |
| --- | --- |
| 登录 / Agent 目录 | `/login`、`/agents` |
| Agent 列表 / 详情 | `/agent-management/agents`、`/agent-management/agents/:agentId` |
| 模型列表 / 详情 | `/agent-management/models`、`/agent-management/models/:modelId` |
| 工具目录 | `/agent-management/tools` |
| 公司 / 用户 / 部门 | `/enterprise/company`、`/enterprise/users`、`/enterprise/departments` |
| 知识列表 / 详情 | `/knowledge`、`/knowledge/:documentId` |
| Analytics | `/analytics/dashboard`、`/analytics/rankings`、`/analytics/agents`、`/analytics/agents/:agentId`、`/analytics/runs`、`/analytics/errors` |
| 聊天 / 历史会话 | `/agents/:agentId/chat`、`/agents/:agentId/chat/c/:conversationId` |
| 独立聊天 / 历史会话 | `/agents/:agentId/chat/standalone`、`/agents/:agentId/chat/standalone/c/:conversationId` |

旧管理 Dashboard 页面保留重定向到 Analytics；不存在 `/admin/models`、`/admin/agents` Web 页面。

查看 **Worker 控制台**：`local/dev` 环境下，每次模型调用只打印“LLM 开始”“LLM 输出中...”和“LLM 结束”。“输出中...”仅在收到首个模型增量时打印一次，后续增量不刷日志；失败时显示“LLM 结束（失败）”。一次 Agent Run 可能调用模型多次，可通过 `call_index` 区分。

这些状态日志不包含正文或逐块详情；生产及其他环境默认关闭。可通过 `SUPPORTOPS_LLM_STREAM_LOG_ENABLED=true/false` 显式开关。修改代码或开关后需要重启 Worker；API 不执行聊天 Agent 推理，但模型发现与连接/能力测试会调用模型端点。

## 验证命令

```bash
uv run ruff check .
uv run mypy
uv run pytest
pnpm run web:type-check
pnpm run web:build
openspec validate establish-enterprise-support-agent-foundation --strict
```

真实 PostgreSQL 迁移验证：

```bash
uv run --package supportops-api alembic upgrade head
uv run --package supportops-api alembic check
```

浏览器 E2E（首次使用按需安装 Playwright Chromium）：

```bash
pnpm --dir apps/web_chat exec playwright install chromium
pnpm --dir apps/web_chat run e2e
```

真实 PostgreSQL 集成测试（**先确认 `.env`/环境配置指向隔离的本地或测试数据库**）：

```bash
SUPPORTOPS_RUN_POSTGRES_TESTS=1 uv run pytest -q tests/postgres
```

默认 Python API/Worker 测试主要使用 SQLite、FakeRedis 和模型替身；E2E 使用 API 路由桩，覆盖 Analytics、路由与聊天交互。真实 PostgreSQL 用例覆盖数据字典注释、软删除迁移和 checkpoint 重连隔离，需要显式开启，并会读取当前连接配置；没有自动保证不连接生产库的隔离机制。

也可以运行 `make check` 执行仓库现有静态检查、默认测试、前端构建和指定 OpenSpec 校验。以上命令是验证入口，不代表本次文档更新已经执行；它们也不替代真实队列故障、多 Worker 并发或外部业务工具验收。

## 基础 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health/live` | 进程存活检查 |
| `GET` | `/health/ready` | PostgreSQL 与 Redis 就绪检查 |
| `POST` | `/v1/auth/login` | 使用系统用户邮箱密码登录并签发短期 JWT |
| `GET` | `/v1/auth/me` | 返回可信身份与角色 |
| `GET/PATCH` | `/v1/admin/company` | 查询和更新当前租户公司资料 |
| `GET/POST` | `/v1/admin/organization-units` | 查询完整部门树或创建部门，返回直属人数 |
| `PATCH/DELETE` | `/v1/admin/organization-units/{unit_id}` | 编辑或软删除指定部门 |
| `GET/POST` | `/v1/admin/users` | 分页查询或创建当前租户用户 |
| `GET/PATCH` | `/v1/admin/users/{user_id}` | 查询或编辑用户资料、角色和状态 |
| `PUT` | `/v1/admin/users/{id}/password` | 管理员重置用户密码 |
| `GET/POST` | `/v1/admin/model-endpoints` | 管理模型端点与只写密钥 |
| `GET` | `/v1/admin/model-endpoints/presets` | 获取供应商预设 |
| `POST` | `/v1/admin/model-endpoints/discover` | 发现远端模型 |
| `PUT` | `/v1/admin/model-endpoints/{id}/configuration` | 保存端点及其模型配置 |
| `POST/GET` | `/v1/admin/model-endpoints/{id}/models/{model_id}/tests` | 启动能力测试、分页查询测试历史 |
| `GET` | `/v1/admin/model-endpoints/test-runs/{test_run_id}` | 查询模型测试任务状态 |
| `POST` | `/v1/admin/model-endpoints/{id}/test` | 验证模型连通与能力 |
| `GET/POST` | `/v1/admin/agents` | 管理 Agent 基础资料与草稿 |
| `PATCH` | `/v1/admin/agents/{id}/draft` | 带 revision 保存 Agent 草稿 |
| `POST` | `/v1/admin/agents/{id}/versions` | 发布不可变版本，可显式激活 |
| `PUT` | `/v1/admin/agents/{id}/grants` | 替换用户/角色授权 |
| `GET/PATCH` | `/v1/admin/tools`、`/v1/admin/tools/{tool_id}` | 管理服务端注册工具目录、启停和风险元数据 |
| `GET/POST` | `/v1/admin/knowledge/sources` | 管理租户级知识来源 |
| `GET/POST` | `/v1/admin/knowledge/documents` | 分页筛选知识文档或创建文档 |
| `GET/PATCH/DELETE` | `/v1/admin/knowledge/documents/{id}` | 查询文档、编辑治理元数据或软删除归档 |
| `GET/POST` | `/v1/admin/knowledge/documents/{id}/versions` | 查看和创建不可变知识版本 |
| `POST` | `/v1/admin/knowledge/versions/{id}/submit-review`、`/publish`、`/retire` | 提交审核、发布或失效知识版本 |
| `PUT` | `/v1/admin/knowledge/documents/{id}/acl` | 替换知识正文访问 ACL |
| `GET` | `/v1/admin/knowledge/documents/{id}/snapshot` | 读取当前主体可访问的有效知识快照 |
| `POST` | `/v1/admin/knowledge/mock-import` | 幂等导入 P0 Mock 知识清单 |
| `GET` | `/v1/admin/knowledge/audit-events` | 分页查询知识生命周期审计事件 |
| `POST` | `/v1/admin/knowledge/documents/{id}/rollback/{source_version_id}` | 基于历史版本创建知识回滚版本 |
| `GET` | `/v1/admin/dashboard/summary`、`/timeseries`、`/runs`、`/errors` | 查询当前租户运行概览、趋势、运行和错误聚合 |
| `GET` | `/v1/admin/dashboard/runs/{run_id}/trace` | 查询单次 Run 的父子追踪 |
| `GET` | `/v1/admin/system/dashboard/summary`、`/timeseries`、`/agents/status`、`/rankings` | 查询当前租户系统级运行概览、Agent 状态与排行 |
| `GET` | `/v1/admin/analytics/errors`、`/v1/admin/analytics/errors/{event_id}` | 分页查询错误事件与详情 |
| `GET` | `/v1/agents` | 获取当前主体被授权的 Agent 目录 |
| `GET` | `/v1/conversations?agent_id={id}` | 获取当前 Agent 的历史会话摘要 |
| `POST` | `/v1/conversations` | 使用必填 `agent_id` 创建固定版本会话 |
| `GET` | `/v1/conversations/{id}` | 获取会话与消息 |
| `PATCH` | `/v1/conversations/{id}` | 重命名或切换会话置顶状态 |
| `DELETE` | `/v1/conversations/{id}` | 软删除会话，取消未完成 Run，保留消息、事件和诊断历史 |
| `POST` | `/v1/conversations/{id}/messages` | 幂等提交消息并创建 Run |
| `GET` | `/v1/runs/{id}` | 查询 Run 状态 |
| `POST` | `/v1/runs/{id}/cancel` | 幂等取消 Run |
| `GET` | `/v1/runs/{id}/events` | 订阅支持断点恢复的 SSE |

发送消息必须携带 `Idempotency-Key`。事件流支持 `Last-Event-ID` 请求头或 `cursor` 查询参数。

Agent 通过 LangGraph 模型消息流提取结构化回答的 `answer` 正文，生成过程中约每 50ms
合并并持久化 `text.delta`；结构化校验重试改写正文时使用 `text.replace`，工具参数、推理
过程和其他结构化字段不进入正文流。最终回答仍经过完整结构校验后才落库为消息。
会话详情的 `latest_run` 返回最新 Run 的状态、累计正文及同一快照的 `last_event_id`。
前端使用 Fetch ReadableStream 持续读取 SSE，收到 `text.delta` 就直接追加正文，收到
`text.replace` 就整体替换正文，不设置打字速度、拆字符播放或等待播放完成。刷新页面直接
恢复服务端已生成的正文，再从快照游标订阅原 Run 的后续事件；已完成的回答直接显示。
已请求停止（`cancelling`）或已取消（`cancelled`）的回答刷新后仅保留已有正文，不重建 SSE 订阅。
断线按 0.5–5 秒退避自动重连，连续失败后保留手动重试和停止入口。模型端点需具备流式
输出能力才能在生成期间返回增量；上游一次返回全部内容时，前端也一次显示全部内容。

所有现有业务删除入口统一采用软删除：会话、模型端点、部门和知识文档使用 `deleted_at`
标记，默认列表、详情和分页总数排除已删除资源，后续编辑与发送也返回 404。关联消息、
版本、凭据、执行观测及审计历史保留，管理员运行诊断继续保留执行事实。会话删除时取消
尚未完成的执行；模型停用、固定引用和部门非空等原有删除限制保持有效。授权与知识 ACL
整体替换时软删除旧条目，旧授权立即失效。活跃记录的唯一约束允许删除后重新创建同名
资源。内部历史查询可显式使用 `execution_options(include_deleted=True)`，不提供面向客户端的
查询绕过参数。升级已有数据库需先执行 `uv run alembic upgrade head`，再启动新版服务。

## 仓库结构

```text
apps/
├── api/                 FastAPI 接口
├── agent_worker/        Redis 队列消费者与 Run 执行器
└── web_chat/            React/TypeScript 用户聊天端
packages/
└── support_core/        配置、身份、模型、服务、队列与 Worker 运行时
migrations/              Alembic 数据库迁移
deploy/                  本地基础设施
tests/                   API 与 Worker 集成测试
openspec/                产品基线与变更规格
docs/                    专题实现文档
architecture-diagrams/   2026 目标架构图
AGENTS.md                项目工作指南与十二个模块的实现明细
```

## 安全边界

- 本地 JWT 使用 HS256；production 禁止默认密钥和示例管理员密码，并要求显式配置安全凭据。
- API 从已验证令牌建立租户、主体和角色上下文，不接受消息体指定租户。
- 跨租户资源返回与不存在资源一致的 404。
- 日志不得包含访问令牌、数据库凭据或完整消息正文。
- 当前身份完全由本地系统用户、密码哈希和 User UUID JWT 构成，不包含外部身份兼容层。
- API 每次鉴权使用数据库实时角色，但 Worker 当前仍使用 Run 创建时角色快照；调用时授权撤销尚未贯通。
- 前端当前使用 localStorage 保存访问令牌，无 refresh-token 流程；登录页固定预填值需要在生产使用前移除。

## 2026 目标架构与当前差距

以下图片是根据公开工程实践归纳的**目标参考设计**，不是当前部署图或完成证明。业务模块可以先在现有执行图中实现，专业 Agent、独立服务和动态模型路由按评测收益决定。

![企业级支持 Agent 生产架构：2026 修订参考设计](architecture-diagrams/enterprise-support-agent-architecture-2026-10-12.png)

| 目标能力 | 当前已有 | 仍需补齐 |
| --- | --- | --- |
| 持久化 Runtime 与编排 | 固定版本 Run、产品事件、checkpoint、执行超时 | 独立心跳、Run 租约/尝试记录、running 恢复、同会话串行、业务流程分支 |
| Context 与知识证据 | 会话历史、知识版本/ACL/有效快照 | 完整上下文预算、索引消费、检索、证据组织、引用校验与持久化 |
| Model Router | 多端点控制面、能力验证、固定版本模型适配 | 按调用能力/成本/数据约束选型、受控故障切换 |
| 诊断与工单 | 通用 Agent、占位工单查询 | 真实受限日志与状态工具、场景诊断、升级包、幂等建单 |
| 工具 Gateway 与 Policy | 静态注册工具、目录、绑定和角色过滤 | 动态策略统一、实时用户/资源授权、MCP/API 连接器、调用时限制与撤销 |
| 人工审批与动作复核 | 审批配置字段、结构化回答 Schema | 可恢复审批状态、参数绑定、执行前复检、业务回执、幂等与未知结果对账 |
| 观测与质量反馈 | Trace、错误、Token/估算成本、延迟、Dashboard、文档评测基线 | 全程动作审计、真实故障验收、可执行质量评测和发布门禁 |
| 凭据与隔离 | 本地密封、历史 revision、生产配置校验 | Vault/KMS、短期委托凭据、连接器隔离及生产运维能力 |

### 已知实现边界

- 模型连接测试会先验证 DNS、TLS、认证和最小模型调用，再对已声明的 streaming、tool calling 与 structured output 分别发送无业务数据的最小探测请求；任一能力失败会将版本标记为 `partial` 并阻止 Agent 发布。
- SSE 使用数据库轮询确保恢复语义，高并发优化留待可观测数据出现后处理。
- 主要业务列表使用统一服务端分页；工具目录、知识来源和知识版本等接口当前仍返回整集，普通列表分页规范尚未全量统一。
- Redis 入队尚未采用事务 Outbox；Worker 会周期扫描 `queued` Run 进行恢复。
- Worker 当前在单个消费循环中等待 Run 完成后才更新服务心跳；长任务可能超过 15 秒服务租约。服务租约不是 Run 执行租约，`running/cancelling` 在进程崩溃后尚无恢复收敛机制。
- 消息提交幂等与 Run 领取锁不保证同会话执行互斥；历史消息加载尚未按本轮输入截断，多 Worker 下需补齐会话执行顺序。
- 工具目录现在支持按租户查看、编辑名称/描述/角色/risk level 和启停；运行实现仍必须由服务端注册，当前 `support_ticket_lookup` 尚未连接真实工单数据，另提供 `current_identity_summary` 内置工具。
- 目录启停当前用于新版本绑定；运行时角色过滤读取静态注册表和 Run 角色快照。审批配置与单工具调用限额还未形成完整执行门禁，不能宣称已具备高风险动作治理。
- 知识生命周期已具备来源、版本、ACL、发布快照和 Mock 导入能力；目前尚未实现向量索引、Rerank、自动知识更新或外部 CMS 适配。
- 知识发布已有自己的 Outbox 表，但无索引消费者；它与尚未实现的 Run 入队 Outbox 是两条独立链路。
- 输入/成本预算当前仅估算本轮输入和单次最大输出，没有覆盖完整历史、工具返回与多次模型调用；结构化回答目前只把 `answer` 保存为产品消息。
- 产品风险规范采用 R0–R4，工具实现采用 `low/medium/high`，真实业务动作接入前需明确映射。

### 建议实施顺序

1. **执行与授权基础**：心跳解耦、Run 租约/恢复、同会话互斥、实时授权、工具策略和完整预算。
2. **可信回答**：消费知识发布事件、受权限控制的检索、证据/引用校验、结构化产品结果。
3. **支持业务闭环**：选择一个已确认场景，接入只读诊断工具、工单查询、幂等建单和升级包。
4. **受控副作用与质量门禁**：统一工具执行入口、持久化审批、批准后复检、执行后业务复核与对账、可执行场景评测。
5. **按需扩展**：根据评测再选择专业 Agent、多模型路由、长期记忆与独立部署，不将目标图中的每个框直接拆成服务。

详细差距与维护约束见 [AGENTS.md](AGENTS.md#6-对照-2026-目标设计的差距与实施优先级)。
