## ADDED Requirements

### Requirement: 独立分析页面
系统 SHALL 将系统 KPI/趋势、Agent 调用排行、Agent 状态和 Agent 运行分析分别渲染为可独立访问的页面；每个页面 SHALL 使用白色主画布，并提供加载、空数据和错误状态。

#### Scenario: Dashboard 页面
- **WHEN** 管理员打开 `/analytics/dashboard`
- **THEN** 页面展示总 Agent/运行次数/Token/错误等 KPI、趋势图和最近运行摘要，不重复渲染排行与状态完整列表

#### Scenario: 排行页面
- **WHEN** 管理员打开 `/analytics/rankings`
- **THEN** 页面展示可按时间范围筛选的 Agent 调用量、成功率、Token 和延迟排行

#### Scenario: 状态页面
- **WHEN** 管理员打开 `/analytics/agents`
- **THEN** 页面展示每个 Agent 的运行状态、版本、最近运行时间、成功率和健康标识，并支持进入 Agent 运行分析

#### Scenario: 页面加载异常
- **WHEN** 分析接口失败或返回空集合
- **THEN** 页面显示可理解的错误/空状态和重试入口，且保持白色页面背景

### Requirement: 共享分析筛选
分析页面 SHALL 支持统一的时间范围筛选和刷新动作；Agent 运行分析 SHALL 额外支持指定 Agent 与运行维度。

#### Scenario: 修改时间范围
- **WHEN** 管理员选择新的时间范围
- **THEN** 当前页面重新请求对应范围数据，并保持筛选控件与结果一致
