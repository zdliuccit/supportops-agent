import { expect, test } from "@playwright/test";
import { analyticsErrors, installAnalyticsApi } from "./helpers/analytics";

test.describe("Analytics", () => {
  test.beforeEach(async ({ page }) => {
    const api = await installAnalyticsApi(page);
    api.errors = Array.from({ length: 21 }, (_, index) => ({
      ...analyticsErrors[index === 20 ? 1 : 0], id: `00000000-0000-0000-0000-${String(index + 100).padStart(12, "0")}`,
      reason: index === 0 ? "模型服务响应超时" : index === 20 ? "重试次数已耗尽" : `隔离错误 ${index}`,
      error_code: index === 0 ? "MODEL_TIMEOUT" : index === 20 ? "RETRY_EXHAUSTED" : `OTHER_${index}`,
    }));
  });

  test("导航、旧路由跳转与独立 Analytics 页面", async ({ page }) => {
    await page.goto("/analytics/dashboard");
    await expect(page.getByRole("heading", { name: "运营总览" })).toBeVisible();
    await expect(page.getByText("42").first()).toBeVisible();

    await page.getByRole("link", { name: "Agent 调用排行" }).click();
    await expect(page).toHaveURL(/\/analytics\/rankings\?/);
    await expect(page.getByRole("heading", { name: "Agent 指标排行" })).toBeVisible();

    await page.goto("/analytics/agents");
    await expect(page.getByRole("heading", { name: "Agent 状态" })).toBeVisible();

    await page.goto("/analytics/errors");
    await expect(page.getByRole("heading", { name: "错误分析" })).toBeVisible();

    await page.goto("/agent-management/dashboard");
    await expect(page).toHaveURL(/\/analytics\/dashboard\?/);
  });

  test("列表页展示数据、链路详情和刷新入口", async ({ page }) => {
    await page.goto("/analytics/runs");
    await expect(page.getByRole("heading", { name: "调用记录" })).toBeVisible();
    await expect(page.getByText("测试员工")).toBeVisible();
    await expect(page.getByRole("button", { name: "刷新列表" })).toBeVisible();
    await page.getByRole("button", { name: "查看链路" }).click();
    await expect(page.getByText("运行链路")).toBeVisible();

  });

  test("错误分析支持筛选、分页与详情抽屉", async ({ page }) => {
    await page.goto("/analytics/errors");
    await expect(page.getByText("模型服务响应超时")).toBeVisible();

    const filteredRequest = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return url.pathname.endsWith("/analytics/errors") && url.searchParams.get("error_code") === "MODEL_TIMEOUT";
    });
    await page.getByPlaceholder("按错误码筛选").fill("MODEL_TIMEOUT");
    await filteredRequest;
    await expect(page.getByRole("button", { name: "刷新列表" })).toBeEnabled();
    await page.getByPlaceholder("按错误码筛选").fill("");
    await expect(page.getByRole("button", { name: "刷新列表" })).toBeEnabled();

    await page.getByRole("button", { name: "第 2 页" }).click();
    await expect(page.getByText("重试次数已耗尽")).toBeVisible();

    await page.getByRole("button", { name: "查看详情" }).click();
    await expect(page.getByRole("heading", { name: "错误详情" })).toBeVisible();
    await expect(page.getByText("运行时间线")).toBeVisible();
    await expect(page.getByText("gpt-test")).toBeVisible();
  });

  test("窄屏仍保留页面标题和核心指标", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/analytics/dashboard");
    await expect(page.getByRole("heading", { name: "运营总览" })).toBeVisible();
    await expect(page.getByText("42").first()).toBeVisible();
  });

});
