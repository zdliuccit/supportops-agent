## ADDED Requirements

### Requirement: Agent 管理权限
平台 SHALL 仅允许可信身份上下文中具备平台管理员能力的主体访问 Agent 管理、版本、激活、停用和授权接口。客户端声明、Prompt 或请求体中的角色 MUST NOT 扩大权限。

#### Scenario: 平台管理员管理 Agent
- **WHEN** 当前租户的有效 Principal 具备平台管理员能力
- **THEN** 系统允许其在当前租户范围内执行授权的 Agent 管理操作

#### Scenario: 普通员工调用管理接口
- **WHEN** 普通员工使用有效令牌调用 Agent 管理接口
- **THEN** 系统返回禁止访问且不执行任何控制面写入

### Requirement: Agent 使用授权默认拒绝
平台 SHALL 使用显式授权决定主体是否可以使用 Agent，并默认拒绝没有匹配授权的主体。首版授权 SHALL 支持当前租户内的单个用户主体和可信角色主体，授权记录 MUST NOT 引用其他租户资源。

#### Scenario: 用户获得显式 Agent 授权
- **WHEN** 管理员向当前租户用户授予某个 active Agent 的使用权
- **THEN** 该用户可以发现并选择该 Agent

#### Scenario: 用户没有匹配授权
- **WHEN** 已认证用户没有 Agent 的用户或角色授权
- **THEN** 目录不返回该 Agent，直接请求其详情或创建会话也返回不可用响应

#### Scenario: 创建跨租户授权
- **WHEN** 管理员尝试把 Agent 授予另一个租户的用户或资源
- **THEN** 系统拒绝授权且不泄露目标主体详情

### Requirement: 员工可用 Agent 目录
平台 SHALL 向员工返回当前租户内同时满足 active、有激活版本且拥有使用授权的 Agent。目录项 SHALL 使用 Agent 当前基础资料，只包含安全展示字段，包括 Agent ID、名称、Logo、描述、欢迎语、建议问题和当前公开版本标识，不得包含 Prompt、模型域名、密钥状态或工具内部配置。

#### Scenario: 查询可用 Agent
- **WHEN** 已认证员工查询 Agent 目录
- **THEN** 系统只返回该员工当前获授权且可启动会话的 Agent，并以稳定顺序分页

#### Scenario: Agent 尚未发布
- **WHEN** 员工拥有某 Agent 授权但该 Agent 没有激活版本
- **THEN** 目录不返回该 Agent且员工不能用它创建会话

### Requirement: Agent 详情与目录一致授权
Agent 详情、会话创建和消息提交 SHALL 使用与目录相同的服务端授权规则，不得通过猜测 ID 绕过目录过滤。

#### Scenario: 直接读取隐藏 Agent
- **WHEN** 员工直接请求一个不在其可用目录中的 Agent ID
- **THEN** 系统返回不可用响应且不暴露 Agent 的名称、状态或版本

#### Scenario: 读取已授权 Agent 详情
- **WHEN** 员工请求其目录中的 Agent
- **THEN** 系统返回安全展示信息，不返回完整系统指令、内部配置或控制面审计内容

### Requirement: 授权撤销即时生效
平台 SHALL 在每次创建会话和提交新消息时重新验证 Agent 使用权。授权撤销后 MUST 阻止新会话和新消息，但 SHALL 允许主体读取其已有历史会话。

#### Scenario: 会话期间撤销授权
- **WHEN** 管理员撤销用户对 Agent 的授权后用户在旧会话发送新消息
- **THEN** 系统拒绝创建消息和 Run，同时保留用户对既有消息和终态 Run 的只读访问

### Requirement: 授权变更可审计
平台 SHALL 为授权创建、修改和撤销保存操作者、授权主体类型、授权目标、Agent、时间和 correlation ID，不保存不必要的身份属性。

#### Scenario: 撤销角色授权
- **WHEN** 管理员撤销某角色对 Agent 的使用权
- **THEN** 系统原子删除或失效该授权并追加不含敏感令牌的审计事件
