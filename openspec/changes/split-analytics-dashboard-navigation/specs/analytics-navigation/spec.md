## ADDED Requirements

### Requirement: Analytics 一级导航
系统 SHALL 在“智能体管理”之前显示名为“Analytics”的一级菜单，并提供 Dashboard、Agent 调用排行、Agent 状态、错误分析四个二级菜单；仅具备管理员权限的用户可见。

#### Scenario: 管理员看到 Analytics 菜单
- **WHEN** 管理员打开工作台
- **THEN** 侧边栏在智能体管理之前显示 Analytics 及四个二级入口

#### Scenario: 非管理员访问 Analytics
- **WHEN** 非管理员请求任一 `/analytics/*` 路由
- **THEN** 页面拒绝访问或按既有权限策略跳转，且不泄露分析数据

### Requirement: Canonical Analytics routes
系统 SHALL 使用 `/analytics/dashboard`、`/analytics/rankings`、`/analytics/agents` 和 `/analytics/errors` 作为四个页面的 canonical 路由，并保留 `/agent-management/dashboard` 的兼容跳转。

#### Scenario: 旧总览链接兼容
- **WHEN** 用户打开 `/agent-management/dashboard`
- **THEN** 应跳转到 `/analytics/dashboard` 并保持管理员权限校验

#### Scenario: Agent 统计入口
- **WHEN** 用户点击 Agent 列表中的统计图标
- **THEN** 应打开对应 Agent 的运行分析页面并携带 agent id
