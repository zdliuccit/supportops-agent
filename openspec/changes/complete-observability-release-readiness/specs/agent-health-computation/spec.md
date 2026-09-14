## ADDED Requirements

### Requirement: Agent health SHALL be computed from a fixed evidence window
The health coordinator SHALL combine lifecycle state, queued/running runs, recent terminal runs, active version/config digest and execution-service lease into a persisted health snapshot with an explanation and observation timestamps.

#### Scenario: Healthy active Agent
- **WHEN** an active Agent has recent successful runs and a valid execution-service lease
- **THEN** its snapshot reports healthy and includes the latest run facts

#### Scenario: Pending publish
- **WHEN** the current configuration digest differs from the active published version
- **THEN** the snapshot sets `pending_publish` and explains that republish and restart are required

#### Scenario: Stale execution service
- **WHEN** the execution-service lease is expired or missing beyond the tolerance window
- **THEN** the snapshot reports unavailable, stale or unknown according to the defined lease rule and exposes the reason

### Requirement: Health statistics SHALL use minimum-sample rules
Error rate, latency percentiles and active-run counts SHALL use documented windows and minimum sample thresholds so a single failure cannot incorrectly downgrade a healthy Agent.

#### Scenario: Insufficient samples
- **WHEN** the window has fewer than the minimum terminal runs
- **THEN** error rate and percentile metrics are unavailable and the health reason states that evidence is insufficient
