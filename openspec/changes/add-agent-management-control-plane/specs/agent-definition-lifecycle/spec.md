## ADDED Requirements

### Requirement: 租户内 Agent 稳定身份
平台 SHALL 允许有管理权限的主体在当前租户内创建 Agent，并为其分配稳定 ID、租户内唯一 slug、生命周期状态、基础资料和创建审计信息。基础资料 SHALL 包含名称、Logo、描述和欢迎语，并与版本化运行配置分离。Agent 不得跨租户读取、修改或引用。

#### Scenario: 创建 Agent 草稿
- **WHEN** 当前租户的平台管理员提交有效且未占用的 Agent slug
- **THEN** 系统创建未激活 Agent 及 revision 为 1 的初始草稿，并记录创建主体和关联 ID

#### Scenario: 跨租户读取 Agent
- **WHEN** 管理员请求另一个租户的 Agent ID
- **THEN** 系统返回资源未找到且不泄露该 Agent 是否存在

#### Scenario: 修改 Agent 基础资料
- **WHEN** 管理员修改 Agent 名称、Logo、描述或欢迎语
- **THEN** 系统立即更新目录和聊天展示资料、记录审计事件，但不创建或改变 AgentVersion

#### Scenario: 基础资料更新影响历史会话展示
- **WHEN** 员工打开该 Agent 的既有会话
- **THEN** 页面显示 Agent 当前基础资料，同时继续执行会话固定的历史运行版本

### Requirement: Agent 草稿乐观并发更新
平台 SHALL 允许管理员编辑未发布草稿，并 MUST 使用单调递增 revision 防止并发修改被静默覆盖。

#### Scenario: 使用当前 revision 保存草稿
- **WHEN** 管理员提交的期望 revision 与服务端当前 revision 一致且配置有效
- **THEN** 系统保存草稿、递增 revision 并追加草稿修改审计事件

#### Scenario: 使用过期 revision 保存草稿
- **WHEN** 管理员提交的期望 revision 已落后于服务端版本
- **THEN** 系统拒绝更新并返回稳定冲突错误码和当前 revision，不覆盖任一方内容

### Requirement: 发布前配置校验
平台 SHALL 按明确的配置 Schema 校验草稿中的 Prompt、模型绑定、工具绑定、结构化输出、运行限制和禁止内容，并以字段级问题返回全部可识别错误。未知或不受支持的配置 Schema MUST NOT 发布。

#### Scenario: 校验有效 Agent 运行配置
- **WHEN** 草稿包含有效系统 Prompt、已验证模型端点、已注册工具、兼容的输出策略和平台允许范围内的运行限制
- **THEN** 系统返回校验成功、解析将要固定的模型/工具版本且不创建发布版本

#### Scenario: 发布包含不受支持配置的草稿
- **WHEN** 管理员尝试发布未知 Schema、不可用模型、能力不兼容、跨租户工具或超出上限的运行参数
- **THEN** 系统拒绝发布、返回字段级校验问题且不改变当前激活版本

### Requirement: 不可变 Agent 发布版本
平台 SHALL 从通过校验的草稿创建租户及 Agent 范围内单调递增的不可变版本，并保存规范化运行配置、固定的 ModelEndpointVersion、固定的工具定义版本、配置 Schema、内容摘要、版本说明、发布时间和发布主体。已发布版本 MUST NOT 被原地修改或删除。

#### Scenario: 发布新版本
- **WHEN** 管理员发布当前有效草稿
- **THEN** 系统原子创建下一个版本号及内容摘要，草稿后续修改不改变该版本快照

#### Scenario: 尝试修改历史版本
- **WHEN** 客户端请求更新一个已发布版本的配置内容
- **THEN** 系统拒绝请求并保留原版本和审计证据

### Requirement: 发布版本激活与回滚
平台 SHALL 将发布和激活建模为独立操作。只有当前 Agent 的有效发布版本才能被激活；重新激活历史版本 SHALL 构成可审计回滚，不得复制或改写历史版本。

#### Scenario: 激活新发布版本
- **WHEN** 管理员激活当前 Agent 的有效发布版本
- **THEN** 系统原子更新 Agent 的 active version、将 Agent 标记为 active 并记录前后版本 ID

#### Scenario: 回滚到历史版本
- **WHEN** 管理员选择当前 Agent 的一个历史发布版本进行激活
- **THEN** 系统将 active version 指回该历史版本并追加回滚审计事件

#### Scenario: 激活其他 Agent 的版本
- **WHEN** 管理员尝试把另一个 Agent 或租户的版本设为 active version
- **THEN** 系统拒绝操作且不改变任一 Agent 状态

### Requirement: Agent 停用
平台 SHALL 允许管理员停用 Agent。停用 MUST 阻断新的目录发现、会话创建和消息提交，但 SHALL 保留历史版本、会话、Run 和审计记录。

#### Scenario: 停用活动 Agent
- **WHEN** 管理员停用一个 active Agent
- **THEN** 系统将其标记为 disabled、记录审计事件且不删除任何历史记录

#### Scenario: 查看停用 Agent 历史
- **WHEN** 有权管理员查看已停用 Agent
- **THEN** 系统返回其版本和审计历史，但不允许员工继续提交新工作

### Requirement: Agent 控制面审计
平台 MUST 为创建、草稿修改、校验失败、发布、激活、回滚、停用和授权变更追加不可变审计事件。事件 SHALL 包含租户、主体、Agent、动作、相关版本、时间和 correlation ID，且 MUST NOT 包含访问令牌、数据库连接串、完整系统指令或秘密值。

#### Scenario: 发布操作产生审计事件
- **WHEN** 管理员成功发布 Agent 版本
- **THEN** 系统追加包含发布主体、Agent ID、新版本 ID、配置摘要和 correlation ID 的审计事件

#### Scenario: 审计负载包含敏感配置
- **WHEN** Agent 草稿包含系统指令或疑似秘密文本
- **THEN** 审计事件只保存必要标识和摘要，不复制完整敏感内容
