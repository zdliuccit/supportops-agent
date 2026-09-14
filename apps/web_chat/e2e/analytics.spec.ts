import { expect, test, type Page } from "@playwright/test";

const identity = {
  id: "00000000-0000-0000-0000-000000000001",
  tenant_id: "00000000-0000-0000-0000-000000000002",
  email: "admin@example.test",
  display_name: "测试管理员",
  roles: ["platform_admin"],
  organization_unit_id: null,
  job_title: "平台管理员",
};

const summary = {
  window_start: "2026-09-13T00:00:00Z",
  window_end: "2026-09-14T00:00:00Z",
  metrics: {
    total_users: { value: 12, available: true },
    active_users: { value: 8, available: true },
    active_users_5m: { value: 2, available: true },
    conversations: { value: 24, available: true },
    agent_runs: { value: 42, available: true },
    success_rate: { value: 97.6, available: true },
    error_count: { value: 1, available: true },
    input_tokens: { value: 1000, available: true },
    output_tokens: { value: 800, available: true },
    total_cost_microusd: { value: 1200, available: true },
    p95_latency_ms: { value: 840, available: true },
  },
  status_counts: { total: 3, running: 2, active: 3, draft: 0, disabled: 0 },
  service: { status: "healthy", instances: 1, available_instances: 1 },
};

const run = {
  id: "00000000-0000-0000-0000-000000000010",
  agent_id: "00000000-0000-0000-0000-000000000011",
  agent_name: "客服 Agent",
  user_name: "测试员工",
  organization_unit_id: null,
  department_name: null,
  conversation_id: "00000000-0000-0000-0000-000000000012",
  status: "completed",
  error_code: null,
  created_at: "2026-09-14T00:00:00Z",
  started_at: "2026-09-14T00:00:01Z",
  finished_at: "2026-09-14T00:00:02Z",
  input_tokens: 10,
  output_tokens: 8,
  total_cost_microusd: 12,
  end_to_end_latency_ms: 1000,
  correlation_id: "e2e-correlation",
};

async function mockAnalyticsApi(page: Page) {
  await page.route("**/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const body = url.pathname.endsWith("/auth/me")
      ? identity
      : url.pathname.endsWith("/summary")
        ? summary
        : url.pathname.endsWith("/timeseries")
          ? { interval: "hour", items: [{ bucket_start: "2026-09-14T00:00:00Z", values: { agent_runs: 4, output_tokens: 100 } }] }
          : url.pathname.endsWith("/agents/status")
            ? { items: [], total: 0, page: 1, page_size: 10, pages: 0 }
            : url.pathname.endsWith("/rankings")
              ? { rankings: { agent_runs: [], tokens: [], cost_microusd: [], errors: [] } }
      : url.pathname.endsWith("/runs")
                ? { items: [run], total: 1, page: 1, page_size: 10, pages: 1 }
                : url.pathname.endsWith("/trace")
                  ? { run, observations: [{ id: "00000000-0000-0000-0000-000000000013", kind: "llm", name: "model.generate", status: "completed", started_at: run.started_at, finished_at: run.finished_at, duration_ms: 1000, input_tokens: 10, output_tokens: 8, total_cost_microusd: 12, error_code: null, metadata: {} }] }
                : url.pathname.endsWith("/errors")
                  ? { items: [], total: 0, page: 1, page_size: 10, pages: 0 }
                  : url.pathname.endsWith("/organization-units")
                    ? { items: [] }
                    : {};
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

test.describe("Analytics", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((token) => localStorage.setItem("supportops-access-token-v4", token), "e2e-token");
    await mockAnalyticsApi(page);
  });

  test("导航、旧路由跳转与 Dashboard 关键指标", async ({ page }) => {
    await page.goto("/analytics/dashboard");
    await expect(page.getByRole("heading", { name: "运营总览" })).toBeVisible();
    await expect(page.getByText("42").first()).toBeVisible();

    await page.getByRole("link", { name: "Agent 调用排行" }).click();
    await expect(page).toHaveURL(/\/analytics\/rankings$/);
    await expect(page.getByRole("heading", { name: "Agent 调用排行" })).toBeVisible();

    await page.goto("/agent-management/dashboard");
    await expect(page).toHaveURL(/\/analytics\/dashboard$/);
  });

  test("列表页展示数据、链路详情和刷新入口", async ({ page }) => {
    await page.goto("/analytics/runs");
    await expect(page.getByRole("heading", { name: "调用记录" })).toBeVisible();
    await expect(page.getByText("测试员工")).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新列表" })).toBeVisible();
    await page.getByRole("button", { name: "查看链路" }).click();
    await expect(page.getByText("运行链路")).toBeVisible();

    await page.goto("/analytics/errors");
    await expect(page.getByRole("heading", { name: "错误分析" })).toBeVisible();
    await expect(page.getByText("当前时间范围暂无错误事件")).toBeVisible();
  });

  test("窄屏仍保留页面标题和核心指标", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/analytics/dashboard");
    await expect(page.getByRole("heading", { name: "运营总览" })).toBeVisible();
    await expect(page.getByText("42").first()).toBeVisible();
  });
});
