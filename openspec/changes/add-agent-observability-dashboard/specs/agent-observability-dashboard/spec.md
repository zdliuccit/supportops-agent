## ADDED Requirements

### Requirement: 管理员可以查看 Agent 级运行分析总览

系统 SHALL 在 Agent 管理区域提供以单个 Agent 为范围的运行分析页面，并允许管理员按时间、Agent 版本、模型、用户/部门和运行状态筛选指标。共享 summary 契约 SHALL 同时提供租户总用户数、时间范围内活跃用户数和近5分钟活跃用户数；系统级跨 Agent 总览 SHALL 使用同一套指标和查询契约，但由独立的系统 Dashboard 提供。

#### Scenario: 查看 Agent 总览
- **WHEN** 管理员从某个 Agent 的列表或配置页打开运行分析
- **THEN** 页面固定该 Agent 范围，并展示活跃用户、近5分钟活跃用户、新增会话、Agent 调用、成功率、总 Token、成本和延迟指标

#### Scenario: 使用 Agent 级筛选条件
- **WHEN** 管理员改变时间、版本、模型、用户/部门或状态筛选
- **THEN** 所有指标卡、趋势图、调用记录和错误列表 SHALL 使用同一组筛选条件重新查询

#### Scenario: 防止 Agent 级查询扩大范围
- **WHEN** Agent 级页面请求汇总、趋势或调用记录
- **THEN** 请求 SHALL 携带 Agent 范围，系统不得将该页面无意扩大为当前租户全部 Agent 的数据

### Requirement: 总览展示可解释的趋势和对比

系统 SHALL 展示调用量、会话量、用户量、成功/失败/取消、错误数/错误率、Token、成本和延迟的时间序列，并在指标卡中展示与上一等长周期的变化值。

#### Scenario: 查看时间趋势
- **WHEN** 管理员选择时间范围和时间粒度
- **THEN** 页面展示该范围内按粒度分桶的数据，并保持空桶为零或明确的无数据状态

#### Scenario: 点击图表下钻
- **WHEN** 管理员点击趋势图中的时间桶或错误数据点
- **THEN** 系统打开调用记录或错误列表，并自动带上对应时间和状态筛选

### Requirement: Dashboard 明确区分活跃用户和在线人数

系统 SHALL 将第一阶段的在线相关指标命名为“近5分钟活跃用户”，该指标按窗口内产生消息或 AgentRun 的去重用户计算，不得使用最后登录时间冒充实时在线状态。

#### Scenario: 计算近5分钟活跃用户
- **WHEN** 指标查询结束时间为当前时间
- **THEN** 系统返回最近5分钟内产生有效消息或 AgentRun 的去重用户数

#### Scenario: 没有活动数据
- **WHEN** 最近5分钟没有用户消息或 AgentRun
- **THEN** 系统返回 0，并在指标说明中保留“近5分钟活跃用户”的口径

### Requirement: Agent 级 Dashboard UI SHALL 突出当前 Agent 上下文

Agent 级 Dashboard SHALL 在顶部显示 Agent 名称、生命周期状态、当前活动版本、模型/工具摘要、最近更新时间和返回系统 Dashboard 的入口，并将页面内所有统计默认锁定到该 Agent。页面 SHALL 复用系统 Dashboard 的绿色主题、状态颜色、卡片、图表、表格和详情抽屉组件。

#### Scenario: 查看 Agent 页面首屏
- **WHEN** 管理员从 Agent 列表或配置页进入运行分析
- **THEN** 页面首屏显示当前 Agent 上下文、筛选条件、运行健康、调用概览和主要 Token/成本/延迟指标

#### Scenario: 查看调用与 Trace
- **WHEN** 管理员点击趋势数据点、错误记录或调用记录行
- **THEN** 页面打开带原筛选条件的调用列表或右侧 Trace 详情抽屉，展示排队、Worker、模型、工具和完成/失败时间线

#### Scenario: 版本未更新提示
- **WHEN** 当前 Agent 草稿、模型或工具版本与运行版本不一致
- **THEN** 页面在运行健康区域显示“运行版本未更新”提示、影响范围和进入配置/发布流程的操作入口

#### Scenario: 加载、缺失和错误状态
- **WHEN** 页面正在加载、没有运行记录、usage 缺失、执行服务不可用或接口请求失败
- **THEN** 页面分别展示骨架屏、空态、指标未知、服务异常或可重试错误，不将缺失数据伪装成 0

#### Scenario: 窄屏访问
- **WHEN** 管理员在窄屏设备访问 Agent 级 Dashboard
- **THEN** 顶部 Agent 信息和筛选器可折叠，卡片与图表单列堆叠，调用表保持横向滚动且不遮挡状态列
