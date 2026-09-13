## ADDED Requirements

### Requirement: 结构化错误事件采集
运行失败时系统 SHALL 持久化结构化错误事件，至少包含 tenant、发生时间、severity、error_code、reason、stage、run、conversation、agent、agent version、model version、user、retry_count、latency_ms 和脱敏 metadata；事件写入失败不得覆盖原始失败结果。

#### Scenario: 运行失败产生事件
- **WHEN** Agent run 进入失败状态
- **THEN** 系统新增一条可查询的错误事件并关联已知运行上下文

#### Scenario: 事件写入异常
- **WHEN** 错误事件写入数据库失败
- **THEN** run 仍保持失败状态，服务记录结构化日志且请求不返回数据库堆栈

### Requirement: 错误事件分页查询
管理员 API SHALL 提供带租户隔离的错误事件分页查询，支持时间范围、severity、error_code、agent、user 和 resolution/status 筛选，结果按发生时间倒序稳定分页。

#### Scenario: 查询错误列表
- **WHEN** 管理员请求错误分析列表
- **THEN** 响应包含 page、page_size、total 和 items；每项至少返回发生时间、严重级别、错误原因、Agent、用户、会话/运行、阶段和恢复状态

#### Scenario: 筛选与越权
- **WHEN** 管理员带筛选参数查询或尝试传入其他租户 id
- **THEN** 结果仅包含当前租户匹配事件，非法租户过滤不会扩大可见范围

### Requirement: 错误详情追踪
管理员 API SHALL 提供单条错误事件详情，包含列表字段、模型与版本、重试/延迟、关联运行摘要及可用 trace 时间线；前端 SHALL 以列表加详情抽屉呈现。

#### Scenario: 打开错误详情
- **WHEN** 管理员点击错误列表行
- **THEN** 右侧抽屉展示错误原因、用户、Agent、模型版本、发生时间、阶段、运行标识和 trace/恢复信息

#### Scenario: 事件不存在
- **WHEN** 请求不存在或不属于当前租户的事件详情
- **THEN** API 返回 404，前端保留列表并显示“记录不存在或已移除”的提示
