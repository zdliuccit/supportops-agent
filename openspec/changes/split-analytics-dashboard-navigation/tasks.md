## 1. Backend error event foundation

- [x] 1.1 Add `AgentErrorEvent` model with tenant/run/conversation/agent/version/user links, severity, code, reason, stage, status, retry/latency metadata and query indexes.
- [x] 1.2 Add an Alembic migration for the event table, foreign keys and tenant/time/agent/severity indexes.
- [x] 1.3 Record a sanitized error event from the run failure path without masking the original run failure when event persistence fails.
- [x] 1.4 Add typed API schemas for paginated error events and error detail/trace payloads.

## 2. Analytics APIs

- [x] 2.1 Add tenant-scoped, admin-only paginated error event list endpoint with time, severity, code, agent, user and status filters.
- [x] 2.2 Add admin-only error event detail endpoint with joined display names, run summary, model/version and trace timeline.
- [x] 2.3 Preserve existing aggregate dashboard endpoints and correct affected-user aggregation to use distinct users where available.
- [x] 2.4 Add API tests for event creation, pagination/filter combinations, 404 behavior and cross-tenant isolation.

## 3. Analytics navigation and pages

- [x] 3.1 Add Analytics navigation group above 智能体管理 with Dashboard、Agent 调用排行、Agent 状态、错误分析 entries and admin visibility.
- [x] 3.2 Add canonical `/analytics/*` routes, breadcrumbs and compatibility redirect from `/agent-management/dashboard`.
- [x] 3.3 Split existing system dashboard UI into Dashboard、排行、状态独立页面 while retaining shared KPI/chart/table components and white page backgrounds.
- [x] 3.4 Keep Agent-specific runtime dashboard reachable from the Agent list statistics icon and add loading/empty/error states.
- [x] 3.5 Add frontend API types/hooks for error event list/detail and shared analytics filters.
- [x] 3.6 Implement error analysis table with server pagination, filters, stable columns and right-side detail drawer.

## 4. Verification and documentation

- [ ] 4.1 Add browser E2E coverage for Analytics navigation, legacy redirect, page split, error filtering/pagination and detail drawer.
- [x] 4.2 Run frontend type-check/build and backend test suite; fix regressions without changing unrelated worktree edits.
- [x] 4.3 Validate OpenSpec change and update task progress with final verification notes.
