import type { Page, Route } from "@playwright/test";
import type { DashboardErrorEvent, DashboardRankings, DashboardRun, DashboardSummary, DashboardTimeseriesPoint, SystemAgentStatus } from "../../src/types";

export const analyticsTime = "2026-10-24T00:00:00Z";
export const analyticsAgentId = "00000000-0000-0000-0000-000000000011";
export const analyticsRun: DashboardRun = {
  id: "00000000-0000-0000-0000-000000000010", agent_id: analyticsAgentId,
  agent_name: "客服 Agent", user_name: "测试员工", organization_unit_id: null, department_name: null,
  conversation_id: "00000000-0000-0000-0000-000000000012", status: "completed", error_code: null,
  created_at: analyticsTime, started_at: analyticsTime, finished_at: analyticsTime,
  input_tokens: 10, output_tokens: 8, total_cost_microusd: 12, end_to_end_latency_ms: 1000,
  correlation_id: "analytics-isolated-test",
};

export function analyticsSummary(): DashboardSummary {
  const values: Record<string, number | null> = {
    total_users: 12, active_users: 8, active_users_5m: 2, conversations: 24, agent_runs: 42,
    success_rate: 97.6, error_count: 1, input_tokens: 1000, output_tokens: 800,
    total_cost_microusd: 1200, p50_latency_ms: 200, p95_latency_ms: 840, p99_latency_ms: 900,
    queue_depth: 0,
  };
  return {
    window_start: "2026-10-23T00:00:00Z", window_end: analyticsTime,
    metrics: Object.fromEntries(Object.entries(values).map(([key, value]) => [key, {
      value, available: value !== null, source: "isolated_fixture", sample_count: 3, missing_count: 0,
    }])),
    status_counts: { total: 3, running: 2, active: 3, draft: 0, disabled: 0,
      health_healthy: 1, health_degraded: 1, health_unknown: 1, health_no_recent_activity: 0 },
    service: { status: "healthy", instances: 1, available_instances: 1 },
  };
}

export function analyticsStatus(): SystemAgentStatus {
  return {
    id: analyticsAgentId, name: "客服 Agent", lifecycle_status: "active", execution_status: "idle",
    health_status: "unknown", health_reason: "健康快照尚未生成，请检查协调任务",
    last_run_at: null, last_success_at: null, last_failure_at: null, active_run_count: 0,
    error_rate: null, p95_latency_ms: null, pending_publish: false,
    observed_at: null, computed_at: null, health_stale: true,
  };
}

export const analyticsErrors: DashboardErrorEvent[] = [
  { id: "00000000-0000-0000-0000-000000000020", occurred_at: analyticsTime,
    severity: "error", error_code: "MODEL_TIMEOUT", reason: "模型服务响应超时", stage: "model.generate",
    resolution_status: "unresolved", agent_id: analyticsAgentId, agent_name: "客服 Agent",
    user_id: "00000000-0000-0000-0000-000000000001", user_name: "测试管理员",
    conversation_id: analyticsRun.conversation_id, run_id: analyticsRun.id,
    correlation_id: analyticsRun.correlation_id, agent_version_number: 3, model_name: "gpt-test",
    retry_count: 1, latency_ms: 1200 },
  { id: "00000000-0000-0000-0000-000000000021", occurred_at: analyticsTime,
    severity: "warning", error_code: "RETRY_EXHAUSTED", reason: "重试次数已耗尽", stage: "tool.call",
    resolution_status: "resolved", agent_id: analyticsAgentId, agent_name: "客服 Agent",
    user_id: "00000000-0000-0000-0000-000000000001", user_name: "测试管理员",
    conversation_id: analyticsRun.conversation_id, run_id: analyticsRun.id,
    correlation_id: analyticsRun.correlation_id, agent_version_number: 3, model_name: "gpt-test",
    retry_count: 3, latency_ms: 2400 },
];

export function deferredAnalytics() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

/** 只验收浏览器展示/请求契约；不代表真实后端、Worker、模型或外部系统验收。 */
export async function installAnalyticsApi(page: Page) {
  await page.addInitScript(() => {
    const claims = { jti: "analytics-test", sub: "test-admin", tenant_id: "test-tenant", exp: Math.floor(Date.now() / 1000) + 3600 };
    sessionStorage.setItem("supportops-auth-session-v1", `test.${btoa(JSON.stringify(claims))}.test`);
  });
  const rankings: DashboardRankings["rankings"] = {
    agent_runs: [], active_users: [], failure_rate: [], tokens: [], cost_microusd: [], errors: [],
    p95_latency_ms: [], tool_calls: [],
  };
  for (const key of Object.keys(rankings) as (keyof typeof rankings)[]) {
    rankings[key] = [{ agent_id: analyticsAgentId, agent_name: "客服 Agent", value: 42 }];
  }
  const state = {
    summary: analyticsSummary(), runs: [{ ...analyticsRun }], statuses: [analyticsStatus()],
    errors: analyticsErrors.map((item) => ({ ...item })), rankings,
    points: [{ bucket_start: "2026-10-23T00:00:00Z", bucket_end: analyticsTime,
      values: { agent_runs: 42, output_tokens: 800, error_count: 1 } }] as DashboardTimeseriesPoint[],
    calls: [] as URL[], unexpected: [] as string[],
    before: null as null | ((route: Route, url: URL) => Promise<boolean>),
  };
  await page.route("**/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    state.calls.push(url);
    if (state.before && await state.before(route, url)) return;
    const current = Number(url.searchParams.get("page") ?? 1);
    const size = Number(url.searchParams.get("page_size") ?? 20);
    const paged = (items: unknown[]) => ({ items: items.slice((current - 1) * size, current * size),
      total: items.length, page: current, page_size: size, pages: Math.ceil(items.length / size) });
    let body: unknown;
    if (path === "/v1/auth/me") body = {
      user_id: "test-admin", tenant_id: "test-tenant", email: "admin@example.test", display_name: "测试管理员",
      roles: ["platform_admin"], organization_unit_id: null, job_title: "平台管理员",
      company_name: "隔离验收企业", company_logo_url: null,
    };
    else if (path === "/v1/admin/organization-units") body = { items: [] };
    else if (path.endsWith("/summary")) body = state.summary;
    else if (path.endsWith("/timeseries")) body = { interval: "day", items: state.points };
    else if (path.endsWith("/agents/status")) body = paged(state.statuses);
    else if (path.endsWith("/rankings")) body = { rankings: state.rankings };
    else if (path === "/v1/admin/dashboard/runs") body = paged(state.runs);
    else if (path === "/v1/admin/dashboard/errors") body = { items: [] };
    else if (path === "/v1/admin/analytics/errors") body = paged(state.errors.filter((item) =>
      (!url.searchParams.has("error_code") || item.error_code === url.searchParams.get("error_code")) &&
      (!url.searchParams.has("severity") || item.severity === url.searchParams.get("severity"))));
    else if (/\/analytics\/errors\/[^/]+$/.test(path)) body = {
      ...state.errors.find((item) => path.endsWith(item.id)), metadata: {}, observations: [],
    };
    else if (path.endsWith("/trace")) body = { run: state.runs[0], observations: [] };
    else if (path === `/v1/admin/agents/${analyticsAgentId}`) body = { id: analyticsAgentId, name: "客服 Agent" };
    else if (["/v1/admin/agents", "/v1/admin/users"].includes(path)) body = paged([]);
    else { state.unexpected.push(path); await route.abort(); return; }
    await route.fulfill({ json: body });
  });
  return state;
}
