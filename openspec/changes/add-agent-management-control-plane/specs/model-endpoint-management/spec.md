## ADDED Requirements

### Requirement: 租户模型端点独立管理
平台 SHALL 将模型连接作为独立于 Agent 的租户资源管理。模型端点 SHALL 包含稳定 ID、名称、Logo、状态和当前有效版本；其不可变版本 SHALL 包含供应商类型、平台推导的 API 协议、Base URL、远端模型名称、JSON 扩展对象、验证能力和秘密引用。逐模型管理输入 SHALL 只要求模型名称和扩展对象，扩展对象默认 `{}` 且顶层必须为 JSON 对象。

#### Scenario: 创建 OpenAI 官方模型端点
- **WHEN** 平台管理员选择 `openai_official`、填写展示资料、远端模型名称和有效密钥
- **THEN** 系统使用平台预设官方 Base URL 创建模型端点及首个配置版本，并且 Agent 尚不能在连接验证前发布该绑定

#### Scenario: 创建 OpenAI-compatible 中转站端点
- **WHEN** 平台管理员选择 `openai_compatible` 并提交允许的 HTTPS Base URL、远端模型名称、可选 JSON 扩展对象和有效密钥
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
Base URL、远端模型名称或 JSON 扩展对象的修改 SHALL 创建新的不可变 ModelEndpointVersion。Agent 发布版本 MUST 固定所选端点的具体版本。API Key 轮换 SHALL 更新稳定 Secret Reference 的凭据 revision，而不要求重新发布所有 Agent。

#### Scenario: 修改中转站域名
- **WHEN** 管理员将模型端点 Base URL 从一个域名改为另一个域名
- **THEN** 系统创建新的端点版本，既有 AgentVersion 继续固定旧版本，直到管理员发布新的 AgentVersion

#### Scenario: 轮换 API Key
- **WHEN** 管理员为同一端点写入新密钥并验证成功
- **THEN** 系统增加 credential revision，后续 Run 使用新 revision，历史 Run 仍保留其使用过的 revision 标识

### Requirement: 官方与中转站协议适配
平台 SHALL 首版支持 `openai_official` 和 `openai_compatible`。官方端点 SHALL 使用平台预设域名与 Responses；兼容端点 SHALL 默认使用 Chat Completions，并仅在能力验证通过后启用供应商原生结构化输出等增强能力。

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

### Requirement: 单个连接管理多个模型
平台 SHALL 将模型端点建模为一套可复用供应商连接，并允许其包含一个或多个稳定模型。每个模型 SHALL 保存上游模型名称、默认 `{}` 的 JSON 扩展对象、当前不可变调用版本和独立测试历史。Agent 绑定 MUST 固定到端点下的具体模型版本，而不是隐式选择端点中的第一个模型。

#### Scenario: 保存模型扩展对象
- **WHEN** 管理员填写合法的 JSON 对象并保存模型
- **THEN** API 将对象保存到模型不可变版本并在读取时原样返回；数组、标量或非法 JSON 不得进入保存请求

#### Scenario: 手动添加多个模型
- **WHEN** 管理员在模型输入框分别输入多个模型 ID 并按回车
- **THEN** 页面只在本地模型集合中增加这些模型，不请求供应商模型列表，也不自动开始连通性测试

#### Scenario: 从供应商列表选择模型
- **WHEN** 管理员点击“获取模型列表”且连接信息有效
- **THEN** 服务端使用只写凭据和安全网络策略请求供应商模型目录，页面展示可多选列表，选中结果合并到本地模型集合

### Requirement: 模型端点仅区分是否启用
模型端点 MUST NOT 存在草稿或发布业务状态。系统 SHALL 只保存 `is_enabled`：普通“保存”写入当前配置并设置为未启用；“保存并使用”只有在所有当前模型的有效测试通过后才设置为启用。不可变配置版本只用于运行固定和审计，不构成用户可见状态。

#### Scenario: 普通保存模型配置
- **WHEN** 管理员点击“保存”
- **THEN** 系统持久化连接、凭据引用和模型集合，生成必要的不可变版本，并将端点设为未启用

#### Scenario: 保存并使用未测试配置
- **WHEN** 管理员点击“保存并使用”但至少一个当前模型未测试、失败或测试已过期
- **THEN** 系统保存配置但保持未启用，并要求完成这些模型的连通性测试

### Requirement: 逐模型持久化连通性测试
平台 SHALL 为端点中的每个模型持久化独立测试记录。测试 SHALL 使用固定的简单问题发起无业务数据的真实流式推理请求，只有解析到模型回答文本时才视为连通，并记录截断后的回答文本、请求发出、收到响应头、收到首包内容和测试完成阶段，以及 HTTP 状态、响应头耗时、首包耗时、总耗时、凭据 revision、配置摘要、稳定错误码和 correlation ID。

#### Scenario: 模型测试通过
- **WHEN** 指定模型在当前连接配置和凭据 revision 下完成真实流式推理
- **THEN** 系统记录通过状态、模型回答文本和各阶段耗时，并允许页面展示该模型的最新测试摘要与历史记录

#### Scenario: 连接配置发生变化
- **WHEN** Base URL、API Key、上游模型 ID、API 协议或影响请求的配置发生变化
- **THEN** 旧测试结果保留用于审计，但当前模型状态显示为已过期且不能用于启用端点

#### Scenario: 展示测试诊断信息
- **WHEN** 管理员查看连通性测试弹窗
- **THEN** 页面可以展示主机、请求路径、HTTP 状态、阶段耗时和截断后的模型回答文本，但不得展示 Authorization、API Key、原始请求体、原始响应事件或供应商原始错误体

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
