## Why

2026-10-12 的根 `AGENTS.md` 已明确当前 12 个模块的实现边界，但运行可靠性、调用时授权、可信知识、诊断工单、审批复核与生产交付仍缺少可追踪的完整实施任务。现有两个 change 另有 20 项未验收任务，需要将代码现状、已有承诺和 2026 目标设计整理为一个有依赖、阶段和验收条件的项目级规划。

## What Changes

- 新增项目级 OpenSpec 总规划：需求覆盖清单、实施顺序、场景验收、完整未勾选任务；本次只生成规划，不实现业务功能、不修改旧任务完成状态。
- 补强独立 Worker 心跳、Run 通知 Outbox、任务尝试与租约、失效执行者隔离、恢复、取消和同会话串行；将进程内模型探测迁移到可恢复任务。
- 统一工具目录与调用时策略，实施实时身份/Agent 授权/资源范围校验、风险 R0–R4、单工具限制、条件审批及受控 MCP/API 连接。
- 完善上下文裁剪、完整输入估算、累计成本预算，保存结构化产品结果与可信引用；完成知识生命周期验收、索引消费与 ACL 检索。
- 以既有 429 场景基线构建明确的诊断流程，支持证据不足追问、幂等建单、升级包、客服接管和业务动作未知结果对账。
- 建设持久化审批、批准后复检、一次性凭证、独立结果复核与安全审计；高风险验收仅使用隔离 Mock/沙箱目标。
- 补齐真实基础设施故障测试、当前 Run 指标与客户端首文本测量、Dashboard 历史验收、离线质量/安全评测、发布门禁、灰度与回滚。
- 规划凭据提供者与连接器隔离、部署/备份恢复/运维、数据保留与擦除；HA、自动知识更新、企业 SSO、长期记忆、模型路由、专业 Agent 和额外渠道作为有门禁的后续能力。
- **BREAKING（未来实施时）**：部分整集列表会迁移到标准分页响应；新增 Run 等待/对账状态、版本化结构化回答及配置 Schema。每项须先提供 API/Web 同步和旧数据兼容策略，不改写历史会话固定版本。

## Capabilities

### New Capabilities

- `runtime-reliability`: 服务心跳、通知 Outbox、Run 尝试/租约、会话串行、恢复取消及持久化探测任务。
- `runtime-authorization-policy`: 实时主体、Agent 和资源授权、目录策略、风险映射及单工具/租户限制。
- `context-and-budget-control`: 本轮历史边界、证据上下文、裁剪、完整 Token 与累计费用预算。
- `api-contract-completion`: 剩余分页、稳定列表与新接口的 Schema/前端一致性。
- `knowledge-retrieval-and-evidence`: 索引生命周期、权限过滤检索、有效证据与引用校验。
- `structured-support-results`: 产品回答、引用、建议动作、升级事实和流式恢复的版本化持久化。
- `diagnostic-support-workflows`: 单领域 429 流程、受控只读工具、证据判断、追问及升级。
- `governed-tool-connectivity`: 统一执行网关、MCP/API/本地适配、目录治理、入站授权与出站边界。
- `durable-human-approval`: 持久化等待、非本人审批、参数冻结、过期/拒绝/撤销与恢复。
- `business-action-reconciliation`: 动作记录、幂等、业务回执、独立复核、未知结果对账和补偿。
- `support-ticket-lifecycle`: 工单查询/建单/更新、升级包、客服接管与外部同步。
- `knowledge-source-ingestion`: 外部来源、解析预览、版本审核、增量同步和受控自动更新。
- `enterprise-authentication-hardening`: 登录预填移除、会话安全与企业身份接入决策及受控实现。
- `governed-long-term-memory`: 按需记忆提取、确认、ACL、召回、纠错和删除。
- `evaluated-model-routing`: 固定版本路由策略、能力/数据约束、失败切换与质量成本门禁。
- `evaluated-specialist-orchestration`: 按评测启动的专业模块/子 Agent、上下文隔离和有界协作。
- `agent-release-controls`: 版本清单、评测门禁、灰度激活、回滚及历史会话兼容。
- `support-quality-evaluation`: 版本化场景集、离线评测、故障与安全门禁、条件在线反馈。
- `production-operations-and-retention`: 凭据隔离、部署、备份恢复、留存擦除、运维和条件高可用。

### Modified Capabilities

- `runtime-observability-completion`: 明确本轮/尝试统计、客户端首文本边界、恢复/审批/对账观测与无秘密导出。
- `agent-health-computation`: 服务/Run 租约分层、有等待状态的活跃统计、固定证据窗口与 stale/unknown 解释。
- `dashboard-data-contract`: 增加统一筛选与下钻、等待/恢复/工具动作事实和累计指标的明确口径，保留原隔离契约。

## Impact

- 范围来源：根 `AGENTS.md` 的 M01–M12、差距表与验证边界；已归档产品/风险/评测基线；`add-knowledge-lifecycle` 和 `add-system-agent-dashboard` 的未勾选任务。
- 后续影响：`apps/api`、`apps/agent_worker`、`apps/web_chat`、`packages/support_core`、迁移、测试与部署；不因总规划默认引入新微服务、向量数据库、消息中间件或工作流引擎。
- 依赖沿用 uv/pnpm。模型计量、MCP、企业 IdP、工单/CMS、Vault/KMS 等选型先验证既有依赖与 SDK；没有生产凭据或业务契约时先完成隔离替身和契约测试。
- 原 change 保持各自验收所有权；新规划通过引用与差距补齐承接，不重复宣称已勾选功能完成。本规划 artifact 完备只表示可开始按阶段实施，不表示 P0/P1/P2 产品已交付。
