## ADDED Requirements

### Requirement: 租户模型端点独立管理
平台 SHALL 将模型连接作为独立于 Agent 的租户资源管理。模型端点 SHALL 包含稳定 ID、名称、Logo、状态和当前有效版本；其不可变版本 SHALL 包含供应商类型、API 协议、Base URL、远端模型名称、组织/项目标识、受控 Header 引用、能力、上下文窗口、默认参数、允许覆盖范围、限流/并发、可选价格元数据和秘密引用。

#### Scenario: 创建 OpenAI 官方模型端点
- **WHEN** 平台管理员选择 `openai_official`、填写展示资料、远端模型名称和有效密钥
- **THEN** 系统使用平台预设官方 Base URL 创建模型端点及首个配置版本，并且 Agent 尚不能在连接验证前发布该绑定

#### Scenario: 创建 OpenAI-compatible 中转站端点
- **WHEN** 平台管理员选择 `openai_compatible` 并提交允许的 HTTPS Base URL、API 协议、远端模型名称和有效密钥
- **THEN** 系统规范化 URL、保存不可变配置版本并将密钥写入 Secret Provider

#### Scenario: 跨租户读取模型端点
- **WHEN** 管理员请求另一个租户的模型端点 ID
- **THEN** 系统返回资源未找到且不泄露端点名称、域名、模型或密钥状态

### Requirement: 模型密钥写后不可读
平台 MUST 将 API Key 与普通模型和 Agent 配置分离。密钥 SHALL 写入受控 Secret Provider，普通数据库记录和版本快照只保存 Secret Reference、脱敏提示和凭据 revision。任何读取、列表、审计或错误响应 MUST NOT 返回明文密钥或 Authorization Header。

#### Scenario: 管理员保存模型密钥
- **WHEN** 管理员为模型端点提交 API Key
- **THEN** 系统通过只写接口保存秘密，并仅返回 `configured=true`、masked hint、credential revision 和轮换时间

#### Scenario: 管理员重新打开编辑页
- **WHEN** 已配置密钥的模型端点被读取
- **THEN** 页面得到空密钥输入框和脱敏状态，不得从服务端取回原密钥

#### Scenario: 密钥进入日志或审计负载
- **WHEN** 密钥写入、连接测试或模型调用失败
- **THEN** 日志和审计只记录秘密引用、错误分类和 correlation ID，不记录请求 Header 或密钥正文

### Requirement: 模型端点版本与凭据轮换语义
Base URL、API 协议、远端模型名称、能力或参数范围的修改 SHALL 创建新的不可变 ModelEndpointVersion。Agent 发布版本 MUST 固定所选端点的具体版本。API Key 轮换 SHALL 更新稳定 Secret Reference 的凭据 revision，而不要求重新发布所有 Agent。

#### Scenario: 修改中转站域名
- **WHEN** 管理员将模型端点 Base URL 从一个域名改为另一个域名
- **THEN** 系统创建新的端点版本，既有 AgentVersion 继续固定旧版本，直到管理员发布新的 AgentVersion

#### Scenario: 轮换 API Key
- **WHEN** 管理员为同一端点写入新密钥并验证成功
- **THEN** 系统增加 credential revision，后续 Run 使用新 revision，历史 Run 仍保留其使用过的 revision 标识

### Requirement: 官方与中转站协议适配
平台 SHALL 首版支持 `openai_official` 和 `openai_compatible`。官方端点 SHALL 使用平台预设域名并允许明确选择 Responses 或 Chat Completions；兼容端点 SHALL 默认使用 Chat Completions，并仅在能力验证通过后启用供应商原生结构化输出等增强能力。

#### Scenario: 中转站只支持 Chat Completions
- **WHEN** 连接测试确认端点可聊天和调用工具但不支持 Provider-native structured output
- **THEN** 系统将端点标记为适合 `ToolStrategy`，不得让 Agent 发布为 `ProviderStrategy`

#### Scenario: 非标准供应商扩展字段
- **WHEN** 中转站响应包含 OpenAI 规范以外的字段
- **THEN** 通用适配层可以忽略这些字段，但不得把未建模字段自动注入 Agent 状态或审计数据

### Requirement: 模型连接和能力测试
平台 SHALL 提供不含业务数据的连接测试，分别验证 TLS/DNS、认证、指定模型可用性、streaming、tool calling 和 structured output。测试结果 SHALL 保存时间、端点版本、结果分类、延迟摘要和 correlation ID，并且 Agent 发布只允许绑定满足其配置能力要求的最近已验证端点版本。

#### Scenario: 端点认证失败
- **WHEN** 连接测试收到认证失败响应
- **THEN** 系统返回脱敏错误分类、将端点标记为不可用且不暴露供应商响应中的秘密信息

#### Scenario: `/models` 不可用但最小调用成功
- **WHEN** 中转站不支持模型列表接口，但指定模型的最小聊天请求成功
- **THEN** 系统可以将基础连接标记为通过，并单独记录未验证的增强能力

### Requirement: 中转站网络安全
平台 MUST 对中转站 Base URL 实施服务端出站安全控制，包括 HTTPS、URL 规范化、DNS 与解析后 IP 校验、私网及保留地址拒绝、重定向限制、超时、响应大小限制和生产出口 allowlist。仅显式开发模式可以允许 localhost，且该设置不得由请求参数覆盖。

#### Scenario: 中转站域名解析到私网地址
- **WHEN** 管理员提交的公开域名在连接时解析到 loopback、link-local、私网或保留地址
- **THEN** 系统拒绝连接测试和启用该版本，并记录不含敏感网络细节的审计事件

#### Scenario: 中转站重定向到禁止地址
- **WHEN** 允许域名返回到私网或未允许域名的重定向
- **THEN** 客户端不跟随该重定向并将测试标记为安全策略失败

### Requirement: 模型端点停用与引用保护
管理员 SHALL 能停用模型端点。停用 MUST 阻止新的 AgentVersion 绑定和新会话使用该端点，但 SHALL 保留版本、秘密引用、AgentVersion 和历史 Run 审计。已经运行中的 Run 按固定版本继续，除非显式取消。

#### Scenario: 发布 Agent 绑定停用端点
- **WHEN** 管理员尝试发布绑定 disabled 模型端点的 Agent 草稿
- **THEN** 系统拒绝发布并返回模型绑定字段级错误

#### Scenario: 查看历史模型使用
- **WHEN** 审计员查询曾使用已停用模型端点的历史 Run
- **THEN** 系统返回端点和凭据 revision 标识，但不返回密钥或完整供应商请求
