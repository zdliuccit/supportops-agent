## ADDED Requirements

### Requirement: 系统 SHALL 记录可关联的 Agent 运行观测步骤

系统 SHALL 为一次 AgentRun 记录可选的 Agent、模型、工具和检索观测步骤，并通过 trace ID、span ID 和父级 ID 关联顺序、耗时、状态、Token、成本和错误信息。

#### Scenario: Agent 包含多次模型调用
- **WHEN** 一次 AgentRun 先后触发两次模型调用
- **THEN** 系统保存两个独立的模型观测步骤，并将它们关联到同一 AgentRun 和 trace ID

#### Scenario: Agent 调用工具
- **WHEN** Agent 执行工具并返回结果
- **THEN** 系统保存工具名称、开始/结束时间、状态、错误信息和父级模型或 Agent 观测 ID

#### Scenario: 观测步骤失败
- **WHEN** 模型或工具步骤失败
- **THEN** 对应观测步骤标记失败并保存结构化错误类型/错误码，且不会覆盖 AgentRun 的最终失败状态

### Requirement: 管理员可以查看单次运行时间线

系统 SHALL 提供按运行 ID查询 Trace 详情的能力，返回从排队、Worker 开始、模型/工具步骤到完成或失败的时间线；普通响应不得默认包含完整 Prompt、回答或原始堆栈。

#### Scenario: 查看成功运行
- **WHEN** 管理员打开一条成功的调用记录详情
- **THEN** 系统展示按开始时间排序的观测步骤、各步骤耗时、Token/成本摘要和最终状态

#### Scenario: 查看失败运行
- **WHEN** 管理员打开一条失败的调用记录详情
- **THEN** 系统展示失败步骤、错误码、correlation ID、已完成步骤及失败前的时间线

#### Scenario: 未授权查看
- **WHEN** 请求者不具备当前租户的运行详情权限
- **THEN** 系统拒绝 Trace 查询，并且不返回观测元数据或敏感内容

### Requirement: 观测数据 SHALL 默认保护敏感内容

系统 SHALL 通过结构化字段白名单限制观测数据内容，不得默认写入完整 Prompt、模型回答、认证凭据或未经脱敏的原始堆栈。

#### Scenario: 记录模型观测
- **WHEN** 系统写入模型观测步骤
- **THEN** 仅保存模型标识、usage、耗时、状态、错误和经过限制的 metadata

#### Scenario: 记录异常
- **WHEN** 运行产生异常堆栈
- **THEN** 系统保存稳定错误类型/错误码和 correlation ID，并对可选堆栈进行脱敏或不写入观测表
