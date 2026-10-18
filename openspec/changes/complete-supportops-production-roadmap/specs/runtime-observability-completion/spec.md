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

#### Scenario: 遥测发送结果未知
- **WHEN** 显式维护命令进入唯一发送路径后遇到超时、传输、坏回执或中断
- **THEN** 输出telemetry_export_report_v1的outcome_uncertain并退出4，send_attempted不证明已发字节，无有效回执时accepted/rejected保持null；保留原扫描定位与载荷摘要但无成功游标，不自动重发或改写业务状态

#### Scenario: 部分接收或接收警告
- **WHEN** HTTP200回执给出部分拒绝或0拒绝的警告
- **THEN** 检查非负int64及本批次范围，只输出拒绝数和警告存在性、不输出自由文本；部分接收退出2且next_cursor为空，禁止按扫描末尾跳过未确认批次或自动重发。0拒绝的有效回执可继续扫描，但不证明永久存储、迟到数据完整性或业务成功

#### Scenario: 预览和没有可导出span
- **WHEN** 命令只预览或send批次没有可导出span
- **THEN** 明确preview或not_sent，accepted/rejected保持null且receiver_response_confirmed=false，查询游标不作为HTTP投递证明

#### Scenario: ProtoJSON计数不经浮点舍入
- **WHEN** 回执拒绝数采用字符串/JSON数字的整数值指数形式或null
- **THEN** 用精确十进制接受合法非负int64，null按未设置为0；数字文本限128字符，拒绝非整数值、非有限/负数/int64或本批超限，不先用float舍入为合法拒绝数
