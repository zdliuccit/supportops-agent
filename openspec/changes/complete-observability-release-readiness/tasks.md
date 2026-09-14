## 1. Runtime 观测采集

- [x] 1.1 梳理 AgentRun、AgentRunObservation、错误事件和 Worker 的成功/失败/取消路径，补齐统一观测上下文与白名单字段。
- [x] 1.2 在模型调用开始/结束/失败回调中记录阶段耗时、TTFT、usage、finish reason、重试次数和 provider request ID。
- [x] 1.3 在工具与检索调用开始/结束/失败回调中记录 trace/span/parent 关联、输入输出摘要和耗时。
- [x] 1.4 统一 Worker 终态持久化 AgentRun 摘要与 Observation，保证观测写入失败不覆盖原始运行结果。
- [x] 1.5 补齐 provider 成本映射、价格快照来源和缺失 usage/cost 的 unavailable 语义。
- [x] 1.6 对 Observation metadata、异常文本、结构化日志和 provider 返回值执行字段白名单与敏感信息脱敏。

## 2. Agent 健康计算

- [x] 2.1 实现健康协调器，读取 AgentRun、活动 Run、版本 digest 和执行服务租约并写入 AgentRuntimeHealth。
- [x] 2.2 固定生命周期、执行态、健康态、待发布和执行服务状态的转换规则及 health_reason。
- [x] 2.3 固定错误率、P50/P95、active run 的时间窗口、最小样本和 stale/unknown 判定。
- [x] 2.4 将健康协调器接入 Worker 生命周期或本地调度入口，并补齐租约过期与服务不可用处理。

## 3. Dashboard 数据口径

- [x] 3.1 统一 summary/timeseries/rankings/runs/errors/trace 的租户、时间、Agent 范围和 unavailable 语义。
- [x] 3.2 补齐 Dashboard 趋势聚合、版本/模型/用户/部门/状态筛选及稳定排序分页接口。
- [x] 3.3 补齐系统级健康、库存、活跃用户、会话、Token、成本、延迟和错误聚合指标接口。
- [x] 3.4 补齐排行维度、图表桶下钻和 Agent/错误/Trace 关联参数，保持前后端类型同步。
- [x] 3.5 完善 Agent 级和系统级 Dashboard 的加载、空态、错误、指标缺失、响应式和下钻展示。

## 4. 自动化与真实环境验证

- [x] 4.1 增加 Runtime 观测、健康派生、成本口径、租约过期和失败隔离的后端单元/集成测试。
- [x] 4.2 增加 Dashboard 聚合、租户隔离、筛选、分页、排行、错误详情和 Trace 权限测试。
- [x] 4.3 增加 Analytics 浏览器 E2E：导航、旧路由跳转、筛选联动、分页、排行图表和详情抽屉。
- [x] 4.4 增加真实 PostgreSQL 迁移、历史回填、约束、旧数据兼容和 checkpointer 恢复测试。
- [x] 4.5 固定本地 E2E/真实数据库测试数据、令牌、启动命令和失败诊断方式，更新 README。

## 5. 视觉、性能与发布整理

- [x] 5.1 完成 Dashboard 桌面端与窄屏视觉验收，覆盖图表、表格、筛选、Drawer、长名称和状态颜色。
- [x] 5.2 检查首屏加载、路由 chunk、图表 bundle 和运行时请求瀑布，按收益实施路由级懒加载或拆包。
- [x] 5.3 运行 Ruff、mypy、前端 type-check/build、后端测试、真实 PostgreSQL 测试和 OpenSpec 严格校验。
- [ ] 5.4 整理 Git 工作区，解决 staged/working-tree 冲突、移除临时 IDE 文件，并核对新增文件已明确暂存。
- [x] 5.5 更新相关 OpenSpec 原 change 的完成状态和发布说明，形成可审阅的最终验收清单。
