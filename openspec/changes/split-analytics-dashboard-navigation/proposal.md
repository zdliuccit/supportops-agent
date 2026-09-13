## Why

当前总览页把系统概览、Agent 排行、运行状态和错误信息堆在同一页面，信息密度高且难以定位问题；导航也无法表达“分析”这一独立工作流。现在已有运行数据基础，需要把 Analytics 作为一级入口，将各类分析拆成可聚焦、可扩展的页面，并提供可追溯的错误事件明细。

## What Changes

- 将 Analytics 设为位于“智能体管理”之前的一级菜单，提供 Dashboard、Agent 调用排行、Agent 状态、错误分析四个二级菜单。
- 将现有系统总览收敛为 Analytics > Dashboard；排行、状态和错误分析拆分为独立页面，保留统一的时间范围、Agent 和刷新交互。
- 为错误分析增加分页事件列表、筛选、排序和详情抽屉，展示错误原因、用户、Agent、会话/运行、模型版本、发生时间、阶段、严重级别及恢复状态。
- 增加错误事件查询接口与详情接口，保留现有聚合指标接口供 Dashboard 使用，并保证租户隔离和管理员权限校验。
- 为 Agent 列表的统计入口接入 Agent 运行分析页面；旧 Dashboard 路径提供兼容跳转。
- Analytics 下所有页面统一白色背景、清晰的区块层级和空/加载/错误状态。

## Capabilities

### New Capabilities

- `analytics-navigation`: Analytics 一级菜单、二级路由、权限和旧路径兼容跳转。
- `analytics-pages`: Dashboard、Agent 调用排行、Agent 状态和 Agent 运行分析页面的展示与交互。
- `error-analysis`: 错误事件采集、分页筛选查询、详情追踪和前端列表/抽屉展示。

### Modified Capabilities


## Impact

- `apps/web_chat`: SidebarNavigation、MainLayout、路由及 Analytics 页面组件和 API 类型。
- `apps/api` 与 `packages/support_core`: 错误事件模型、迁移、采集逻辑及管理员查询路由。
- `tests/api` 与浏览器端 E2E：覆盖导航、列表筛选分页、错误详情和白色页面背景。
