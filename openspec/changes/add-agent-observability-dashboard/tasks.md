## 1. 数据模型与迁移

- [x] 1.1 为 `agent_runs` 增加输入/输出/缓存/推理 Token、成本及来源、模型/工具/重试次数、分阶段耗时、TTFT、finish reason 和 provider request ID 字段
- [x] 1.2 新增 `agent_run_observations` 模型，支持 agent、llm、tool、retrieval 类型及 trace/span/parent 关联
- [x] 1.3 为运行摘要和观测表创建租户、Agent、状态、错误和时间范围查询索引
- [x] 1.4 编写数据库迁移并验证旧 AgentRun 记录可以以空指标兼容读取

## 2. Runtime 与 Worker 采集

- [x] 2.1 定义统一的 Agent 执行结果和 usage/observation 数据结构
- [x] 2.2 在 Agent runtime 接入模型和工具开始、结束、失败回调，采集 usage、finish reason、重试和阶段时间
- [x] 2.3 将 provider usage 映射为统一 Token 字段，并在缺失时保留未知状态而不是写入零值
- [x] 2.4 根据运行时价格快照计算缺少 provider 成本的运行成本，并保存成本来源和价格版本
- [x] 2.5 在 Worker 成功、失败和取消路径统一持久化 AgentRun 摘要和 Observation
- [x] 2.6 保证当前非流式路径不伪造 TTFT，只在收到真实首个输出事件时记录该指标
- [x] 2.7 对观测 metadata、异常和日志进行字段白名单与敏感信息脱敏

## 3. 管理 API

- [x] 3.1 实现租户隔离且支持可选 Agent 范围的 Dashboard summary 接口，返回总用户、活跃用户、近5分钟活跃用户、概览卡片指标和上一周期对比
- [x] 3.2 实现按时间粒度聚合的调用量、用户、会话、状态、错误数/错误率、Token、成本和延迟 timeseries 接口；Agent 级调用必须固定 Agent 范围，系统级调用可省略范围
- [x] 3.3 实现分页 AgentRun 调用记录接口，支持时间、Agent、版本、模型、用户/部门和状态筛选
- [x] 3.4 实现错误聚合接口，返回错误码、次数、影响用户数、涉及 Agent 和最后发生时间
- [x] 3.5 实现单次 AgentRun Trace 详情接口，返回按时间排序的观测步骤和运行摘要
- [x] 3.6 为所有接口增加管理员权限、参数校验、稳定排序、分页边界和跨租户访问测试
- [x] 3.7 固定 summary/timeseries/runs/errors/trace 的共享响应口径，使 Agent 级和系统级 Dashboard 不重复定义指标

## 4. Dashboard Web 页面

- [x] 4.1 在 Agent 列表/配置页增加 Agent 级运行分析入口，并固定当前 Agent 筛选
- [x] 4.2 实现统一筛选栏：时间、版本、模型、用户/部门、状态和手动刷新
- [x] 4.3 实现活跃用户、近5分钟活跃用户、新增会话、Agent 调用、成功率、Token、成本和 P95 耗时指标卡
- [x] 4.4 实现调用量/会话趋势、成功失败趋势、Token/成本趋势和延迟分位数图表
- [x] 4.5 实现分页调用记录表，并支持从图表点击进入带筛选的记录列表
- [x] 4.6 实现错误分析列表和错误聚合下钻
- [x] 4.7 实现单次运行详情抽屉，展示排队、Worker、模型、工具和完成/失败时间线
- [x] 4.8 处理加载、空数据、指标缺失、错误、无权限和响应式布局状态
- [x] 4.9 按 UI 规格实现 Agent 上下文头部、运行健康区、指标卡、趋势/错误/调用区、Trace 详情抽屉和返回系统 Dashboard 入口
- [x] 4.10 复用系统 Dashboard 的绿色主题和组件 token，完成桌面端、窄屏、版本未更新提示、骨架屏、空态和错误态的视觉验收

## 5. 验证与运维准备

- [x] 5.1 增加运行摘要、Token/成本口径、失败落库和 Observation 关联的后端测试
- [x] 5.2 增加 Dashboard 聚合、租户隔离、分页、筛选和 Trace 权限测试
- [x] 5.3 增加 Web Dashboard 关键筛选、下钻、空数据和错误状态的浏览器端测试
- [x] 5.4 增加 Agent 级 Dashboard UI 验收，覆盖 Agent 上下文、状态颜色、筛选保持、Trace 抽屉和窄屏布局
- [x] 5.5 使用本地样例运行验证成功、失败、工具调用、多模型调用和缺少 usage 的展示结果
- [x] 5.6 记录观测数据留存周期、敏感字段策略和未来小时聚合表的迁移触发条件
