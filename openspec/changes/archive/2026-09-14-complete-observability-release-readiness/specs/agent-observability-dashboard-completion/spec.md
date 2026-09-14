## ADDED Requirements

### Requirement: Agent Dashboard SHALL explain runtime quality
The Agent Dashboard SHALL display KPI availability, call/session/error/token/cost/latency trends, paginated calls, error aggregation and a trace drawer using the shared metric contract.

#### Scenario: Agent with complete observations
- **WHEN** an Agent has successful, failed and tool-enabled runs
- **THEN** the Dashboard displays their measured counts, trends, costs and trace stages without client-side metric drift

#### Scenario: Missing or failed data
- **WHEN** a metric is unavailable or an endpoint fails
- **THEN** the page distinguishes unavailable, empty and error states and provides a retry path
