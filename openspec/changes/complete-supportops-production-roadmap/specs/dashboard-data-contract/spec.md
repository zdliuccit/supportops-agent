## MODIFIED Requirements

### Requirement: Dashboard endpoints SHALL share one metric scope contract
Summary, timeseries, rankings, runs, errors and trace responses SHALL apply the same tenant, time-window and optional Agent scope rules for system and Agent views. Supported version, model, user/department, lifecycle, health and execution-state filters SHALL share semantics across applicable views; unsupported combinations SHALL be explicit. Waiting, attempts, tool actions, approval and reconciliation facts SHALL be distinguished from completed business runs, while P50/P95/P99, cost and usage retain source and unavailable-state semantics.

#### Scenario: System scope
- **WHEN** an administrator requests a system Dashboard without an Agent filter
- **THEN** every aggregate and list response covers only the current tenant and the selected time window across all Agents

#### Scenario: Agent scope
- **WHEN** an administrator requests an Agent Dashboard with an Agent ID
- **THEN** aggregates, trends, records and errors are constrained to that Agent and reject cross-tenant IDs

#### Scenario: 统一筛选与等待状态
- **WHEN** 管理员按用户、模型和等待审批状态筛选
- **THEN** 适用卡片/趋势/列表使用同一范围，未支持的维度明确提示，不把审批等待算作模型执行耗时

### Requirement: Dashboard list queries SHALL expose stable filters and pagination
Runs and errors SHALL support stable ordering, page boundaries and the documented Agent, user/department, status, severity and time filters; unavailable metrics SHALL remain distinguishable from zero. Agent status, ranking/drill-down and action/approval views SHALL preserve applicable originating filters and cursor/page semantics. Web views SHALL support loading, empty, denied, unavailable, stale, responsive and keyboard-accessible states without reverting to inconsistent aggregate scope.

#### Scenario: Filter and page combination
- **WHEN** an administrator changes an Agent filter or page size
- **THEN** the API returns the matching page, total count and stable ordering without leaking records outside the tenant

#### Scenario: Drill-down
- **WHEN** a user clicks a chart bucket, ranking item or error row
- **THEN** the destination list or trace view preserves the originating time and Agent scope

#### Scenario: 排行与状态表下钻
- **WHEN** 管理员从特定部门/版本的排行或健康状态点击运行详情
- **THEN** 有效筛选被保留且分页重置，缺失指标与零值区分，窄屏和键盘入口可操作
