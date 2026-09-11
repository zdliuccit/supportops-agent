## ADDED Requirements

### Requirement: 角色化控制台导航
Web 应用 SHALL 根据服务端确认的主体能力展示员工 Agent 目录和管理控制台入口，但 MUST NOT 把客户端路由或隐藏导航作为授权边界。

#### Scenario: 平台管理员进入应用
- **WHEN** 具备平台管理员能力的用户完成身份初始化
- **THEN** 页面显示 Agent 管理入口和员工工作台入口

#### Scenario: 普通员工访问管理路由
- **WHEN** 普通员工直接打开 Agent 管理 URL
- **THEN** 页面显示无权访问状态且后端拒绝管理数据请求

### Requirement: Agent 管理列表
管理控制台 SHALL 展示当前租户 Agent 的名称、slug、状态、草稿 revision、激活版本和更新时间，并支持加载、空数据、分页、错误和无权限状态。

#### Scenario: 租户尚无 Agent
- **WHEN** 管理员打开空的 Agent 管理列表
- **THEN** 页面解释尚未配置 Agent并提供创建入口

#### Scenario: 管理列表加载失败
- **WHEN** Agent 列表 API 失败
- **THEN** 页面保留明确错误信息并提供重试，不呈现其他租户缓存数据

### Requirement: 独立模型端点管理界面
管理控制台 SHALL 提供独立模型端点页面，支持 OpenAI 官方与 OpenAI-compatible 中转站，编辑连接名称、Base URL 和只写密钥；连接下每个模型只编辑模型名称与默认 `{}` 的 JSON 扩展对象，并可独立测试。

#### Scenario: 创建官方模型配置
- **WHEN** 管理员选择 OpenAI 官方并填写模型名称与 API Key
- **THEN** 页面使用只读官方 API 域名、保存端点并提示执行连接测试

#### Scenario: 创建中转站模型配置
- **WHEN** 管理员选择 OpenAI-compatible 中转站
- **THEN** 页面要求填写 HTTPS Base URL、远端模型名称、JSON 扩展对象和 API Key，并展示生产 allowlist 与数据合规提示

#### Scenario: 重新编辑已保存密钥
- **WHEN** 管理员重新打开已有模型端点
- **THEN** 页面只显示已配置状态、脱敏尾号与轮换入口，不回填或展示原密钥

#### Scenario: 模型连接测试
- **WHEN** 管理员发起测试
- **THEN** 页面分别展示认证、模型调用、streaming、tool calling 和 structured output 的验证状态及脱敏错误

### Requirement: Agent 基础信息独立编辑
管理控制台 SHALL 将 Agent 基础信息与运行配置分区。基础信息包括名称、Logo、slug、描述、欢迎语和建议问题；除稳定 slug 外，资料修改保存后立即反映到目录和所有聊天页面，并显示已审计状态而不是未发布运行变更。

#### Scenario: 更新 Agent Logo 和名称
- **WHEN** 管理员保存有效的新名称与 Logo
- **THEN** Agent 目录、聊天侧栏、空会话状态和页面标题读取并展示新资料，不改变已有 AgentVersion

### Requirement: Agent 草稿编辑与校验
管理控制台 SHALL 提供版本化运行配置表单，包括 system prompt、从模型端点目录选择的 model、受控工具选择、生成参数、结构化响应策略、模型/工具调用上限、总超时和并行限制。保存时携带 revision，并在发布前展示服务端字段级校验结果和将要固定的模型/工具版本。

#### Scenario: 保存有效草稿
- **WHEN** 管理员编辑字段并使用当前 revision 保存
- **THEN** 页面显示新的 revision 和未发布变更状态，不误报为已激活

#### Scenario: 草稿并发冲突
- **WHEN** 保存返回 revision 冲突
- **THEN** 页面保留本地输入、显示服务端当前 revision，并允许管理员刷新或对比后重新处理

#### Scenario: 草稿校验失败
- **WHEN** 服务端返回一个或多个字段级问题
- **THEN** 页面将问题关联到对应字段并阻止发布动作

#### Scenario: 选择能力不兼容模型
- **WHEN** Agent 启用了工具或结构化输出，但所选模型端点未通过对应能力验证
- **THEN** 页面显示模型绑定字段错误并阻止发布，不静默关闭 Agent 能力

### Requirement: 发布、激活与版本历史交互
管理控制台 SHALL 明确区分保存草稿、发布版本和激活版本，展示不可变版本历史、版本说明、发布者、时间和当前激活标识，并提供显式回滚操作。

#### Scenario: 发布并激活新版本
- **WHEN** 管理员确认发布有效草稿并明确选择立即激活
- **THEN** 页面先创建不可变版本，再激活该版本，并展示两个操作的最终状态和审计关联信息

#### Scenario: 回滚需要确认
- **WHEN** 管理员选择历史版本作为 active version
- **THEN** 页面展示目标版本及当前版本、要求明确确认，并在成功后刷新激活标识

### Requirement: Agent 停用与授权管理交互
管理控制台 SHALL 允许管理员查看和变更 Agent 使用授权，并在停用或撤销授权前说明对新会话、新消息和历史只读访问的影响。

#### Scenario: 停用 Agent
- **WHEN** 管理员确认停用 active Agent
- **THEN** 页面调用停用接口、显示 disabled 状态，并从员工目录结果中移除该 Agent

#### Scenario: 撤销用户授权
- **WHEN** 管理员确认撤销某用户的 Agent 使用权
- **THEN** 页面刷新授权列表并说明该用户的历史会话仍可只读访问

### Requirement: 员工 Agent 目录与选择
员工工作台 SHALL 以可访问的卡片或列表展示服务端返回的可用 Agent，并允许员工查看安全详情后选择 Agent 创建新会话。客户端 MUST NOT 展示或接受任意 Agent ID 作为目录授权替代。

#### Scenario: 员工选择 Agent
- **WHEN** 员工在可用目录中选择一个 Agent 并开始工作
- **THEN** 客户端使用该 Agent ID 创建会话并进入显示 Agent 名称和固定版本的聊天工作台

#### Scenario: 员工没有可用 Agent
- **WHEN** Agent 目录为空
- **THEN** 页面显示无可用 Agent 的解释和联系管理员提示，不自动选择隐藏或停用 Agent

### Requirement: Agent 会话工作台状态
聊天工作台 SHALL 使用 `/agents/:agentId/chat` 和 `/agents/:agentId/chat/c/:conversationId` 作为规范路由，并在现有加载、空会话、发送中、运行中、失败、取消和 SSE 重连状态基础上，动态显示服务端返回的 Agent Logo、名称、描述、欢迎语和固定运行版本。Agent 停用或授权撤销 SHALL 显示历史只读状态，不丢失已有消息。

#### Scenario: 打开 Agent 新建对话路由
- **WHEN** 用户打开有权访问的 `/agents/:agentId/chat`
- **THEN** 页面先加载 Agent 资料，再稳定展示该 Agent 的 Logo、名称、欢迎语、建议问题和按 Agent 过滤的最近会话，不闪现旧版硬编码资料

#### Scenario: 打开 Agent 具体会话路由
- **WHEN** 用户打开 `/agents/:agentId/chat/c/:conversationId`
- **THEN** 页面校验会话属于路由 Agent、恢复消息和流式状态，并显示该会话固定的 Agent/模型版本标识

#### Scenario: 路由 Agent 与会话不一致
- **WHEN** URL 中的 Agent ID 与 Conversation.agent_id 不一致
- **THEN** 客户端根据服务端资源隐藏策略跳转到规范 URL 或显示不可用状态，不以 URL 参数覆盖会话真实绑定

#### Scenario: 访问应用根路径
- **WHEN** 用户访问 `/`
- **THEN** 页面进入 `/agents` 员工 Agent 目录，由用户明确选择可用 Agent

#### Scenario: 访问已移除的旧 Chat URL
- **WHEN** 用户访问 `/chat` 或 `/chat/c/:conversationId`
- **THEN** 路由按未匹配路径处理，不解析默认 Agent、不读取旧会话进行跳转，也不保留兼容别名

#### Scenario: 旧会话对应非当前版本
- **WHEN** 用户打开的会话固定版本早于 Agent 当前激活版本
- **THEN** 页面明确显示该会话使用历史版本，并提供使用当前版本新建会话的入口

#### Scenario: 会话变为只读
- **WHEN** Agent 被停用或用户授权被撤销
- **THEN** 页面保留历史消息、禁用发送控件并显示可理解的只读原因

### Requirement: 控制台可访问性与秘密防护
Agent 管理和员工目录 SHALL 提供语义化标签、键盘操作、可见焦点、状态播报和可理解错误。系统指令编辑区 SHALL 提示禁止粘贴凭据，秘密字段不得在本 change 的通用 Agent 配置表单中出现。

#### Scenario: 键盘完成 Agent 选择
- **WHEN** 员工仅使用键盘浏览 Agent 目录
- **THEN** 用户可以聚焦、读取并选择 Agent，选择结果由辅助技术正确播报

#### Scenario: 管理员尝试输入疑似秘密
- **WHEN** 服务端校验将配置字段标记为疑似秘密内容
- **THEN** 页面展示安全警告或错误且不把该内容复制到审计摘要
