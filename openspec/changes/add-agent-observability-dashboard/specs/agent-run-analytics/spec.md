## ADDED Requirements

### Requirement: 系统 SHALL 保存 AgentRun 运行摘要

每次 AgentRun SHALL 在结束时保存状态、输入/输出/缓存/推理 Token、成本及来源、模型调用数、工具调用数、重试次数、排队/执行/端到端耗时和可用的 finish reason；失败或取消运行 SHALL 至少保存终态、错误码和已采集的部分指标。

#### Scenario: AgentRun 成功完成
- **WHEN** AgentRun 产生最终回答并完成
- **THEN** 系统持久化运行状态、Token、成本、调用次数、重试次数和各阶段耗时

#### Scenario: AgentRun 失败
- **WHEN** AgentRun 在模型、工具或运行流程中失败
- **THEN** 系统持久化失败状态、稳定错误码、correlation ID、已采集指标和失败阶段耗时

#### Scenario: 成本无法由供应商提供
- **WHEN** provider 没有返回成本但模型版本存在价格快照
- **THEN** 系统根据该价格快照计算成本并标记成本来源为 calculated

### Requirement: 成本和 Token 统计 SHALL 避免重复计算

系统 SHALL 将输入、输出、缓存和推理 Token 作为互不混淆的明细，并明确总 Token 的计算口径；历史成本 SHALL 使用运行时保存的价格或 provider 成本，不随当前模型价格变化。

#### Scenario: 展示总 Token
- **WHEN** 管理员查看总览或调用记录
- **THEN** 总 Token 按输入 Token 加输出 Token 展示，并可展开查看缓存和推理 Token 明细

#### Scenario: 缺少 Token 数据
- **WHEN** provider 未返回可用 usage
- **THEN** 相关指标显示缺失或“未知”，不得把缺失值当作 0 参与成本统计

### Requirement: 管理员可以分页查询调用记录和错误

系统 SHALL 提供按租户、时间、Agent、版本、模型、用户/部门、状态和错误码筛选的分页调用记录与错误聚合查询，并返回总量或下一页游标。

#### Scenario: 查询调用记录
- **WHEN** 管理员提交筛选条件和分页参数
- **THEN** 系统返回匹配的 AgentRun 摘要、稳定排序字段和分页信息

#### Scenario: 查询错误聚合
- **WHEN** 管理员打开错误分析
- **THEN** 系统按错误码或错误类型返回发生次数、影响用户数、涉及 Agent 和最后发生时间

#### Scenario: 跨租户访问
- **WHEN** 管理员请求不属于当前租户的 AgentRun 或错误数据
- **THEN** 系统不返回该数据，并按现有管理员 API 约定返回未授权结果
