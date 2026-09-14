# release-readiness-validation Specification

## Purpose
TBD - created by archiving change complete-observability-release-readiness. Update Purpose after archive.
## Requirements
### Requirement: Release validation SHALL cover browser and real database paths
The project SHALL provide repeatable browser E2E coverage for Analytics navigation, filters, pagination, ranking, error detail and trace flows, plus explicit real PostgreSQL migration, backfill, constraint and checkpointer tests.

#### Scenario: Browser critical path
- **WHEN** E2E runs with local administrator and employee fixtures
- **THEN** it verifies protected navigation, loading/empty/error states, scoped filtering and detail drill-down without fixed sleeps

#### Scenario: PostgreSQL gate
- **WHEN** `SUPPORTOPS_RUN_POSTGRES_TESTS=1` is set against the local test database
- **THEN** migrations, historical data compatibility, constraints and checkpointer recovery tests execute and report failures clearly

### Requirement: Release validation SHALL include visual and performance gates
Desktop and narrow-screen Analytics views SHALL be checked for layout, loading, empty, error and long-content states; build output and route loading SHALL be measured before release.

#### Scenario: Responsive visual review
- **WHEN** the Analytics pages are reviewed at desktop and narrow viewport sizes
- **THEN** cards, tables, charts, drawers and filters remain usable without unintended overflow or visual overlap

#### Scenario: Workspace release check
- **WHEN** the release checklist is run
- **THEN** type-check, build, lint/test results, OpenSpec status and Git working-tree state are recorded, with temporary files and staged conflicts resolved
