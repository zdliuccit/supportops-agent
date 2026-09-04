## 1. 契约与配置基线

- [x] 1.1 确认并记录平台管理员、员工和 Agent 使用者的规范角色标识，补充本地开发令牌示例
- [x] 1.2 定义 Agent 基础资料与运行配置 Schema v2，覆盖 Prompt、模型绑定、工具绑定、输出策略、调用/超时/并行上限和稳定错误码
- [x] 1.3 定义管理员模型/Agent API、员工目录 API、Agent 路由、会话创建和 Run 响应的请求/响应契约
- [x] 1.4 为模型供应商/协议/能力、Agent 状态、授权主体类型、审计动作和配置版本建立共享枚举及允许转换
- [x] 1.5 定义 ModelEndpoint/Version/Credential 契约、官方与中转站 URL 规则、参数允许范围和连接测试结果 Schema
- [x] 1.6 定义 Tool Registry、`support_context_v1`、`support_answer_v1`、结构化输出策略及平台强制中间件边界

## 2. 数据模型与迁移

- [x] 2.1 实现 Agent 基础资料、AgentDraft、AgentVersion、AgentAccessGrant 和 AgentAuditEvent SQLAlchemy 模型及租户约束
- [x] 2.2 为 Conversation 和 AgentRun 增加 `agent_id`、`agent_version_id`、`model_endpoint_version_id` 关系及同租户一致性索引
- [x] 2.3 创建 Alembic 迁移，先建立模型/Agent 控制面表和可空运行关联，仅为存在旧数据的租户创建只读迁移 Agent
- [x] 2.4 在迁移中回填历史 Conversation 和 AgentRun，验证引用一致后建立非空、唯一和外键约束
- [x] 2.5 在真实 PostgreSQL 上执行升级、数据回填、约束检查、降级策略评审和 Alembic 无漂移验证
- [x] 2.6 实现 ModelEndpoint、ModelEndpointVersion 和 ModelCredential 引用模型，以及不可变版本、唯一性和租户约束
- [x] 2.7 实现 Secret Provider 接口与本地 envelope-encryption provider，主密钥仅来自服务端环境并支持凭据 revision
- [x] 2.8 为 LangGraph PostgreSQL checkpointer 规划独立表/Schema、租户化 thread ID、保留策略和迁移方式

## 3. Agent 生命周期服务

- [x] 3.1 实现运行配置解析、规范化、模型/工具能力校验、平台限额校验、未知 Schema 拒绝和疑似秘密检测
- [x] 3.2 实现租户内 Agent 创建、基础资料、唯一 slug 校验和初始 revision 为 1 的草稿创建事务
- [x] 3.3 实现基于期望 revision 的草稿条件更新及不覆盖内容的并发冲突结果
- [x] 3.4 实现不可变版本发布、单调版本号、规范化内容摘要和版本说明持久化
- [x] 3.5 实现版本激活、历史版本回滚、Agent 停用和允许状态转换校验
- [x] 3.6 实现追加写控制面审计服务及敏感字段排除，并为所有生命周期写操作接入 correlation ID
- [x] 3.7 发布时解析并固定 ModelEndpointVersion 与 ToolDefinition 版本，拒绝跨租户、未验证、停用或能力不兼容绑定
- [x] 3.8 实现 Agent 名称、Logo、描述、欢迎语和建议问题的独立更新与审计，不触发运行版本发布

## 3A. 模型端点与凭据服务

- [x] 3A.1 实现 OpenAI 官方和 OpenAI-compatible 模型端点创建、资料更新、版本保存、停用和审计
- [x] 3A.2 实现 API Key 只写、轮换、masked hint、credential revision 和日志/错误统一脱敏
- [x] 3A.3 实现 Base URL HTTPS、规范化、DNS/IP 复检、私网/保留地址拒绝、重定向限制和生产 allowlist
- [x] 3A.4 实现基础连通、认证、模型调用、streaming、tool calling 和 structured output 分项测试
- [x] 3A.5 实现 ModelEndpointVersion 发布/激活及引用保护，保证旧 AgentVersion 不随端点编辑漂移

## 4. Agent 授权与目录服务

- [x] 4.1 实现服务端平台管理员能力校验，并保证管理接口不接受客户端 tenant 或 role 扩权
- [x] 4.2 实现当前租户用户主体和角色主体的 Agent 授权创建、查询和撤销服务
- [x] 4.3 实现默认拒绝的员工 Agent 目录和安全详情查询，统一 active、版本、租户和授权过滤
- [x] 4.4 在会话创建和消息提交时复用 Agent 使用权检查，并实现撤权后的历史只读语义
- [x] 4.5 为授权变更接入追加写审计，并验证授权目标和 Agent 不得跨租户关联

## 5. 控制面与目录 API

- [x] 5.1 实现 `/v1/admin/agents` 创建、列表和详情接口及分页、状态过滤和统一错误响应
- [x] 5.2 实现 Agent 草稿读取、带 revision 更新和独立配置校验接口
- [x] 5.3 实现版本发布、版本历史、激活/回滚和停用接口，并返回明确的状态及版本标识
- [x] 5.4 实现 Agent 用户/角色授权管理接口和管理员审计摘要查询接口
- [x] 5.5 实现 `/v1/agents` 员工目录与安全详情接口，拒绝 ID 枚举和未授权详情泄露
- [x] 5.6 扩展会话创建、会话响应和 Run 响应契约，要求并返回固定 Agent 及版本信息
- [x] 5.7 实现 `/v1/admin/model-endpoints` 列表、创建、详情、资料修改、版本历史和停用接口
- [x] 5.8 实现模型密钥只写/轮换接口和不含业务数据的连接/能力测试接口
- [x] 5.9 扩展会话与 Run 响应返回安全的动态 Agent 资料及模型端点版本标识，不返回域名、Prompt 或秘密引用

## 6. Agent 绑定运行时

- [x] 6.1 修改会话创建事务，原子校验 Agent 可用性和授权并固定当前激活版本
- [x] 6.2 修改消息与 Run 创建事务，重新校验使用权并复制会话 Agent/版本，不产生失败后的部分消息
- [x] 6.3 修改 Worker 按 Run 版本加载不可变 Agent/模型配置，校验租户、Schema、端点、能力和凭据 revision 一致性
- [x] 6.4 实现 ModelAdapter，按固定配置构造 OpenAI 官方或兼容中转站聊天模型并执行统一脱敏
- [x] 6.5 实现 AgentFactory，将 model、tools、system_prompt、SupportContext、PostgreSQL checkpointer、SupportAnswer 和 middleware 映射到 `create_agent`
- [x] 6.6 验证发布新版本、回滚、停用和撤权不会改变旧会话或运行中 Run 的固定执行语义
- [x] 6.7 实现模型调用 6 次、工具调用 8 次默认上限，以及总超时、最大并行工具、重试、token/成本和平台安全中间件
- [x] 6.8 实现 Tool Registry 与基于 Agent 绑定、租户和用户权限的运行时工具过滤，可信字段只从 SupportContext 注入
- [x] 6.9 实现 `ToolStrategy(SupportAnswer)` 默认结构化输出，并仅对验证支持的端点开放 ProviderStrategy
- [ ] 6.10 实现 PostgreSQL checkpoint 与产品 messages 双层状态边界、恢复、一致性错误和保留策略
- [x] 6.11 为缺失版本、秘密解析、模型超时、能力不兼容、结构化校验和 checkpoint 失败实现稳定终态事件

## 7. Web 管理控制台

- [x] 7.1 扩展前端身份上下文、角色化导航、路由和 API 客户端类型，并为管理路由提供无权限状态
- [x] 7.2 实现 Agent 管理列表、创建入口、分页、加载、空数据、失败和重试界面
- [x] 7.3 实现 Agent 草稿编辑、字段级校验、未发布状态和 revision 冲突保留/刷新交互
- [x] 7.4 实现版本历史、发布、显式立即激活、激活历史版本回滚和停用确认交互
- [x] 7.5 实现用户/角色授权管理和控制面审计摘要界面，明确撤权与停用影响
- [x] 7.6 完成管理表单的键盘操作、焦点管理、状态播报、错误关联和秘密输入提示
- [x] 7.7 实现 `/admin/models` 模型端点列表与详情，覆盖官方/中转站字段、Logo、只写 Key、版本、状态和连接测试
- [x] 7.8 将 Agent 编辑页拆分为“基础信息”和“Agent 配置”，模型从已验证端点中选择，工具从受控目录中选择
- [x] 7.9 展示模型/工具能力兼容性、参数允许范围、将要固定的版本和发布阻断原因

## 8. 员工 Agent 工作台

- [x] 8.1 实现员工可用 Agent 目录、安全详情、动态 Logo/名称/描述、加载、空数据、失败和无可用 Agent 状态
- [x] 8.2 实现 `/agents/:agentId/chat` 新建对话和 `/agents/:agentId/chat/c/:conversationId` 会话路由及 Agent/Conversation 一致性校验
- [x] 8.3 将聊天工作台所有硬编码品牌改为服务端 Agent 资料，并持续显示固定 Agent/模型版本，保留发送、取消和 SSE 重连
- [x] 8.4 实现旧版本提示、使用当前版本新建会话入口，以及停用或撤权后的历史只读状态
- [ ] 8.5 使用管理员和员工本地令牌完成“创建—编辑—发布—授权—选择—对话”的浏览器端到端验证
- [x] 8.6 删除旧 `/chat` 和 `/chat/c/:conversationId` 路由，使 `/` 进入 `/agents`，未匹配路径不执行 Agent 兼容解析
- [x] 8.7 按当前 Agent 查询“最近”会话；删除当前会话后返回该 Agent 新建对话路由

## 9. 测试、文档与交付

- [x] 9.1 添加 Agent/模型配置校验、内容摘要、revision 冲突、不可变版本和状态转换单元测试
- [x] 9.2 添加管理员权限、默认拒绝、用户/角色授权、撤权、跨租户和资源枚举 API 测试
- [x] 9.3 添加会话/Run 固定版本、发布后旧会话不变、停用阻断和失败不产生部分数据的集成测试
- [x] 9.4 添加 Worker 固定 Agent/模型版本、ModelAdapter、AgentFactory、工具过滤、结构化输出、调用上限和重复消费测试
- [ ] 9.5 添加真实 PostgreSQL 历史数据回填与约束测试，确认现有消息、Run 和事件不丢失
- [x] 9.6 运行 Ruff、mypy、后端测试、前端类型检查/构建、Alembic 检查和 OpenSpec 严格校验
- [x] 9.7 更新 README 的角色令牌、Agent 管理、发布/启动、员工选择和已知非目标说明
- [x] 9.8 添加模型 API Key 不可读回、日志脱敏、轮换 revision 和审计无秘密测试
- [x] 9.9 添加中转站 SSRF、DNS 重解析、私网地址、重定向、allowlist、超时和响应大小安全测试
- [x] 9.10 添加 PostgreSQL checkpointer 重启恢复、跨租户隔离、双写失败和历史 Conversation 回归测试
- [ ] 9.11 添加规范 Agent 路由、旧路由不可用、动态品牌、按 Agent 会话过滤和刷新无闪烁浏览器测试
