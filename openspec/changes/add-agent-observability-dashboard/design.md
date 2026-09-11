## Context

当前控制面已经持久化用户、会话、消息和 `AgentRun`，但运行时只保存状态、错误码和时间戳，模型 usage、工具调用、成本及分阶段耗时没有进入可查询的数据模型。Web 侧也没有统一的 Agent 级运行分析入口。该变更负责单个 Agent 的详情分析和可复用数据契约；系统级跨 Agent 总览由 `add-system-agent-dashboard` 消费同一套数据和接口。

系统需要支持多租户隔离、管理员权限、分页查询和从聚合指标下钻到单次运行。第一阶段的“在线人数”采用最近活跃时间窗口口径，避免把登录时间误当作实时在线状态；真实心跳在线状态作为后续扩展。

## Goals / Non-Goals

**Goals:**

- 采集每次 AgentRun 的 Token、成本、调用次数、重试次数、状态和分阶段耗时。
- 保存可关联 Agent、模型、工具和错误阶段的观测记录，支持调用详情时间线。
- 提供支持 Agent 范围筛选的租户隔离 Dashboard 汇总、时间序列、调用记录、错误列表和 Trace 查询 API。
- 提供 Agent 级运行分析页面，并支持从 Agent 列表或配置页带 Agent 筛选进入。
- 在不暴露敏感 Prompt/回答的前提下提供足够的故障定位信息。

**Non-Goals:**

- 第一阶段不实现拖拽式自定义 Dashboard。
- 第一阶段不实现跨 Agent 的系统状态看板、Agent 在线/离线判定和租户级排行；这些由 `add-system-agent-dashboard` 负责。
- 第一阶段不引入 ClickHouse、TimescaleDB 或外部观测 SaaS。
- 第一阶段不承诺真实 WebSocket 在线人数，不把 `last_login_at` 作为在线指标。
- 第一阶段不实现自动质量评估、主题聚类和复杂异常检测。

## Decisions

### 1. 使用 AgentRun 汇总字段加 Observation 明细的混合模型

在 `agent_runs` 增加用于列表和聚合的稳定字段：输入/输出/缓存/推理 Token、成本、成本来源、模型调用数、工具调用数、重试数、排队/执行/端到端耗时、TTFT、finish reason 和 provider request ID。新增 `agent_run_observations` 记录 `agent`、`llm`、`tool`、`retrieval` 等步骤，并通过 `parent_observation_id`、`trace_id`、`span_id` 组织调用树。

选择该方案是为了让常用 Dashboard 查询只扫描 `agent_runs`，同时保留单次运行下钻所需的细节。只保留 Observation 会导致聚合查询复杂；只扩展 AgentRun 又无法表达多次模型和工具调用。

### 2. 在 runtime callback 边界采集 usage 和阶段事件

Agent runtime 使用现有 LangChain callback 或等价的执行回调捕获模型、工具开始/结束/错误事件和供应商 usage metadata。`invoke_agent` 的内部结果扩展为带回答、usage 和 observations 的执行结果，Worker 在成功、失败和取消路径统一写入运行摘要。

供应商报告的 Token 和成本优先；缺少成本时根据当时绑定的模型版本价格计算，并保存 `cost_source=provider|calculated|unknown`。缓存 Token 和推理 Token作为总 Token 的组成明细，不能重复累加。

### 3. Dashboard API 采用聚合与明细分离

提供 summary、timeseries、runs、errors 和 trace 五类管理员接口。summary 和 timeseries 返回已经按筛选条件聚合的数据；runs/errors 返回游标或页码分页的明细；trace 返回单次运行的时间线。所有查询首先按 `tenant_id` 和管理员权限约束，再应用可选的 Agent、版本、模型、用户、状态和时间范围筛选。Agent 级页面必须传入 Agent 范围；系统级页面可以省略该范围以获取租户内综合数据。

初期使用 PostgreSQL 聚合查询和必要索引。后续如果查询量明显增长，再增加小时/天级 `agent_metric_buckets` 汇总表，不改变 API 契约。

### 4. 采用“最近活跃用户”替代伪实时在线

第一阶段将在线卡片命名为“近5分钟活跃用户”，按指定窗口内产生消息或 AgentRun 的去重用户计算，并单独展示当前 queued/running 的活跃任务。真实在线人数需要浏览器心跳或 WebSocket 状态与 TTL，单独作为后续能力设计。

### 5. 敏感内容默认不进入观测数据

Observation 只保存结构化元数据、错误类型、错误码、关联 ID 和经过限制的 provider 信息，不默认保存完整 Prompt、回答或堆栈。受权限保护的运行详情可以通过现有会话/消息查询链路获取必要内容；列表和聚合接口不得返回原始内容。

## Risks / Trade-offs

- [供应商 usage 字段不一致] → 建立 provider adapter，统一映射 Token、成本和 finish reason；无法映射时保留 null 与 `unknown` 来源。
- [流式输出尚未真正分片] → 只有在收到第一个真实输出事件后才填充 TTFT，当前路径不得用最终回答时间伪造 TTFT。
- [运行失败时摘要不完整] → Worker 在异常和取消的 finally 路径写入已采集字段，并允许字段为空但保留状态和错误码。
- [聚合查询随数据增长变慢] → 先建立租户、Agent、状态、错误和时间索引；达到阈值后切换到小时聚合表。
- [观测数据泄露敏感内容] → 结构化字段白名单、日志脱敏、管理员权限校验和原始内容短留存策略。
- [成本因价格变化产生历史漂移] → 每次运行保存价格版本或单价快照，历史成本不依赖当前模型配置重算。

## Migration Plan

1. 新增数据库字段、观测表和索引，旧 AgentRun 记录允许指标字段为空。
2. 部署 runtime/Worker 采集逻辑，先对新运行写入摘要和 Observation。
3. 部署管理员 API，验证租户隔离、分页和聚合口径。
4. 部署 Web Dashboard，默认展示“暂无历史指标”而不是把缺失数据显示为零。
5. 观察写入失败率和查询耗时；必要时关闭明细 Observation 写入但保留 AgentRun 状态，之后再补偿或重启采集。

回滚时先隐藏 Dashboard 入口和接口调用，再回滚采集代码；新增列和表保留以避免破坏已经写入的历史数据。

## Open Questions

- 模型供应商是否能稳定返回 billed cost，还是所有成本都需要基于模型版本价格本地计算？
- 原始 Prompt/回答的受保护详情是否复用现有会话权限，还是需要单独的运行查看权限？
- 运行观测数据默认保留 30 天还是按租户配置保留周期？
- 何时以查询耗时和数据规模为阈值引入小时聚合表？
