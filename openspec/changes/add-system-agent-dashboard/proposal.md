## Why

现有 `add-agent-observability-dashboard` 主要解决单个 Agent 的运行分析，但管理员还需要从系统视角了解所有 Agent 的整体健康度和使用情况：有多少 Agent 正在运行、离线、未发布或停用，哪些 Agent 调用量和错误最多，以及整个租户的用户、会话、Token、成本和调用趋势。系统级页面必须复用同一套运行指标口径，并补上可判断 Agent 是否真正在线的运行状态数据。

## What Changes

- 新增系统级 Agent Dashboard，默认聚合当前租户内全部 Agent，支持按 Agent、状态、版本、模型、用户/部门和时间筛选。
- 增加 Agent 运行状态概览，区分运行中、排队中、离线、无近期活动、异常、待发布和已停用，并展示状态更新时间和异常原因。
- 增加所有 Agent 的综合指标：用户、近5分钟活跃用户、会话、调用、成功率、Token、成本、延迟、工具调用和错误趋势。
- 增加 Agent 排行和分布：调用量、活跃用户、失败率、Token、成本、P95 延迟，以及模型/版本/工具使用分布。
- 增加跨 Agent 调用记录、错误列表和 Trace 下钻，复用 Agent 级 Dashboard 的筛选与指标口径。
- 精细设计系统级 Dashboard UI：明确页面层级、状态分组、指标卡、趋势图、排行、Agent 状态表、详情抽屉，以及加载/空态/错误态和响应式表现。
- 为运行状态增加心跳或租约机制，定义离线判定、状态转换和过期处理。
- 调整 `add-agent-observability-dashboard` 的范围，使其明确负责 Agent 级详情和共享运行数据契约；系统级聚合由本变更负责。

## Capabilities

### New Capabilities

- `system-agent-dashboard`: 当前租户全部 Agent 的状态、综合指标、趋势、排行和跨 Agent 下钻。
- `agent-runtime-health`: Agent 运行实例心跳、租约、状态派生、离线判定和状态变更记录。

### Modified Capabilities

本变更不直接修改已归档的基础能力规格；上一份未归档的 `add-agent-observability-dashboard` 已同步收窄为 Agent 级详情，并补充系统级复用的范围契约。

## Impact

- 后端：新增系统级聚合查询、Agent 运行状态查询和心跳/租约写入接口；扩展现有 Dashboard API 的可选 Agent 范围。
- 数据库：新增 Agent runtime health/lease 数据及状态索引；复用 `agent_runs` 和运行观测数据，不复制统计事实。
- Worker/运行时：在执行服务启动、心跳、处理和关闭路径更新全局实例租约；由健康协调任务派生 Agent 状态，并处理租约过期和多实例并发。
- Web 控制面：新增系统 Dashboard 入口，Agent 管理页面保留 Agent 级详情入口；共享筛选、图表和详情组件。
- 权限和隔离：普通管理员只能查看当前租户；系统管理员的跨租户汇总作为后续扩展，不改变现有租户边界。
