## Why

当前 Agent 管理、聊天和 Analytics 页面已经可以演示，但运行时观测数据、Agent 健康状态、Dashboard 指标口径和真实环境验收仍未形成闭环。需要把这些剩余工作收敛为一次可验收的收尾变更，确保系统能够解释每次调用、准确反映 Agent 健康，并具备发布前的自动化验证证据。

## What Changes

- 补齐 Runtime 对模型、工具和运行阶段的观测采集，统一记录 usage、耗时、错误、重试、成本来源和脱敏 metadata。
- 增加 Agent 健康快照协调计算，统一生命周期、执行态、健康态、待发布和执行服务状态口径。
- 收敛 Dashboard summary、timeseries、排行、调用记录和错误分析的数据契约，保证系统级与 Agent 级统计可解释、可筛选、可下钻。
- 补齐 Dashboard 关键交互和异常状态的浏览器端 E2E 测试，并执行真实 PostgreSQL schema、回填、约束和 checkpointer 回归测试。
- 完成桌面端/窄屏视觉验收、性能检查、构建产物检查和发布前 Git/文档整理。

## Capabilities

### New Capabilities

- `runtime-observability-completion`: 完成 Agent Runtime 观测采集、持久化、脱敏和成本口径。
- `agent-health-computation`: 根据运行事实、版本一致性和执行服务租约计算可解释的 Agent 健康快照。
- `dashboard-data-contract`: 统一 Dashboard 指标、趋势、排行、调用记录和错误分析的数据范围、筛选与分页契约。
- `release-readiness-validation`: 提供浏览器 E2E、真实 PostgreSQL、视觉、性能和发布整理验收门禁。
- `agent-observability-dashboard-completion`: 收敛 Agent 级 Dashboard 的指标、趋势、Trace 和验收闭环。
- `system-agent-dashboard-completion`: 收敛系统级 Dashboard 的健康、聚合、排行和跨 Agent 下钻闭环。

### Modified Capabilities

无。本 change 为现有进行中 change 提供独立的收尾验收契约，不直接修改已归档的主规格。

## Impact

- 后端：`apps/api`、`apps/agent_worker`、`packages/support_core`、Alembic migrations、Dashboard API schemas and tests。
- 前端：`apps/web_chat` 的 Analytics 页面、图表、筛选、加载/空态/错误态、E2E 测试和构建配置。
- 验收环境：真实 PostgreSQL/Redis/Worker，本地测试令牌和浏览器自动化运行环境。
- 不改变已有 Agent 配置、授权和聊天 API 的兼容行为；仅补齐观测字段、健康状态和验收契约。

## 验收与发布说明

- 关联 change `add-agent-observability-dashboard`、`add-system-agent-dashboard`、`split-analytics-dashboard-navigation` 的 OpenSpec artifact 状态均已核对为 `complete`；本 change 记录的是对其运行时、数据口径和验收缺口的收尾补齐。
- 已完成 Runtime 模型/工具回调观测、失败隔离持久化、健康快照协调、系统与 Agent 级 Dashboard 数据口径、Analytics 浏览器 E2E、真实 PostgreSQL 回归、路由懒加载和前端/后端质量门禁。
- 发布前仍需在目标工作区人工处理既有的 staged/working-tree 冲突及 `.idea/` 临时 IDE 文件；本 change 不覆盖或删除这些用户已有文件。
- 通过门禁：`uv run pytest -q`（72 passed, 2 skipped）、`SUPPORTOPS_RUN_POSTGRES_TESTS=1 uv run pytest -q tests/postgres`（2 passed）、`uv run ruff check .`、`uv run mypy`、前端 `type-check`/`build`、Playwright E2E（3 passed）和 OpenSpec strict validate。
