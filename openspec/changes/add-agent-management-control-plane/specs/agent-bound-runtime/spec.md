## ADDED Requirements

### Requirement: 创建会话时选择 Agent
创建会话请求 MUST 提供 `agent_id`。平台 SHALL 在当前租户和可信主体范围内校验 Agent active、存在激活版本且主体拥有使用权，并在同一事务内把 Agent 及其当前版本固定到新会话。

#### Scenario: 使用可用 Agent 创建会话
- **WHEN** 已认证员工选择目录中的 active Agent 创建会话
- **THEN** 系统创建同时记录 `agent_id` 和当时 `agent_version_id` 的会话

#### Scenario: 使用停用或未授权 Agent 创建会话
- **WHEN** 员工选择已停用、未发布或未授权的 Agent
- **THEN** 系统拒绝创建会话且不产生部分会话数据

#### Scenario: 创建会话未提供 Agent
- **WHEN** 客户端提交不含 `agent_id` 的创建会话请求
- **THEN** 系统返回明确的请求校验错误

### Requirement: 会话固定不可变 Agent 版本
会话 SHALL 在生命周期内固定创建时选择的 `agent_id` 和 `agent_version_id`。管理员发布或激活其他版本 MUST NOT 静默修改已有会话。

#### Scenario: 会话建立后发布新版本
- **WHEN** 管理员在员工会话建立后发布并激活 Agent 新版本
- **THEN** 该会话继续引用原版本，新创建的会话引用新激活版本

#### Scenario: 客户端尝试修改会话版本
- **WHEN** 客户端请求把已有会话切换到另一个 Agent 或版本
- **THEN** 系统拒绝原地切换并要求创建新会话

### Requirement: Run 固定 Agent 与模型执行版本
每个 Agent Run SHALL 复制所属会话的 `agent_id`、`agent_version_id` 和该版本解析的 `model_endpoint_version_id`，并在入队前验证它们一致。Worker MUST 只加载 Run 固定的不可变配置版本，不得读取 Agent 或模型端点当前激活版本替代它。

#### Scenario: 消息创建 Run
- **WHEN** 用户在有效 Agent 会话中提交消息
- **THEN** 系统在消息事务中创建带相同 Agent 和版本标识的 queued Run

#### Scenario: Worker 消费旧版本 Run
- **WHEN** Agent 或模型端点已发布新版本但队列中 Run 固定的是旧版本
- **THEN** Worker 使用旧版本快照执行并保持可审计的 Agent、模型和凭据 revision 标识

#### Scenario: Run 引用缺失或不匹配版本
- **WHEN** Worker 发现 Run 的 Agent 版本不存在、租户不一致或不属于该 Agent
- **THEN** Worker 不执行配置，将 Run 标记为失败并追加稳定错误事件

### Requirement: 受控 LangChain Agent 工厂
Worker SHALL 通过平台维护的 AgentFactory 把固定 AgentVersion 映射为 LangChain `create_agent`，包括模型适配器、工具集合、system prompt、可信 context schema、PostgreSQL checkpointer、结构化响应策略、中间件和稳定图名称。AgentFactory MUST NOT 执行草稿、未知 Schema、UI 上传代码或请求提供的运行配置。

#### Scenario: 执行受支持 Agent 配置
- **WHEN** Worker 收到固定到有效 `langchain_create_agent_v1` 配置和已验证模型端点的 Run
- **THEN** Worker 构造 Agent、使用 Conversation 派生 thread ID、执行模型—工具循环并完成既有 queued、running、事件、assistant message 和 completed 生命周期

#### Scenario: 执行未知 runtime engine
- **WHEN** 固定版本包含 Worker 不支持的 runtime engine、context schema 或 response schema
- **THEN** Worker 失败关闭 Run，不回退到其他 Agent、其他模型或无结构化输出模式

### Requirement: 模型适配与秘密解析
Worker SHALL 根据固定 ModelEndpointVersion 构造 OpenAI 官方或 OpenAI-compatible 聊天模型，并仅在服务端执行时解析 Secret Reference。模型密钥、Authorization Header 和完整供应商错误体 MUST NOT 进入 RunEvent、消息、日志或客户端响应。

#### Scenario: 使用兼容中转站模型
- **WHEN** Run 固定的端点类型为 `openai_compatible` 且使用 Chat Completions
- **THEN** Worker 使用固定 Base URL、远端模型名称和秘密引用构造兼容模型，并通过统一事件协议返回结果

#### Scenario: 秘密解析失败
- **WHEN** Secret Provider 无法返回所需凭据 revision
- **THEN** Worker 不调用外部端点，将 Run 标记为失败并产生脱敏稳定错误码

### Requirement: 工具绑定与可信上下文
Worker SHALL 只向模型暴露 AgentVersion 中启用、服务端已注册、当前租户允许且当前主体有权使用的工具。租户、用户、角色、会话、Run 和 correlation ID SHALL 通过固定 `SupportContext` 注入，MUST NOT 作为模型可自由构造的工具参数。

#### Scenario: 模型选择允许工具
- **WHEN** 模型调用 AgentVersion 已绑定且当前用户有权使用的工具
- **THEN** 工具从 SupportContext 获取可信身份，执行参数校验并将脱敏结果返回 Agent 循环

#### Scenario: 用户无权使用已绑定工具
- **WHEN** AgentVersion 绑定了某工具但当前用户缺少该工具所需业务权限
- **THEN** Worker 不向本轮模型暴露该工具，并记录不含敏感权限详情的过滤事件

### Requirement: 运行上限与结构化回答
每次 Agent Run MUST 应用平台允许范围内的模型调用、工具调用、总超时、最大并行工具、重试、token/成本和结构化输出策略。平台安全中间件 SHALL 优先于管理员配置，并且不得被 Prompt 关闭。

#### Scenario: 达到模型调用上限
- **WHEN** 单次 Run 达到配置的 model call limit
- **THEN** Agent 按配置安全结束或失败，不再调用模型，并生成可理解的终态事件

#### Scenario: 中转站使用 ToolStrategy
- **WHEN** 模型端点只验证了 tool calling 而未验证 provider-native structured output
- **THEN** Agent 使用 `ToolStrategy` 生成并校验 `SupportAnswer`，不尝试未经验证的 ProviderStrategy

### Requirement: 通用模型生成参数分层
Agent 的版本化模型绑定 SHALL 支持受控的通用生成参数覆盖，包括 `temperature`、`max_output_tokens` 以及可选的 `reasoning_effort` 和 `verbosity`。`reasoning_effort` 与 `verbosity` 未显式设置时 SHALL 使用固定模型版本声明的默认值；请求超时、重试次数、调用次数和并行上限仍受模型端点及平台硬限制约束。适配器 MUST 按固定 API 协议映射参数，并跳过未设置或不适用的可选参数，不得将任意客户端字段直接转发给供应商。

#### Scenario: Agent 覆盖模型默认生成参数
- **WHEN** 管理员在 Agent 草稿中设置 `reasoning_effort` 或 `verbosity`
- **THEN** 发布后的 AgentVersion 固化该覆盖值，Worker 调用固定模型时使用该值

#### Scenario: Agent 未设置可选生成参数
- **WHEN** Agent 草稿未设置 `reasoning_effort` 或 `verbosity`
- **THEN** Worker 使用固定 ModelEndpointVersion 的对应默认值；若模型未声明默认值则不发送该参数

#### Scenario: 不支持的参数由适配层处理
- **WHEN** 固定模型协议不支持某个可选生成参数
- **THEN** 适配层不发送未设置或不适用的参数，并保持平台统一的结构化输出和运行上限策略

### Requirement: PostgreSQL 会话检查点
生产运行 SHALL 使用 PostgreSQL-backed LangGraph checkpointer，并使用不可猜测且包含租户边界的 Conversation 派生 thread ID。产品 `messages` 表 SHALL 作为用户可见历史事实源，checkpoint 只保存内部图状态和工具轨迹，两者通过 Conversation 和 Run ID 关联。

#### Scenario: Worker 重启后继续同一会话
- **WHEN** Worker 重启后用户向既有 Agent 会话发送新消息
- **THEN** Agent 从 PostgreSQL checkpoint 恢复该会话内部状态，不与其他租户或 Conversation 的状态混用

#### Scenario: checkpoint 写入失败
- **WHEN** 图执行状态无法持久化
- **THEN** Worker 不把不完整回答标记为 completed，并产生可重试或人工处置的稳定失败状态

### Requirement: 停用后的运行时行为
Agent 停用或使用授权撤销后，平台 MUST 拒绝该 Agent 会话中的新消息和 Run。停用前已经进入 running 的 Run SHALL 按原固定版本继续，除非通过显式取消接口终止。

#### Scenario: 停用后提交新消息
- **WHEN** 用户在已停用 Agent 的历史会话中提交消息
- **THEN** 系统拒绝请求且不持久化用户消息、不创建 Run、不入队

#### Scenario: Agent 在 Run 执行中被停用
- **WHEN** 管理员停用 Agent 时已有 Run 处于 running
- **THEN** Run 继续使用固定版本，系统不因停用隐式改变或终止执行

### Requirement: Agent 版本运行追踪
会话响应、Run 查询和 Run 生命周期事件 SHALL 暴露用于支持与审计的 Agent ID、Agent 版本、模型端点和模型版本标识，但 MUST NOT 暴露完整系统指令、内部配置快照、Base URL、秘密引用或秘密值。

#### Scenario: 查询 Run 版本
- **WHEN** 有权用户或管理员查询 Run
- **THEN** 响应包含该 Run 固定的 Agent ID 和版本号或版本 ID，可与控制面审计关联

### Requirement: 历史运行数据迁移
迁移 SHALL 只为存在无 Agent 历史数据的租户创建只读迁移 Agent 及迁移版本，并将既有会话和 Run 回填到同租户迁移版本。回填完成前不得建立非空约束，且不得把一个租户的数据关联到另一个租户 Agent。

#### Scenario: 迁移已有会话和 Run
- **WHEN** 数据库中存在没有 Agent 关联的 foundation 会话和 Run
- **THEN** 迁移为其所属租户创建或复用只读迁移 Agent 版本并完成一致回填，消息和事件保持不变
