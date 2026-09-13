## 1. 健康与租约数据模型

- [x] 1.1 新增 `agent_runtime_health` 模型，包含生命周期外的健康状态、原因、最近运行事实、统计窗口、待发布标识和观察时间
- [x] 1.2 为 `agent_runtime_health` 增加 `(tenant_id, agent_id)` 唯一约束及按健康状态、观察时间的查询索引
- [x] 1.3 新增全局 `runtime_service_leases` 模型，记录 Worker instance ID、心跳、过期时间、队列深度、活动运行数和服务状态
- [x] 1.4 编写迁移并验证没有健康快照或租约的旧 Agent 显示为未知/无近期活动

## 2. 执行服务与健康派生

- [x] 2.1 在共享 Worker 启动、轮询、处理和关闭路径写入执行服务租约，不按租户复制 Worker 事实
- [x] 2.2 实现租约过期检测、时钟容差和执行服务 unavailable/unknown 状态转换
- [ ] 2.3 实现 Agent 健康协调任务，根据 AgentRun、版本一致性、per-Agent runtime lease 和执行服务状态计算健康快照
- [ ] 2.4 固定三层状态口径：生命周期、执行态、健康态；运行中按 queued/running AgentRun 去重，active 无租约且无请求显示无近期活动/未知
- [ ] 2.5 固定错误率、P95、active run 数的统计窗口和最小样本规则，避免单次失败直接污染健康状态
- [ ] 2.6 记录 pending_publish、health_reason、computed_at 和 observed_at，支持状态表解释与 stale/unknown 提示

## 3. 系统级管理 API

- [x] 3.1 扩展共享 Dashboard 查询契约，支持 all-agents scope 和 fixed-agent scope，并统一总用户、活跃用户、近5分钟活跃用户及错误数/错误率口径
- [x] 3.2 实现 `GET /v1/admin/system/dashboard/summary`，返回 Agent 库存、运行中/排队中、健康、执行服务和综合使用指标
- [x] 3.3 实现 `GET /v1/admin/system/dashboard/timeseries`，返回调用、用户、会话、状态、错误、Token、成本和延迟趋势
- [x] 3.4 实现 `GET /v1/admin/system/dashboard/agents/status`，提供分页 Agent 状态、健康原因、最近运行、错误率、P95、active run 和待发布标识
- [x] 3.5 实现 `GET /v1/admin/system/dashboard/rankings`，提供调用量、活跃用户、失败率、Token、成本、P95 和工具调用排行
- [x] 3.6 增加 runs/errors/trace 的 all-agents 查询支持，点击 Agent 或图表时可带 Agent、时间和状态条件下钻
- [ ] 3.7 为系统 API 增加租户隔离、管理员权限、分页边界、空租户、跨租户越权和指标范围一致性测试

## 4. 系统 Dashboard Web 页面

- [x] 4.1 在 Agent 管理区域增加系统运行分析入口，并与 Agent 级运行分析入口区分
- [ ] 4.2 实现统一筛选栏：时间、Agent、生命周期、健康态、版本、模型、用户/部门、运行状态和手动刷新
- [x] 4.3 实现 Agent 库存、运行中/排队中、健康态、离线/无近期活动、待发布和执行服务状态卡片
- [ ] 4.4 实现总用户、活跃用户、近5分钟活跃用户、会话、调用、成功率、错误、Token、成本和 P50/P95/P99 指标卡
- [ ] 4.5 实现调用/用户/会话/错误/Token/成本/延迟趋势图，并支持时间桶点击下钻
- [ ] 4.6 实现 Agent 排行和分布视图，展示调用量、活跃用户、失败率、Token、成本、P95 和工具调用
- [x] 4.7 实现分页 Agent 状态表，展示生命周期、执行态、健康态、原因、最近运行、失败、错误率、P95、活动 Run 和待发布
- [x] 4.8 实现跨 Agent 调用记录、错误列表和 Trace 详情入口，复用 Agent 级页面的详情组件
- [x] 4.9 处理空租户、无健康数据、执行服务不可用、指标缺失、权限错误、加载和响应式状态
- [x] 4.10 按 UI 规格实现系统 Dashboard 的分层布局、状态分组、卡片口径、图表下钻、Agent 状态表和右侧详情抽屉
- [ ] 4.11 复用现有绿色主题和组件 token，完成桌面端、窄屏、骨架屏、空态、服务异常和指标缺失的视觉验收

## 5. 验证与上线

- [ ] 5.1 增加 Worker 租约和多 Worker 聚合测试，验证租约过期与队列积压显示
- [ ] 5.2 增加 Agent 健康派生测试，覆盖 active 无活动、per-Agent 租约过期、连续失败、待发布和服务不可用
- [ ] 5.3 增加系统聚合指标测试，覆盖 0 Agent、全部停用、多个并发 queued/running、缺失 usage 和错误率窗口
- [ ] 5.4 增加浏览器端到端测试，覆盖筛选联动、趋势下钻、排行、状态表分页和 Trace 打开
- [ ] 5.5 增加浏览器端视觉/交互验收，覆盖状态文字与颜色、页面层级、详情抽屉、筛选保持和窄屏布局
- [ ] 5.6 记录健康快照和执行租约留存周期、告警阈值、stale 判定及小时聚合表启用条件
