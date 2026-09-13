## Context

当前 `Agent` 只有生命周期状态 `draft`、`active`、`disabled`，`AgentRun` 只有单次请求的 queued/running/completed/failed/cancelled 状态。运行架构是共享的 Redis Worker 按请求执行 Agent，并不存在每个 Agent 的常驻进程，因此 `Agent.status=active` 不能直接表示 Agent 正在运行，`last_login_at` 也不能表示在线用户。上一份 `add-agent-observability-dashboard` 负责单 Agent 运行指标、调用记录和 Trace；本变更在其数据契约上增加租户级聚合和运行服务健康视图。

系统 Dashboard 的主要使用者是当前租户的平台管理员。默认范围是当前 `tenant_id` 下全部 Agent，不做跨租户平台总览。页面需要同时表达三种不同事实：Agent 生命周期库存、当前执行活动、运行服务/Agent 健康，避免把“已启用”“正在处理请求”和“服务在线”混成一个状态。

## Goals / Non-Goals

**Goals:**

- 提供当前租户全部 Agent 的系统级总览、趋势、排行、调用记录、错误和 Trace 下钻。
- 分开展示 Agent 生命周期、当前执行态和健康态，并给出状态更新时间、原因和指标窗口。
- 支持系统卡片回答 Agent 总数、已启用/草稿/已停用、运行中、离线、无近期活动、待发布、异常和服务不可用数量。
- 汇总上一份变更定义的用户、活跃用户、会话、调用、成功率、错误、Token、成本、延迟和工具指标。
- 为每个 Agent 提供可分页的状态表，并能点击回到 Agent 级调用记录和 Trace。
- 为共享 Worker/执行服务建立心跳、租约、队列积压和服务可用性观测。

**Non-Goals:**

- 第一阶段不实现跨租户平台管理看板；跨租户需要独立的 platform super-admin 权限和数据合规设计。
- 第一阶段不把无请求活动的 active Agent 直接标记为 offline；没有有效租约时展示“无近期活动”或“未知”。
- 第一阶段不把共享 Worker 的服务状态复制到所有 Agent；Worker 故障展示为“执行服务不可用”。
- 第一阶段不实现自定义拖拽图表、质量评估、主题聚类和预算策略。

## Decisions

### 1. 用三层状态表达系统运行情况

系统页面固定展示三组状态：

| 状态层 | 状态 | 计算依据 |
| --- | --- | --- |
| 生命周期 | 全部、已启用、草稿、已停用 | `agents.status` |
| 执行态 | 运行中、排队中、无近期调用 | 当前时间窗内的 `AgentRun` 状态和活动时间 |
| 健康态 | 健康、异常、离线、无近期活动、待发布、未知 | Agent 运行租约、最近成功/失败、错误率、版本一致性和执行服务状态 |

“运行中 Agent”按当前存在 queued/running AgentRun 的去重 Agent 计算；“离线 Agent”只统计生命周期为 active 且明确注册的 Agent 运行租约过期的 Agent；当前共享 Worker 架构下，active 但没有请求和 per-Agent 租约的 Agent 统计为“无近期活动”或“未知”，不强行归入离线。若共享 Worker 心跳过期，系统显示“执行服务不可用”和队列积压，Agent 健康状态可为 unknown。

### 2. 增加 Agent 健康快照与执行服务租约

新增 `agent_runtime_health` 保存 Agent 的派生健康状态和最近事实：`agent_id`、`tenant_id`、`health_status`、`health_reason`、`last_run_at`、`last_success_at`、`last_failure_at`、`active_run_count`、`error_rate`、`p95_latency_ms`、`pending_publish`、`window_start`、`window_end`、`computed_at` 和 `observed_at`，并设置 `(tenant_id, agent_id)` 唯一约束。错误率、P95 和 active run 数必须带统计窗口，不能把不同时间范围混成一个当前状态。该表不是调用事实源，而是由 AgentRun 汇总和健康协调任务更新的读模型。

新增 `runtime_service_leases` 保存共享 Worker/执行服务实例的 `instance_id`、`heartbeat_at`、`expires_at`、`queue_depth`、`active_run_count` 和服务状态。该表是全局执行实例事实，Worker 可服务多个租户，不能按租户复制一份租约；系统 Dashboard 只展示当前租户可见的服务状态。Agent 没有常驻进程时不创建伪造的每 Agent 进程；只有明确注册的 per-Agent runtime lease 才能产生“离线 Agent”，否则使用“无近期活动”或“未知”。

### 3. 系统 API 与 Agent API 复用同一指标契约

保留上一份变更的 `/v1/admin/dashboard/*` 作为共享运行分析契约，`agent_id` 可选：Agent 详情必须固定 Agent 范围，系统页面可以省略以获取当前租户全量。新增系统专用接口：

```text
GET /v1/admin/system/dashboard/summary
GET /v1/admin/system/dashboard/timeseries
GET /v1/admin/system/dashboard/agents/status
GET /v1/admin/system/dashboard/rankings
```

系统 summary 返回 Agent 库存、执行服务和综合运行指标；agents/status 返回每个 Agent 的生命周期、健康、最近运行、错误率、P95、待发布和 active run 数；rankings 返回调用量、活跃用户、失败率、Token、成本、P95 和工具调用排行。系统 runs/errors/trace 直接复用共享接口，只增加 all-agents scope 的筛选。

### 4. 聚合不复制运行事实

所有系统指标从 `agents`、`agent_runtime_health`、`runtime_service_leases`、`agent_runs` 和 `agent_run_observations` 聚合。初期用 PostgreSQL 查询和索引；当跨 Agent 时间序列变慢时，再增加小时级 `agent_metric_buckets`，API 响应结构保持不变。

### 5. 页面先显示库存和健康，再显示综合使用量

页面上方先显示 Agent 状态卡片和执行服务卡片，中段显示用户/会话/调用/Token/成本/延迟综合指标，下方提供趋势、排行、错误和调用记录。每张卡显示统计窗口、状态定义和更新时间；“运行中”“离线”“近5分钟活跃用户”必须使用不同文案。

### 6. 系统 Dashboard 的 UI 采用分层信息架构

系统页面采用“页面标题与刷新信息 → 全局筛选 → 状态总览 → 使用量总览 → 趋势与排行 → Agent 状态表 → 调用/错误下钻”的固定层级，避免把生命周期、运行健康和使用量混在同一排卡片中。

首屏需要同时呈现三组状态卡：生命周期卡使用中性底色，执行态卡使用蓝色系，健康态卡使用绿色/红色/灰色表达健康、异常和未知。状态不能只依赖颜色，必须同时显示文字、数量、更新时间和 tooltip 口径。执行服务异常使用页面级提示条，不能让每个 Agent 都重复显示“离线”。

使用量卡片统一显示主数值、统计窗口、环比变化和可点击下钻入口。趋势区域采用两列响应式网格：调用/用户/会话趋势占主要宽度，错误率、Token/成本和延迟作为配套图表；排行使用可排序表格，Agent 名称、状态和关键指标保持同一行可读。

Agent 状态表使用整行点击和固定表头，状态列、最近活动、错误率、P95、待发布标识和操作入口保持稳定位置。点击行打开右侧详情抽屉，展示该 Agent 的健康原因和最近运行；点击调用或错误数据进入共享 Agent 级 Dashboard，并保留时间、状态和 Agent 筛选。

页面必须提供加载骨架、无数据空态、执行服务不可用、指标缺失和权限错误状态。桌面端使用多列网格；窄屏将卡片和图表改为单列，筛选器收进可展开区域，表格允许横向滚动但首列和状态列保持可见。视觉样式复用现有绿色主题、圆角卡片、边框、按钮和表格组件，不新增另一套颜色或交互语言。

## Risks / Trade-offs

- [当前没有 per-Agent 常驻进程] → 将运行中定义为当前 AgentRun 活动，将离线限定为租约过期；无租约时显示无近期活动/未知。
- [共享 Worker 故障造成全局误判] → 单独展示 runtime service lease，服务不可用时不批量把 Agent 标成离线。
- [健康快照与 AgentRun 暂时不一致] → 保存 `observed_at`，页面显示更新时间；健康协调任务可按 AgentRun 重新计算。
- [状态卡片口径容易混淆] → 三层状态分组、固定定义 tooltip 和契约测试，禁止用单一 status 字段覆盖三种状态。
- [跨 Agent 聚合查询成本高] → 建立租户/时间/Agent/状态索引，并按查询耗时阈值启用小时聚合表。
- [租约误过期] → 使用带时钟容差的 expires_at 和连续两次心跳失败判定，并把原因记录到 health_reason。
- [统计包含敏感内容] → 沿用上一变更的结构化字段白名单和租户/管理员权限，系统列表不返回原始 Prompt/回答。

## Migration Plan

1. 新增 `agent_runtime_health`、`runtime_service_leases` 表及索引，旧 Agent 的健康数据为空时显示“未知/无近期活动”。
2. 在共享 Worker 启动、轮询、处理和关闭路径写入执行服务租约；增加定时任务根据 AgentRun 更新健康快照。
3. 扩展共享 Dashboard 查询的 all-agents scope，新增系统 summary、timeseries、agents/status 和 rankings 接口。
4. 部署系统 Dashboard，先展示明确的未知和无数据状态，再逐步填充健康指标。
5. 观察租约更新成功率、健康快照延迟、聚合查询耗时和跨租户拒绝情况。

回滚时隐藏系统 Dashboard 入口并停止健康协调任务；保留新增表和历史租约数据，避免影响已有 AgentRun 和 Agent 级 Dashboard。

## Open Questions

- Agent 运行租约的默认有效期和时钟容差取多少，是否按租户配置？
- 健康协调任务部署在 Worker 内，还是由独立 scheduler 执行？
- “待发布”只比较 draft 与 active version digest，还是还要纳入模型/工具版本漂移？
- 系统 Dashboard 是否需要导出 CSV，以及导出是否采用异步任务？
