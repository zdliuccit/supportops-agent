## MODIFIED Requirements

### Requirement: Runtime SHALL persist complete execution observations
Worker execution SHALL record model, tool, retrieval and terminal observations with trace/span/parent relationships, phase timestamps, usage, retries, finish reason and provider request identifiers when available. Observations SHALL identify Run, attempt and actual model/tool/policy versions, separate approval/recovery/reconciliation from active execution, and derive per-Run usage from current invocation facts rather than accumulated checkpoint messages. First provider output and first client-visible text SHALL be separate metrics with documented boundaries.

#### Scenario: Successful model and tool run
- **WHEN** a run invokes a model and one or more tools
- **THEN** the run stores ordered model/tool observations and a terminal summary linked to the same trace

#### Scenario: Missing provider usage
- **WHEN** the provider omits token or cost usage
- **THEN** the system stores the metric as unavailable with its source marked unknown and never fabricates zero

#### Scenario: 多轮历史不重复计费
- **WHEN** 同一 checkpoint 已包含前几轮模型消息，本轮又产生新调用
- **THEN** 本轮只汇总本轮各 attempt 的真实新增调用，旧消息不会重复计入 Token、次数或费用

#### Scenario: 审批与恢复时间区分
- **WHEN** Run 经历长时间等待审批、恢复尝试或业务对账
- **THEN** Trace 保留各阶段与尝试，等待时间和真正执行时间分开，终态可追溯到业务回执

#### Scenario: 客户端首次正文
- **WHEN** 模型已产出增量但 SSE 传输使客户端较晚收到正文
- **THEN** 分别记录模型首输出和客户端首文本的测量来源，客户端观测不决定业务完成

### Requirement: Observation data SHALL be isolated and sanitized
Observation persistence failure SHALL NOT change the original run terminal state, and metadata, exception text and logs SHALL exclude secrets and fields outside the allowlist. Tool decisions, approval and action outcomes SHALL produce durable minimal audit facts without hidden reasoning or complete sensitive bodies. Telemetry export SHALL retain tenant scope, redaction and convention version, and SHALL NOT replace the business audit source of truth.

#### Scenario: Observation write failure
- **WHEN** an observation insert fails after a run has completed
- **THEN** the run remains completed or failed as originally determined and the persistence failure is logged with correlation context

#### Scenario: Sensitive metadata
- **WHEN** an observation contains an API key, authorization header or secret-like value
- **THEN** the stored and logged value is redacted

#### Scenario: 导出 Trace
- **WHEN** 管理员启用 OpenTelemetry 或评测 Trace 导出
- **THEN** 只导出批准字段和租户范围，不泄露凭据、隐藏推理或未经允许的正文，数据库审计事实保留
