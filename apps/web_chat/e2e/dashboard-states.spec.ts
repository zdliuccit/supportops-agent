import { expect, test, type Page } from "@playwright/test";
import { analyticsAgentId, deferredAnalytics, installAnalyticsApi } from "./helpers/analytics";

const range = "?start=2026-10-23T00%3A00%3A00Z&end=2026-10-24T00%3A00%3A00Z";
const pages = [
  { path: "/analytics/agents", heading: "Agent 状态", loading: "正在加载 Agent 状态…", empty: "暂无 Agent 状态数据" },
  { path: "/analytics/runs", heading: "调用记录", loading: "正在加载调用记录…", empty: "当前时间范围暂无调用记录" },
  { path: "/analytics/errors", heading: "错误分析", loading: "正在加载错误事件…", empty: "当前时间范围暂无错误事件" },
  { path: "/analytics/rankings", heading: "Agent 指标排行", loading: "正在加载调用排行…", empty: "当前范围暂无已知指标；缺失值保留未知" },
];

async function fitsViewport(page: Page) {
  const dimensions = await page.evaluate(() => ({ width: window.innerWidth, scroll: document.documentElement.scrollWidth,
    overflowing: Array.from(document.querySelectorAll("main div")).filter((element) => element.getBoundingClientRect().right > innerWidth + 1).slice(0, 8).map((element) => ({ class: element.className, width: element.getBoundingClientRect().width })) }));
  expect(dimensions.scroll, JSON.stringify(dimensions)).toBeLessThanOrEqual(dimensions.width + 1);
}

for (const width of [320, 390, 1280]) {
  test(`总览 ${width}px：主题、14项指标与图表自然排布`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const api = await installAnalyticsApi(page);
    await page.goto("/analytics/dashboard" + range);
    await expect(page.getByRole("heading", { name: "运营总览" })).toBeVisible();
    await expect(page.getByRole("status", { name: "正在读取分析数据" })).toHaveCount(0);
    const first = page.getByText("用户总数", { exact: true });
    await expect(first).toBeVisible();
    await expect(page.getByText("共享通知队列积压", { exact: true })).toBeVisible();
    await expect(page.getByText("Agent 健康分布", { exact: true })).toBeVisible();
    await expect(page.getByRole("group")).toHaveCount(14);
    const card = page.getByRole("group", { name: "用户总数", exact: true });
    await expect(card).toContainText("12");
    expect(await card.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe("rgb(212, 248, 228)");
    await fitsViewport(page);
    await page.screenshot({ path: test.info().outputPath(`overview-${width}.png`), fullPage: true });
    expect(api.unexpected).toEqual([]);
  });

  test(`空租户 ${width}px：零统计和明确空态`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const api = await installAnalyticsApi(page);
    Object.values(api.summary.metrics).forEach((metric) => { metric.value = 0; metric.available = true; });
    Object.keys(api.summary.status_counts).forEach((key) => { api.summary.status_counts[key] = 0; });
    api.runs = []; api.statuses = []; api.points = [];
    await page.goto("/analytics/dashboard" + range);
    await expect(page.getByText("当前时间范围暂无趋势数据")).toHaveCount(3);
    await expect(page.getByText("当前范围暂无 Agent", { exact: true })).toBeVisible();
    await fitsViewport(page);
    if (width === 320) await page.screenshot({ path: test.info().outputPath("empty-320.png"), fullPage: true });
  });
}

test("概览加载使用骨架并冻结刷新，完成后恢复", async ({ page }) => {
  const api = await installAnalyticsApi(page), gate = deferredAnalytics();
  api.before = async (_route, url) => { if (url.pathname.endsWith("/summary")) await gate.promise; return false; };
  await page.goto("/analytics/dashboard" + range);
  await expect(page.getByRole("status", { name: "正在读取分析数据" })).toBeVisible();
  await expect(page.getByRole("button", { name: "刷新列表" })).toBeDisabled();
  expect(await page.locator(".animate-pulse").count()).toBeGreaterThan(0);
  await page.screenshot({ path: test.info().outputPath("overview-loading.png"), fullPage: true });
  gate.release();
  await expect(page.getByText("用户总数", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "刷新列表" })).toBeEnabled();
});

for (const state of ["unavailable", "degraded", "unknown"]) {
  test(`服务 ${state} 有持续文字说明，不把所有 Agent 当离线`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 900 });
    const api = await installAnalyticsApi(page);
    api.summary.service.status = state;
    await page.goto("/analytics/dashboard" + range);
    const notice = page.getByRole("status", { name: "执行服务状态" });
    await expect(notice).toContainText(state === "unavailable" ? "不可用" : state === "degraded" ? "仍存活" : "待确认");
    await expect(page.getByText("用户总数", { exact: true })).toBeVisible();
    await expect(page.getByText("全部 Agent 离线", { exact: true })).toHaveCount(0);
    await fitsViewport(page);
    if (state === "unavailable") await page.screenshot({ path: test.info().outputPath("service-unavailable-320.png"), fullPage: true });
  });
}

for (const failure of [403, 503, "network"] as const) {
  test(`概览 ${failure} 错误保留重试，清理旧指标`, async ({ page }) => {
    const api = await installAnalyticsApi(page);
    await page.goto("/analytics/dashboard" + range);
    await expect(page.getByText("用户总数", { exact: true })).toBeVisible();
    api.before = async (route, url) => {
      if (!url.pathname.endsWith("/summary")) return false;
      if (failure === "network") await route.abort();
      else await route.fulfill({ status: failure, json: { error: { message: failure === 403 ? "没有分析权限" : "统计服务暂不可用" } } });
      return true;
    };
    await page.getByRole("button", { name: "刷新列表" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByText("用户总数", { exact: true })).toHaveCount(0);
    api.before = null;
    await page.getByRole("button", { name: "重试", exact: true }).click();
    await expect(page.getByText("用户总数", { exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

test("缺失指标与已知零值分开，显示样本缺口", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const api = await installAnalyticsApi(page);
  for (const key of ["input_tokens", "output_tokens", "total_cost_microusd", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms"]) {
    api.summary.metrics[key] = { value: null, available: false, sample_count: 2, missing_count: 1 };
  }
  api.summary.metrics.error_count = { value: 0, available: true };
  await page.goto("/analytics/dashboard" + range);
  const metric = (name: string) => page.getByRole("group", { name, exact: true });
  await expect(metric("输入 Token")).toContainText("—");
  await expect(metric("输入 Token")).toContainText("已知样本 2 · 缺失 1");
  await expect(metric("估算成本")).not.toContainText("$0.000000");
  await expect(metric("错误数")).toContainText("0");
  await expect(metric("错误数")).not.toContainText("—");
  await fitsViewport(page);
  await page.screenshot({ path: test.info().outputPath("missing-320.png"), fullPage: true });
});

for (const item of pages) {
  test(`${item.heading}：局部加载遮罩，完成后可刷新`, async ({ page }) => {
    const api = await installAnalyticsApi(page), gate = deferredAnalytics();
    api.before = async (_route, url) => { if (url.pathname.includes("/dashboard/") || url.pathname.includes("/analytics/errors")) await gate.promise; return false; };
    await page.goto(item.path + range);
    await expect(page.getByRole("status").filter({ hasText: item.loading })).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新列表" })).toBeDisabled();
    await expect(page.locator(".animate-pulse")).toHaveCount(0);
    gate.release();
    await expect(page.getByRole("button", { name: "刷新列表" })).toBeEnabled();
  });

  test(`${item.heading}：空态不会以空白图表或旧数据代替`, async ({ page }) => {
    const api = await installAnalyticsApi(page);
    api.runs = []; api.errors = []; api.statuses = [];
    Object.keys(api.rankings).forEach((key) => { api.rankings[key as keyof typeof api.rankings] = []; });
    await page.goto(item.path + range);
    await expect(page.getByText(item.empty, { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新列表" })).toBeEnabled();
  });

  test(`${item.heading}：503持续错误和键盘重试`, async ({ page }) => {
    const api = await installAnalyticsApi(page);
    api.before = async (route, url) => {
      if (!url.pathname.includes("/dashboard/") && !url.pathname.includes("/analytics/errors")) return false;
      await route.fulfill({ status: 503, json: { error: { message: "分析服务暂不可用" } } });
      return true;
    };
    await page.goto(item.path + range);
    await expect(page.getByRole("alert")).toContainText("分析服务暂不可用");
    const retry = page.getByRole("button", { name: "重试", exact: true });
    await retry.focus(); api.before = null; await retry.press("Enter");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "刷新列表" })).toBeEnabled();
  });
}

for (const width of [320, 1280]) {
  test(`列表 ${width}px：长标识与表格仅在内容区域滚动`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const api = await installAnalyticsApi(page);
    api.statuses[0].name = "AgentLongName".repeat(30);
    api.statuses[0].health_reason = "ReasonLongWithoutSpaces".repeat(30);
    api.runs[0].agent_name = api.statuses[0].name;
    api.errors[0].error_code = "LONG_ERROR".repeat(30);
    for (const item of pages) {
      await page.goto(item.path + range);
      await expect(page.getByRole("button", { name: "刷新列表" })).toBeEnabled();
      await fitsViewport(page);
    }
    await page.screenshot({ path: test.info().outputPath(`lists-${width}.png`), fullPage: true });
  });
}

test("Agent固定范围同样显示缺失和概览加载状态", async ({ page }) => {
  const api = await installAnalyticsApi(page);
  api.summary.metrics.success_rate = { value: null, available: false };
  await page.goto(`/analytics/agents/${analyticsAgentId}` + range);
  await expect(page.getByRole("heading", { name: "客服 Agent 运行分析" })).toBeVisible();
  await expect(page.getByText("当前周期成功率")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "全部 Agent" })).toHaveCount(0);
  expect(api.calls.filter((url) => url.pathname.endsWith("/summary")).every((url) => url.searchParams.get("agent_id") === analyticsAgentId)).toBe(true);
});

test("320px长错误仍完整显示且重试可操作", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const api = await installAnalyticsApi(page);
  const message = "ANALYTICS_UNAVAILABLE_".repeat(25);
  api.before = async (route, url) => {
    if (!url.pathname.endsWith("/summary")) return false;
    await route.fulfill({ status: 503, json: { error: { message } } });
    return true;
  };
  await page.goto("/analytics/dashboard" + range);
  await expect(page.getByRole("alert")).toContainText(message);
  await fitsViewport(page);
  await page.screenshot({ path: test.info().outputPath("error-320.png"), fullPage: true });
  await expect(page.getByRole("button", { name: "重试", exact: true })).toBeEnabled();
});

test("未知排行保留文字入口，图表不绘制虚构零值", async ({ page }) => {
  const api = await installAnalyticsApi(page);
  api.rankings.tokens[0].value = null;
  await page.goto("/analytics/rankings" + range + "&metric=tokens");
  await expect(page.getByRole("link", { name: "客服 Agent · 未知：查看调用" })).toBeVisible();
  await expect(page.getByText("当前范围暂无已知指标；缺失值保留未知", { exact: true })).toBeVisible();
  await expect(page.locator(".recharts-bar-rectangle")).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("unknown-ranking.png"), fullPage: true });
});
