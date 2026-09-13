## Why

Agent 运行后目前缺少统一的可观测性入口，管理员无法回答用户活跃度、调用量、Token 消耗、成本、延迟、错误和版本影响等基本运营问题。该变更负责单个 Agent 的运行分析、统一指标和下钻数据契约；跨 Agent 的系统总览由 `add-system-agent-dashboard` 复用这些能力并单独聚合，避免两套指标口径分裂。

## What Changes

- 新增 Agent 级运行分析 Dashboard，支持按时间、Agent 版本、模型、用户/部门和状态筛选。
- 提供活跃用户、近期开启会话、Agent 调用、成功率、Token、成本和延迟等概览指标及趋势图。
- 新增分页调用记录、错误聚合和单次运行详情，支持从图表和错误下钻到完整运行链路。
- 精细设计 Agent 级 Dashboard UI：突出当前 Agent 身份和运行健康，明确指标卡、趋势分析、错误分析、调用记录和 Trace 详情的层级与交互。
- 扩展 AgentRun 运行摘要，采集输入/输出 Token、成本、模型调用、工具调用、重试和分阶段耗时。
- 新增 Agent 运行观测记录，关联 Agent、模型、工具和错误阶段，保留 correlation ID 以便排查。
- 增加可被 Agent 级页面和系统级页面复用的 Dashboard 查询接口、租户隔离、分页、筛选和必要的数据索引；接口支持带 Agent 范围筛选。
- 第一阶段将“在线人数”定义为最近时间窗口内的活跃用户；后续可接入心跳实现真实在线状态。

## Capabilities

### New Capabilities

- `agent-observability-dashboard`: Agent 运行指标概览、趋势分析、筛选和下钻页面。
- `agent-run-analytics`: AgentRun 运行摘要、Token/成本/延迟指标、调用记录和错误查询。
- `agent-run-tracing`: Agent、模型、工具和检索步骤的运行观测记录及详情链路。

### Modified Capabilities


## Impact

- 后端运行模型和 Worker/Agent runtime：需要捕获模型 usage、工具调用和阶段时间，并在成功/失败时持久化摘要。
- 数据库：扩展 `agent_runs`，新增运行观测表和查询索引；需要明确 Token、成本和敏感内容留存策略。
- 管理 API：新增 Dashboard 汇总、时间序列、调用记录、错误列表和 Trace 详情接口，全部按租户和管理员权限隔离。
- Web 控制面：在 Agent 管理列表/配置页提供 Agent 级运行分析入口；系统级运行总览由 `add-system-agent-dashboard` 单独增加。
- 监控和运维：预留 OpenTelemetry GenAI 字段映射、告警和后续聚合表扩展，不在本变更中强制引入外部观测平台。
