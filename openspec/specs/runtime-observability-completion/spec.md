# runtime-observability-completion Specification

## Purpose
TBD - created by archiving change complete-observability-release-readiness. Update Purpose after archive.
## Requirements
### Requirement: Runtime SHALL persist complete execution observations
Worker execution SHALL record model, tool, retrieval and terminal observations with trace/span/parent relationships, phase timestamps, usage, retries, finish reason and provider request identifiers when available.

#### Scenario: Successful model and tool run
- **WHEN** a run invokes a model and one or more tools
- **THEN** the run stores ordered model/tool observations and a terminal summary linked to the same trace

#### Scenario: Missing provider usage
- **WHEN** the provider omits token or cost usage
- **THEN** the system stores the metric as unavailable with its source marked unknown and never fabricates zero

### Requirement: Observation data SHALL be isolated and sanitized
Observation persistence failure SHALL NOT change the original run terminal state, and metadata, exception text and logs SHALL exclude secrets and fields outside the allowlist.

#### Scenario: Observation write failure
- **WHEN** an observation insert fails after a run has completed
- **THEN** the run remains completed or failed as originally determined and the persistence failure is logged with correlation context

#### Scenario: Sensitive metadata
- **WHEN** an observation contains an API key, authorization header or secret-like value
- **THEN** the stored and logged value is redacted
