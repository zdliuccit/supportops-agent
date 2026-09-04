## Why

当前基础工程只有一个隐式的占位 Agent，缺少企业管理员可操作的 Agent 定义、模型端点、版本、发布、授权和停用能力，也无法让员工从获授权的 Agent 目录中选择服务对象。模型 API 域名、密钥、远端模型名称与 Agent Prompt 若混在一个配置对象中，会造成密钥泄露、连接配置重复、版本不可追溯和中转站切换困难。需要建立“模型端点管理—Agent 配置发布—LangGraph 运行—Agent 专属聊天”的完整链路。

## What Changes

- 新增租户内 Agent 的稳定身份、草稿配置、不可变发布版本、激活版本和停用状态。
- 将 Agent 基础信息与运行配置分离：名称、Logo、描述、欢迎语作为动态展示资料；Prompt、模型绑定、工具绑定、输出策略和运行限制作为版本化配置。
- 新增独立模型端点管理，支持 OpenAI 官方和 OpenAI-compatible 中转站，管理展示名称、Logo、API 协议、API Base URL、远端模型名称、能力声明、默认参数和密钥引用。
- 模型密钥单独加密或托管于 Secret Provider，API 与 Agent 版本只保存引用和脱敏提示，不返回或复制明文密钥。
- 新增 Agent 配置校验、乐观并发控制、版本说明、发布、回滚和审计记录。
- 新增基于可信身份角色及主体范围的 Agent 管理授权和使用授权，服务端执行租户隔离与访问校验。
- 新增管理端 Agent 列表、创建、编辑、校验、发布、回滚和停用界面，并明确加载、空数据、冲突、校验失败和发布失败状态。
- 新增员工可用 Agent 目录、Agent 详情和选择入口；员工只能看到当前租户内获授权且已激活的 Agent。
- **BREAKING**：创建会话时必须选择可用 Agent；会话和 Run 固定关联 Agent、Agent 版本及解析后的模型端点版本，新版本发布不得静默改变历史会话行为。
- 使用 LangChain `create_agent`/LangGraph 运行选定模型与已授权工具，采用可信运行上下文、PostgreSQL checkpointer、结构化输出策略、模型/工具调用上限、超时和重试边界。
- 将聊天路由直接调整为 `/agents/:agentId/chat` 与 `/agents/:agentId/chat/c/:conversationId`，页面 Logo、Agent 名称、描述和欢迎语全部来自服务端 Agent 资料；删除旧 `/chat` 路由，不提供兼容别名或重定向。
- 保留每次创建、修改、发布、回滚、停用和授权变更的持久化审计证据。
- 本 change 不实现任意 LangGraph 图编辑、管理员上传 Python 工具代码、知识摄取、长期记忆写入、企业 OIDC/SSO、完整工单编排或高风险操作审批；工具只允许从服务端注册目录中绑定。

## Capabilities

### New Capabilities

- `agent-definition-lifecycle`: 管理租户内 Agent 的稳定身份、草稿、校验、不可变版本、发布、激活、回滚、停用和变更审计。
- `model-endpoint-management`: 管理 OpenAI 官方及兼容中转站的模型端点、密钥引用、能力、连接测试、版本和使用状态。
- `agent-access-catalog`: 管理 Agent 使用授权，并向员工提供经过租户、身份、状态和授权过滤的可用 Agent 目录。
- `agent-bound-runtime`: 让会话和 Run 固定关联 Agent、Agent 版本与模型端点版本，并使用 LangChain `create_agent`/LangGraph 执行模型—工具循环。
- `agent-management-console`: 提供管理员配置 Agent 和员工选择 Agent 的角色化 Web 界面及完整关键状态。

### Modified Capabilities

无。现有 foundation 和产品基线 change 尚未归档为主规格；本 change 复用其身份、会话、Run、事件、风险和租户隔离约束，不在此重复声明为主规格修改。

## Impact

- 数据库将新增模型端点/版本/凭据引用、Agent、Agent 草稿/版本、当前激活版本、访问授权和控制面审计模型，并为会话与 Run 增加 Agent 与模型版本关联。
- API 将新增模型端点管理与连通性测试、管理员 Agent 管理/发布/授权接口和员工 Agent 目录接口；创建会话接口将要求选择可用 Agent。
- Web 应用将从单一 Chat 页面扩展为模型管理、Agent 管理控制台、员工 Agent 目录和动态品牌的 Agent 会话工作台。
- Worker 将从隐式占位配置改为按 Run 固定版本解析模型、Prompt、工具、结构化输出和中间件，并执行持久化 LangGraph 运行。
- 现有无 Agent 关联的历史会话需要显式迁移到同租户的只读迁移 Agent 版本，或在迁移前确认当前环境没有需保留的历史数据。
- 后续知识检索、更多业务工具、长期记忆和审批 change 将以 Agent 版本为配置入口，以 Run 固定版本为审计边界。
