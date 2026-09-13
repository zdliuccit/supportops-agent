## ADDED Requirements

### Requirement: 执行服务 SHALL 维护可过期的心跳租约

共享 Worker 或其他 Agent 执行服务 SHALL 为每个共享实例维护 instance ID、心跳时间、过期时间、队列积压、当前运行数和服务状态；该租约表是全局实例事实，不按租户复制；租约过期后系统 SHALL 将执行服务标记为不可用或未知。

#### Scenario: Worker 更新租约
- **WHEN** Worker 启动并持续轮询队列
- **THEN** 系统写入或更新该实例的租约、心跳、队列深度和当前运行数

#### Scenario: Worker 租约过期
- **WHEN** 当前时间超过实例 expires_at 且超过允许的时钟容差
- **THEN** 系统将该执行服务标记为 unavailable，并记录最后心跳和过期原因

#### Scenario: 多个 Worker
- **WHEN** 当前租户或执行集群存在多个 Worker 实例
- **THEN** 系统按 instance ID 分别记录租约，并在系统 Dashboard 汇总可用实例数、队列积压和活动运行数

### Requirement: Agent 健康状态 SHALL 由运行事实派生

系统 SHALL 维护 Agent 健康快照，至少保存健康状态、原因、最近运行/成功/失败时间、当前运行数、错误率、P95 延迟、待发布标识、统计窗口和观察时间；健康状态 SHALL 结合 AgentRun、明确注册的 per-Agent 运行租约、版本一致性和执行服务状态计算。

#### Scenario: Agent 有成功运行
- **WHEN** Agent 在健康窗口内有成功运行、错误率未超阈值且执行服务可用
- **THEN** 系统将其健康状态标记为 healthy，并更新 last_success_at 和 observed_at

#### Scenario: Agent 连续失败
- **WHEN** Agent 在健康窗口内连续失败或错误率超过配置阈值
- **THEN** 系统将其标记为 degraded 或 unhealthy，并记录错误原因和 last_failure_at

#### Scenario: Agent 无近期活动
- **WHEN** Agent 为 active 且健康窗口内没有运行事件，同时没有明确注册的 per-Agent 运行租约
- **THEN** 系统将其标记为 no_recent_activity 或 unknown，不得伪造为 healthy 或 offline

#### Scenario: Agent 版本待发布
- **WHEN** Agent 草稿或模型/工具固定版本与活动运行版本不一致
- **THEN** 系统设置 pending_publish，并在 health_reason 中说明需要发布更新运行版本

### Requirement: 健康快照更新 SHALL 可重复且不改变运行事实

健康协调任务 SHALL 能够根据 AgentRun 和执行服务租约重复计算快照；重复执行不得创建重复事实或修改 AgentRun 的原始状态、时间和错误。

#### Scenario: 重复计算健康状态
- **WHEN** 健康协调任务对同一 Agent 重复运行
- **THEN** 系统更新同一健康快照或带版本的读模型，不重复插入相同 AgentRun 统计事实

#### Scenario: 查询快照过期
- **WHEN** health snapshot 的 observed_at 超过允许的新鲜度窗口
- **THEN** 系统在 Dashboard 标记指标为 stale/unknown，并显示最近观察时间

### Requirement: 运行健康接口 SHALL 遵守租户和管理员权限

系统 SHALL 仅允许当前租户的管理员读取或更新其执行服务租约和 Agent 健康快照，普通用户不得通过接口枚举 Agent 健康、队列深度或错误原因。

#### Scenario: 管理员读取本租户状态
- **WHEN** 当前租户管理员请求 Agent 健康或执行服务状态
- **THEN** 系统返回当前租户范围内经过脱敏的状态数据

#### Scenario: 普通用户读取健康状态
- **WHEN** 非管理员请求系统健康详情
- **THEN** 系统拒绝请求且不返回队列、实例或错误元数据
