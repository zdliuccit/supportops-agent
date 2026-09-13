## Context

现有系统 Dashboard 已有汇总、趋势、运行记录、排行和状态数据，但前端全部集中在一个页面；错误接口只有聚合结果，无法从指标追溯到具体用户或运行。实现跨越 React 路由/导航、FastAPI 查询层、运行时事件采集和数据库迁移，并必须兼容现有 `/agent-management/dashboard` 与 Agent 运行分析链接。

## Goals / Non-Goals

**Goals:**

- 建立 Analytics 一级菜单及四个清晰的二级页面，并让页面在白色画布上共享筛选、刷新、加载和错误状态。
- 将 Agent 调用排行、Agent 状态从总览中独立出来，同时保留系统 Dashboard 的关键 KPI 和趋势。
- 持久化结构化错误事件，提供带租户隔离的分页查询和详情追踪，支持错误分析列表与抽屉。
- 从 Agent 列表统计入口直达指定 Agent 的运行分析。

**Non-Goals:**

- 不重写已有指标计算、运行时协议或 LangSmith 集成。
- 不在本变更中增加告警规则、通知渠道、导出任务或实时 WebSocket 推送。
- 不改变普通成员对 Agent 管理页面的既有权限模型。

## Decisions

1. **路由采用 `/analytics/*`，旧路径兼容跳转。** Analytics 作为导航语义和 URL 语义的单一入口；`/agent-management/dashboard` 与现有 Agent dashboard 保留重定向，避免书签和统计按钮失效。相比直接删除旧路径，兼容跳转风险更低。
2. **错误采用独立 `agent_error_events` 事件表。** 每次失败写入一条不可变事件，关联 run、conversation、agent、agent version、model version 和 user；展示名称通过查询时 join 获取。相比继续从 runs 聚合，能够保留重复错误、用户和阶段信息，也避免把展示字段复制到运行表。
3. **错误列表使用服务端分页和筛选。** API 接收 `page/page_size/from/to/severity/error_code/agent_id/user_id/status`，按 `occurred_at DESC, id DESC` 稳定排序；详情接口返回关联运行及 trace。相比一次性加载，能控制大租户数据量并支持未来索引优化。
4. **前端按页面复用轻量分析组件。** 共用页面壳、KPI、筛选条、表格和状态标签，图表继续复用现有 Recharts 依赖；不引入新的图表插件或全局状态库。
5. **错误采集采用运行失败路径同步写入并容错。** `_fail_run` 记录已知上下文和脱敏 metadata；事件写入失败不得掩盖原始运行失败，记录日志后继续完成失败状态更新。

## Risks / Trade-offs

- [历史运行没有错误事件] → 列表仅保证迁移后新失败可追踪；Dashboard 继续使用原有聚合接口，页面明确空数据状态。
- [失败路径增加一次数据库写入] → 仅写入小型结构化事件并建立 tenant/occurred_at、agent/occurred_at、severity/occurred_at 索引；写入异常不阻断主流程。
- [关联对象可能已删除] → API 使用 LEFT JOIN 和可空展示字段，始终返回 error_code、reason、occurred_at 等核心字段。
- [旧链接与新链接并存] → 增加路由重定向测试，并在 Agent 列表统计按钮使用新 canonical URL。
- [错误详情包含敏感输入] → metadata 只保存白名单上下文（阶段、重试次数、延迟、模型标识），不保存 prompt、token 或凭据。

## Migration Plan

1. 新增 `agent_error_events` 迁移并创建索引；部署 API/worker 后新失败开始写入。
2. 发布前端 Analytics 路由和页面，保留旧路由重定向。
3. 观察 API 错误事件写入失败日志与查询耗时；必要时回滚前端到旧总览，数据库表保留以便安全回滚。

## Open Questions

- 是否在后续迭代加入错误事件导出和告警订阅；本变更暂不实现。
