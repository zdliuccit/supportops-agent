## MODIFIED Requirements

### Requirement: Agent health SHALL be computed from a fixed evidence window
The health coordinator SHALL combine lifecycle state, queued/running runs, recent terminal runs, active version/config digest and execution-service lease into a persisted health snapshot with an explanation and observation timestamps. Service lease, Run attempt lease and Agent health SHALL remain distinct; long-running work SHALL not suppress service heartbeats. Waiting-approval/reconciliation and stale attempts SHALL have explicit status semantics and SHALL not be counted as actively executing model calls. A shared Worker lease SHALL not fabricate a per-Agent process lease.

#### Scenario: Healthy active Agent
- **WHEN** an active Agent has recent successful runs and a valid execution-service lease
- **THEN** its snapshot reports healthy and includes the latest run facts

#### Scenario: Pending publish
- **WHEN** the current configuration digest differs from the active published version
- **THEN** the snapshot sets `pending_publish` and explains that republish and restart are required

#### Scenario: Stale execution service
- **WHEN** the execution-service lease is expired or missing beyond the tolerance window
- **THEN** the snapshot reports unavailable, stale or unknown according to the defined lease rule and exposes the reason

#### Scenario: 长任务与审批等待
- **WHEN** 模型调用超过原 15 秒租约或 Run 正在等待审批
- **THEN** 服务心跳独立保持有效；等待与执行分开展示，不将合法长任务或等待误判为离线

#### Scenario: 执行尝试过期
- **WHEN** 服务仍存活但某一 Run attempt 租约已过期
- **THEN** 标记具体尝试需要恢复，不把全服务状态直接推断为该 Run 健康

### Requirement: Health statistics SHALL use minimum-sample rules
Error rate, latency percentiles and active-run counts SHALL use documented windows and minimum sample thresholds so a single failure cannot incorrectly downgrade a healthy Agent. Runtime attempts SHALL not duplicate business Run counts; approval waits, cancellations, provider outages and unknown metrics SHALL have documented inclusion rules and unavailable-state explanations. Retention, clock tolerance, stale limits and aggregate-table activation thresholds SHALL be documented.

#### Scenario: Insufficient samples
- **WHEN** the window has fewer than the minimum terminal runs
- **THEN** error rate and percentile metrics are unavailable and the health reason states that evidence is insufficient

#### Scenario: 多 Worker 恢复同一 Run
- **WHEN** 多个执行尝试记录关联同一业务 Run
- **THEN** 活跃/完成 Run 指标按业务 Run 去重，尝试和恢复次数单独统计

#### Scenario: 当前执行跨越历史查询窗口
- **WHEN** 已筛选Agent集合中存在历史窗口外仍排队、持有效当前许可执行或取消中的Run
- **THEN** 状态表及总览当前运行库存仍计入该Run，历史调用卡片继续使用原时间窗口；多个Run按Agent去重，不因额外未采纳attempt增加计数，等待审批/动作、过期/错配许可与通知死信不计入活跃执行
