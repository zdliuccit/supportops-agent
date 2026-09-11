# SupportOps Agent

面向企业客户、客服人员和技术支持团队的智能技术支持与工单处置 Agent。

当前版本已经提供企业 Agent 控制面与员工工作台：模型端点独立管理、Agent 草稿/版本/发布/激活、显式授权、动态品牌、固定版本会话、LangChain `create_agent`、LangGraph PostgreSQL checkpoint、结构化回答和受控工具目录管理。知识库、长期记忆、真实工单系统和人工审批仍是后续能力。

## 架构

```text
React 管理台 / 员工工作台
        │ JWT + REST / fetch SSE
        ▼
FastAPI ───── PostgreSQL/pgvector ─── Agent/模型版本与审计
        │               ▲
        │ Redis queue   │ Run / Event / Message
        ▼               │
Agent Worker ───────────┘
        │ 固定 AgentVersion + ModelEndpointVersion + credential revision
        ▼
LangChain create_agent / LangGraph checkpoint / OpenAI-compatible endpoint
```

PostgreSQL 是会话、消息、Run 和事件的事实来源；Redis 只承担任务通知和后续短期协调。重复队列投递由数据库状态机保护，未成功入队的 `queued` Run 可由 Worker 恢复扫描重新投递。

## 前置环境

- Python 3.12+
- uv
- Node.js 22+
- pnpm 11.10+
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

1. 打开 `/admin/models`，配置 OpenAI 官方或 OpenAI-compatible 中转站，并执行连接测试。
2. 打开 `/admin/agents`，填写基础资料、选择已验证模型、编辑 Prompt，创建草稿。
3. 在详情页保存草稿并“发布并启动”；本地界面会授权给 `employee` 角色。
4. 打开 `/agents` 选择 Agent。首次发送时才创建固定 Agent 版本的会话。

左侧“最近”只展示当前 Agent 的会话，并支持重新打开、重命名、置顶和删除。删除当前会话会返回该 Agent 的新对话页。旧 `/chat` 路由已删除，不提供默认 Agent 或兼容重定向。

分别启动 API、Worker 和 Web Chat：

```bash
uv run --package supportops-api supportops-api
uv run --package supportops-agent-worker supportops-worker
pnpm run web:dev
```

打开 `http://localhost:5173` 会先进入企业登录页，登录成功后进入 `/agents`。API 文档位于 `http://localhost:8000/docs`。

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

也可以运行 `make check` 执行本 change 的静态检查、测试、前端构建和 OpenSpec 校验。

## 基础 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health/live` | 进程存活检查 |
| `GET` | `/health/ready` | PostgreSQL 与 Redis 就绪检查 |
| `POST` | `/v1/auth/login` | 使用系统用户邮箱密码登录并签发短期 JWT |
| `GET` | `/v1/auth/me` | 返回可信身份与角色 |
| `GET/PATCH` | `/v1/admin/company` | 查询和更新当前租户公司资料 |
| `GET/POST/PATCH/DELETE` | `/v1/admin/organization-units` | 使用名称和上级部门管理当前租户部门树，并返回直属人数 |
| `GET/POST/PATCH` | `/v1/admin/users` | 管理当前租户用户资料、角色和状态 |
| `PUT` | `/v1/admin/users/{id}/password` | 管理员重置用户密码 |
| `GET/POST` | `/v1/admin/model-endpoints` | 管理模型端点与只写密钥 |
| `POST` | `/v1/admin/model-endpoints/{id}/test` | 验证模型连通与能力 |
| `GET/POST` | `/v1/admin/agents` | 管理 Agent 基础资料与草稿 |
| `PATCH` | `/v1/admin/agents/{id}/draft` | 带 revision 保存 Agent 草稿 |
| `POST` | `/v1/admin/agents/{id}/versions` | 发布不可变版本，可显式激活 |
| `PUT` | `/v1/admin/agents/{id}/grants` | 替换用户/角色授权 |
| `GET/PATCH` | `/v1/admin/tools`、`/v1/admin/tools/{tool_id}` | 管理服务端注册工具目录、启停和风险元数据 |
| `GET` | `/v1/agents` | 获取当前主体被授权的 Agent 目录 |
| `GET` | `/v1/conversations?agent_id={id}` | 获取当前 Agent 的历史会话摘要 |
| `POST` | `/v1/conversations` | 使用必填 `agent_id` 创建固定版本会话 |
| `GET` | `/v1/conversations/{id}` | 获取会话与消息 |
| `PATCH` | `/v1/conversations/{id}` | 重命名或切换会话置顶状态 |
| `DELETE` | `/v1/conversations/{id}` | 删除会话及关联消息、Run 和事件 |
| `POST` | `/v1/conversations/{id}/messages` | 幂等提交消息并创建 Run |
| `GET` | `/v1/runs/{id}` | 查询 Run 状态 |
| `POST` | `/v1/runs/{id}/cancel` | 幂等取消 Run |
| `GET` | `/v1/runs/{id}/events` | 订阅支持断点恢复的 SSE |

发送消息必须携带 `Idempotency-Key`。事件流支持 `Last-Event-ID` 请求头或 `cursor` 查询参数。

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
```

## 安全边界

- 本地 JWT 使用 HS256；production 禁止默认密钥和示例管理员密码，并要求显式配置安全凭据。
- API 从已验证令牌建立租户、主体和角色上下文，不接受消息体指定租户。
- 跨租户资源返回与不存在资源一致的 404。
- 日志不得包含访问令牌、数据库凭据或完整消息正文。
- 当前身份完全由本地系统用户、密码哈希和 User UUID JWT 构成，不包含外部身份兼容层。

## 已知边界与后续 change

- 模型连接测试会先验证 DNS、TLS、认证和最小模型调用，再对已声明的 streaming、tool calling 与 structured output 分别发送无业务数据的最小探测请求；任一能力失败会将版本标记为 `partial` 并阻止 Agent 发布。
- SSE 使用数据库轮询确保恢复语义，高并发优化留待可观测数据出现后处理。
- Redis 入队尚未采用事务 Outbox；Worker 会周期扫描 `queued` Run 进行恢复。
- 工具目录现在支持按租户查看、编辑名称/描述/角色/risk level 和启停；运行实现仍必须由服务端注册，当前 `support_ticket_lookup` 尚未连接真实工单数据，另提供 `current_identity_summary` 内置工具。
- 下一步依次建设知识生命周期与检索、真实工具网关、工单处置和人工审批。
