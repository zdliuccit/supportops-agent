## ADDED Requirements

### Requirement: System Dashboard SHALL summarize all tenant Agents
The system Dashboard SHALL show Agent inventory, execution-service state, lifecycle/execution/health distribution, aggregate usage metrics, rankings, status pagination and cross-Agent records.

#### Scenario: Mixed Agent fleet
- **WHEN** a tenant has active, stopped, degraded and pending-publish Agents
- **THEN** the Dashboard separates those states, shows their reasons and keeps rankings and aggregate metrics consistent

#### Scenario: Empty or unavailable fleet
- **WHEN** no Agent or no health snapshot is available
- **THEN** the page shows an explicit empty/unknown state and does not represent missing values as healthy or zero usage

### Requirement: System Dashboard SHALL support scoped drill-down
Ranking, chart and status interactions SHALL navigate to records, errors or Agent Dashboard views while preserving tenant, Agent, time and status scope.

#### Scenario: Ranking drill-down
- **WHEN** an administrator selects an Agent from the call ranking
- **THEN** the destination shows that Agent's runtime analytics for the same selected time window
