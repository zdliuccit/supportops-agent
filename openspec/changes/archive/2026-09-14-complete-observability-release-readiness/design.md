## Context

项目已有 Agent 控制面、Worker、聊天和 Analytics 页面，运行记录也已经具备基础字段与查询接口。当前缺口集中在跨层闭环：Worker 的真实执行事实没有完整沉淀为 Observation，系统级健康状态没有稳定计算，Dashboard 的不同页面需要统一指标口径，且浏览器与真实 PostgreSQL 验收尚未建立。

本 change 依赖现有 `add-agent-observability-dashboard`、`add-system-agent-dashboard` 和 `split-analytics-dashboard-navigation` 的代码与契约，目标是完成它们的剩余验收范围，而不是重新设计已有页面或破坏兼容 API。

## Goals / Non-Goals

**Goals:**

- 在 Worker 的模型、工具、检索和终态路径采集可追踪、可脱敏、可重放的运行事实。
- 以统一统计窗口和最小样本规则生成 Agent 健康快照，并解释 stale、未知和待发布状态。
- 统一系统级与 Agent 级 Dashboard 的指标、趋势、排行、筛选、分页和下钻口径。
- 建立浏览器关键路径、真实 PostgreSQL schema/回填/checkpointer 的可重复验收。
- 完成视觉、响应式、性能、Git 工作区和发布文档检查。

**Non-Goals:**

- 不引入新的外部可观测性 SaaS 或替换现有 Recharts/shadcn 组件。
- 不改变既有 Agent 配置、授权、聊天路由和历史会话固定版本语义。
- 不在本 change 中实现告警通知、小时聚合仓库或商业计费系统。

## Decisions

### 1. 观测采集放在 Worker/runtime 边界

模型、工具、检索和终态事件统一由 Worker/runtime 记录，产品 API 只读取规范化摘要。优先复用现有 `AgentRun`、`AgentRunObservation` 和错误事件模型，避免页面端自行推导调用事实。

### 2. 健康状态采用快照而不是请求时临时拼接

健康协调器按固定窗口读取 AgentRun、活动 Run、版本摘要和执行服务租约，写入 `AgentRuntimeHealth`。请求时只做过期判定和兼容 fallback，从而保证列表、卡片和 Dashboard 的状态一致。

### 3. 指标口径由服务端统一

summary、timeseries、rankings、runs、errors 和 trace 共享租户、时间窗口、Agent 范围和分页约定。前端只负责展示、排序可视化和下钻，不重复计算成功率、错误率、P95 或成本。

### 4. 测试分为快速默认门禁和显式真实环境门禁

默认测试保持 SQLite/Mock 的快速反馈；通过 `SUPPORTOPS_RUN_POSTGRES_TESTS=1` 显式启用真实 PostgreSQL、迁移、回填、约束和 checkpointer 测试。浏览器 E2E 使用本地管理员/员工令牌和固定 seed 数据，禁止连接生产环境。

### 5. 视觉和性能以现有组件规则为准

Analytics 页面继续使用统一 `PageHeader`、`ListToolbar`、`AppTable`、`StatusBadge`、`ListLoadingOverlay` 和 Recharts。验收覆盖桌面、窄屏、空态、错误态、指标缺失、长名称和图表横向溢出；性能检查记录构建体积并优先拆分路由级 chunk。

## Risks / Trade-offs

- [观测写入失败影响运行] → 观测和错误事件追加写必须隔离主运行事务，记录告警日志但不能覆盖原始运行终态。
- [健康快照滞后] → 保存 `computed_at`/`observed_at`，按租约过期时间标记 stale，并在 UI 显示原因。
- [历史数据指标为空] → 统一使用“未知/不可用”而不是伪造零值，前端保留兼容展示。
- [真实 PostgreSQL 环境不稳定] → 使用项目提供的本地 Compose/独立测试数据库，测试前检查连接和迁移版本，失败时保留可诊断日志。
- [E2E 对时间和异步状态敏感] → 使用 API 等待与稳定 data-testid，验证 Loading、终态和错误，而不是固定 sleep。
- [图表和图标增大 bundle] → 复用已有 Recharts/lucide 依赖，完成构建产物基线并按路由懒加载评估。

## Migration Plan

1. 先补齐 runtime observation、成本与终态持久化，并为旧记录保持空值兼容。
2. 启用健康协调计算，回填或按需生成缺失快照，验证租约过期和待发布提示。
3. 对齐 Dashboard API 数据契约，补充筛选、分页、排行和下钻测试。
4. 补浏览器 E2E 与真实 PostgreSQL 测试，记录运行命令和环境前置条件。
5. 完成视觉/窄屏/性能检查，清理工作区临时文件和 staged 冲突，再进入发布评审。

回滚时保留新增观测和健康表，前端可回退到兼容的空指标展示；不删除已有运行记录或版本数据。

## Open Questions

- 健康协调任务在本地采用 Worker 内置周期任务还是独立 CLI 调度，目前优先复用 Worker 生命周期以减少部署面。
- 成本价格快照的最终来源需要与后续计费需求确认；本 change 先保证来源字段和缺失状态正确。
- E2E 是否纳入 CI 需要在真实 PostgreSQL 环境稳定后决定，本地命令和测试数据先固定下来。
