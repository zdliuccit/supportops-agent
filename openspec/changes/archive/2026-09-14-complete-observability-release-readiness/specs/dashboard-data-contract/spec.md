## ADDED Requirements

### Requirement: Dashboard endpoints SHALL share one metric scope contract
Summary, timeseries, rankings, runs, errors and trace responses SHALL apply the same tenant, time-window and optional Agent scope rules for system and Agent views.

#### Scenario: System scope
- **WHEN** an administrator requests a system Dashboard without an Agent filter
- **THEN** every aggregate and list response covers only the current tenant and the selected time window across all Agents

#### Scenario: Agent scope
- **WHEN** an administrator requests an Agent Dashboard with an Agent ID
- **THEN** aggregates, trends, records and errors are constrained to that Agent and reject cross-tenant IDs

### Requirement: Dashboard list queries SHALL expose stable filters and pagination
Runs and errors SHALL support stable ordering, page boundaries and the documented Agent, user/department, status, severity and time filters; unavailable metrics SHALL remain distinguishable from zero.

#### Scenario: Filter and page combination
- **WHEN** an administrator changes an Agent filter or page size
- **THEN** the API returns the matching page, total count and stable ordering without leaking records outside the tenant

#### Scenario: Drill-down
- **WHEN** a user clicks a chart bucket, ranking item or error row
- **THEN** the destination list or trace view preserves the originating time and Agent scope
