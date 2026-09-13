## ADDED Requirements

### Requirement: 系统 Dashboard SHALL 默认聚合当前租户全部 Agent

系统 SHALL 为当前租户的平台管理员提供系统级 Agent Dashboard，默认覆盖该租户的全部 Agent，并通过统一筛选支持时间、Agent、生命周期、健康状态、版本、模型、用户/部门和运行状态范围。

#### Scenario: 查看系统总览
- **WHEN** 管理员打开系统 Dashboard 且没有指定 Agent
- **THEN** 系统展示当前租户全部 Agent 的库存、执行服务、用户、会话、调用、成功率、错误、Token、成本和延迟指标

#### Scenario: 按 Agent 缩小系统范围
- **WHEN** 管理员在系统 Dashboard 选择一个或多个 Agent
- **THEN** 库存以外的运行指标、趋势、排行、调用记录和错误列表 SHALL 使用所选 Agent 范围重新查询

#### Scenario: 跨租户访问
- **WHEN** 管理员尝试通过参数查询其他租户的 Agent 或运行数据
- **THEN** 系统拒绝请求，且响应不得泄露其他租户的数量或状态

### Requirement: 系统 Dashboard SHALL 分组展示 Agent 库存、执行态和健康态

系统 SHALL 分别展示生命周期库存（全部、已启用、草稿、已停用）、执行态（运行中 Agent、排队中 Agent、当前队列积压）和健康态（健康、异常、离线、无近期活动、待发布、未知），不得使用单个 Agent 生命周期字段替代三组状态。

#### Scenario: 统计运行中 Agent
- **WHEN** 一个 Agent 至少存在一个 queued 或 running 的 AgentRun
- **THEN** 该 Agent 计入“运行中 Agent”一次，不因多个并发 Run 重复计数

#### Scenario: 统计离线 Agent
- **WHEN** Agent 生命周期为 active、存在明确的 per-Agent 运行租约且该租约已过期
- **THEN** 该 Agent 计入“离线 Agent”，并展示租约过期时间和健康原因

#### Scenario: active Agent 无近期调用
- **WHEN** Agent 生命周期为 active、没有 queued/running Run 且没有有效运行租约
- **THEN** 系统将其计入“无近期活动”或“未知”，不得直接计入“离线 Agent”

#### Scenario: 执行服务不可用
- **WHEN** 共享 Worker 执行服务租约过期
- **THEN** 系统展示“执行服务不可用”和队列积压，并允许 Agent 健康态为 unknown，不得批量把所有 active Agent 标记为离线

### Requirement: 系统 Dashboard SHALL 展示完整的跨 Agent 使用分析

系统 SHALL 展示总用户、活跃用户、近5分钟活跃用户、新增/累计会话、Agent 调用、成功/失败/取消、错误数、输入/输出/缓存/推理 Token、成本、P50/P95/P99 延迟、排队耗时、工具调用和当前运行任务等综合指标。

#### Scenario: 查看综合趋势
- **WHEN** 管理员选择时间范围和时间粒度
- **THEN** 系统展示调用、用户、会话、成功/失败、错误、Token、成本和延迟趋势，并允许点击时间桶下钻到调用记录

#### Scenario: 查看 Agent 排行
- **WHEN** 管理员打开排行区域
- **THEN** 系统至少提供调用量、活跃用户、失败率、Token、成本、P95 延迟和工具调用排行，并显示 Agent 名称及统计窗口

#### Scenario: 缺失 usage
- **WHEN** 某些运行没有 provider usage
- **THEN** 系统将对应 Token 和成本标记为未知或缺失，不得把缺失值当作 0 累加到系统总量

### Requirement: 系统 Dashboard SHALL 提供 Agent 状态明细和统一下钻

系统 SHALL 提供分页 Agent 状态表，至少包含 Agent、生命周期、健康态、健康原因、最近运行、最近成功、最近失败、当前运行数、错误率、P95 延迟和待发布标识；点击 Agent 后 SHALL 能带 Agent 范围进入共享调用记录、错误列表和 Trace 详情。

#### Scenario: 查看 Agent 状态表
- **WHEN** 管理员打开 Agent 状态列表并翻页
- **THEN** 系统返回稳定排序的分页结果及下一页信息，且每行状态使用同一套派生口径

#### Scenario: 下钻 Agent 运行记录
- **WHEN** 管理员点击状态表中的 Agent
- **THEN** 系统打开共享运行分析页面并自动应用该 Agent、时间和状态筛选

#### Scenario: 待发布 Agent
- **WHEN** Agent 草稿配置或固定的模型/工具版本与当前活动运行版本不一致
- **THEN** 状态表显示待发布标识和“运行版本未更新”原因，不得把该状态当作离线

### Requirement: 系统查询 SHALL 保持指标范围一致

系统 Dashboard 的 summary、timeseries、agents/status、rankings、runs、errors 和 trace 查询 SHALL 使用相同的 tenant、时间、Agent、版本、模型、用户/部门和状态筛选规则；系统默认 Agent 范围为当前租户全部 Agent，Agent 级页面必须固定 Agent 范围。

#### Scenario: 同一筛选驱动多个区域
- **WHEN** 管理员改变时间或状态筛选
- **THEN** 指标卡、趋势、排行、状态表、调用记录和错误列表 SHALL 使用相同筛选条件更新

#### Scenario: 空租户
- **WHEN** 当前租户没有任何 Agent
- **THEN** 系统显示 Agent 总数为 0、运行中为 0、离线为 0，并展示可识别的空状态，不返回错误

### Requirement: 系统 Dashboard UI SHALL 提供清晰的分层和状态反馈

系统 Dashboard SHALL 按“标题与刷新信息、全局筛选、Agent 状态总览、综合使用量、趋势与排行、Agent 状态表、调用/错误下钻”的层级展示内容，并复用现有绿色主题、卡片、按钮、表格和圆角边框组件。状态必须同时使用文字、数量、更新时间和 tooltip 口径表达，不能只依赖颜色。

#### Scenario: 查看系统页面首屏
- **WHEN** 管理员首次打开系统 Dashboard
- **THEN** 首屏可见页面标题、数据更新时间、刷新入口、全局筛选以及生命周期、执行态和健康态三组状态卡

#### Scenario: 区分不同状态层
- **WHEN** 页面同时存在已启用 Agent、运行中 Agent、异常 Agent 和执行服务不可用
- **THEN** 系统将它们分布在对应状态分组中，并使用文字和原因区分，不把执行服务不可用批量显示成每个 Agent 离线

#### Scenario: 查看趋势和排行
- **WHEN** 管理员查看趋势或排行区域
- **THEN** 趋势图、排行表和统计窗口具有一致的筛选范围；图表数据点和排行行支持下钻到对应调用记录

#### Scenario: 查看 Agent 状态表
- **WHEN** 管理员查看 Agent 状态表
- **THEN** 表格显示稳定的状态、最近活动、错误率、P95、待发布和操作列，表头固定，点击行打开右侧详情抽屉

#### Scenario: 加载和无数据
- **WHEN** Dashboard 正在加载、当前租户没有数据或执行服务不可用
- **THEN** 页面分别展示骨架屏、可识别的空状态或页面级服务异常提示，不用空白页面或无解释的 0 替代

#### Scenario: 窄屏访问
- **WHEN** 管理员在窄屏设备访问系统 Dashboard
- **THEN** 卡片和图表改为单列，筛选器可折叠，状态表允许横向滚动且首列和状态列保持可见
