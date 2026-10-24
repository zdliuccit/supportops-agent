import { expect, test as base } from "@playwright/test";
import { approvalFixture, approvalLabels, deferred, installApprovalApi } from "./helpers/approvals";

const test = base.extend<{ approvals: Awaited<ReturnType<typeof installApprovalApi>> }>({
  approvals: [async ({ page }, use) => {
    await page.addInitScript(() => {
      const payload = btoa(JSON.stringify({ sub: "user-1", tenant_id: "tenant-1", jti: "ui-session", exp: Math.floor(Date.now() / 1000) + 3600 }));
      sessionStorage.setItem("supportops-auth-session-v1", `e30.${payload}.signature`);
    });
    await page.route("**/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/v1/auth/me") await route.fulfill({ json: { id: "user-1", tenant_id: "tenant-1", display_name: "审批人员", email: "approval@example.test", roles: [], organization_unit_id: null, job_title: null } });
      else if (path.endsWith("/company")) await route.fulfill({ json: { name: "隔离 UI 验收" } });
      else throw new Error(`未模拟的请求 ${path}`);
    });
    await use(await installApprovalApi(page));
  }, { auto: true }],
});

test("W16.10 待办加载、空态与服务端分页筛选", async ({ page, approvals }) => {
  approvals.items.splice(0, 1, ...Array.from({ length: 21 }, (_, index) => approvalFixture({ id: `approval-${index}`, tool_id: `mock-tool-${index}`, status: index === 20 ? "expired" : "pending" })));
  const blocked = deferred();
  await page.route(/\/v1\/approvals\?/, async (route) => { await blocked.promise; await route.fallback(); }, { times: 1 });
  try {
    await page.goto("/approvals");
    await expect(page.getByRole("status").filter({ hasText: "正在读取审批待办" })).toBeVisible();
    blocked.release();
    await expect(page.getByRole("button", { name: "查看审批", exact: true })).toHaveCount(20);
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(page.getByText("mock-tool-20", { exact: true })).toBeVisible();
    expect(approvals.calls.lists.at(-1)?.searchParams.get("page")).toBe("2");
    await page.getByRole("combobox", { name: "筛选审批状态" }).click();
    await page.getByRole("option", { name: "已过期", exact: true }).click();
    await expect(page.getByRole("button", { name: "查看审批", exact: true })).toHaveCount(1);
    expect(approvals.calls.lists.at(-1)?.searchParams.get("page")).toBe("1");
    expect(approvals.calls.lists.at(-1)?.searchParams.get("state")).toBe("expired");
    await page.getByRole("combobox", { name: "筛选审批状态" }).click();
    await page.getByRole("option", { name: "已拒绝，未执行", exact: true }).click();
    await expect(page.getByText("暂无数据", { exact: true })).toBeVisible();
    expect(approvals.calls.writes).toHaveLength(0);
  } finally { blocked.release(); }
});

for (const mobile of [false, true]) {
  test(`W16.10 ${mobile ? "320px" : "桌面"}长详情、键盘圈定与关闭焦点`, async ({ page, approvals }, testInfo) => {
    if (mobile) await page.setViewportSize({ width: 320, height: 640 });
    approvals.items[0].parameters = { schema_version: "1", title: "LongTitle".repeat(100), summary: "LongSummary".repeat(300), issue_key: "LongIssue".repeat(80) };
    await page.goto("/approvals");
    const trigger = page.getByRole("button", { name: "查看审批", exact: true });
    await trigger.focus(); await page.keyboard.press("Enter");
    const drawer = page.getByRole("dialog", { name: "独立审批详情" });
    const close = drawer.getByRole("button", { name: "关闭独立审批详情", exact: true });
    await expect(close).toBeFocused();
    await expect(drawer.getByRole("heading", { name: /^LongTitle/ })).toBeVisible();
    await page.keyboard.press("Shift+Tab");
    await expect(drawer.getByRole("button", { name: "关闭", exact: true })).toBeFocused();
    await page.keyboard.press("Tab"); await expect(close).toBeFocused();
    expect(await drawer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (mobile) await page.screenshot({ path: testInfo.outputPath("approval-mobile.png") });
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0); await expect(trigger).toBeFocused();
  });
}

for (const [status, label] of Object.entries(approvalLabels)) {
  test(`W16.10 ${status}审批与动作进度分开展示`, async ({ page, approvals }) => {
    Object.assign(approvals.items[0], { status, can_decide: status === "pending", can_cancel: false, action_status: status === "approved" || status === "consumed" ? "queued" : "cancelled", decision_reason: "已记录的脱敏理由" });
    await page.goto("/approvals"); await page.getByRole("button", { name: "查看审批", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "独立审批详情" });
    await expect(drawer.getByRole("status")).toHaveText(label);
    await expect(drawer.getByText("决定理由：已记录的脱敏理由")).toBeVisible();
    await expect(drawer.getByRole("button", { name: "批准冻结动作" })).toHaveCount(status === "pending" ? 1 : 0);
    await expect(drawer.getByText(/业务状态已独立复核成功/)).toHaveCount(0);
    expect(approvals.calls.writes).toHaveLength(0);
  });
}

for (const decision of ["approved", "rejected", "cancel"] as const) {
  test(`W16.10 ${decision}冻结版本、理由校验与单次提交`, async ({ page, approvals }) => {
    approvals.items[0].can_decide = decision !== "cancel";
    approvals.items[0].can_cancel = decision === "cancel";
    const blocked = deferred();
    await page.route(/\/v1\/approvals\/approval-1\/(decision|cancel)$/, async (route) => { await blocked.promise; await route.fallback(); });
    try {
      await page.goto("/approvals"); await page.getByRole("button", { name: "查看审批", exact: true }).click();
      const drawer = page.getByRole("dialog", { name: "独立审批详情" });
      const button = drawer.getByRole("button", { name: decision === "approved" ? "批准冻结动作" : decision === "rejected" ? "拒绝" : "取消未消费申请", exact: true });
      if (decision !== "cancel") {
        await expect(button).toBeDisabled();
        const reason = drawer.getByRole("textbox", { name: "审批理由", exact: false });
        await reason.fill("   "); await expect(reason).toHaveAttribute("aria-invalid", "true");
        await reason.fill("  经核对符合当前隔离项目范围  "); await expect(reason).not.toHaveAttribute("aria-invalid", "true");
      }
      await button.focus(); await page.keyboard.press("Enter");
      await expect(drawer.getByRole("button", { name: "关闭", exact: true })).toBeDisabled();
      await page.keyboard.press("Enter"); await page.keyboard.press("Escape"); await expect(drawer).toBeVisible();
      blocked.release();
      await expect(drawer.getByRole("status").filter({ hasText: approvalLabels[decision === "cancel" ? "cancelled" : decision] })).toBeVisible();
      expect(approvals.calls.writes).toHaveLength(1);
      expect(approvals.calls.writes[0].body).toEqual(decision === "cancel" ? { expected_revision: 4 } : { expected_revision: 4, decision, reason: "经核对符合当前隔离项目范围" });
      await drawer.getByRole("button", { name: "关闭", exact: true }).click();
      await expect.poll(() => page.evaluate(() => document.activeElement?.getAttribute("aria-label"))).toBe("审批工作台内容");
    } finally { blocked.release(); }
  });
}

for (const status of [403, 404, 503]) {
  test(`W16.10 重新读取${status}清除旧详情且可重试`, async ({ page }) => {
    await page.goto("/approvals"); await page.getByRole("button", { name: "查看审批", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "独立审批详情" });
    await expect(drawer.getByText("最小支持摘要", { exact: true })).toBeVisible();
    await page.route("**/v1/approvals/approval-1", (route) => route.fulfill({ status, json: { error: { message: `审批读取失败 ${status}` } } }), { times: 1 });
    await drawer.getByRole("button", { name: "重新读取当前状态" }).click();
    await expect(drawer.getByRole("alert")).toHaveText(`审批读取失败 ${status}`);
    await expect(drawer.getByText("最小支持摘要", { exact: true })).toHaveCount(0);
    await expect(drawer.getByRole("button", { name: "批准冻结动作" })).toHaveCount(0);
    await drawer.getByRole("button", { name: "重新读取当前状态" }).click();
    await expect(drawer.getByText("最小支持摘要", { exact: true })).toBeVisible();
  });
}

test("W16.10 关闭后旧详情响应不会污染新审批", async ({ page, approvals }) => {
  approvals.items.push(approvalFixture({ id: "approval-2", parameters: { schema_version: "1", title: "第二条审批", summary: "第二条摘要", issue_key: "second" } }));
  const blocked = deferred();
  await page.route("**/v1/approvals/approval-1", async (route) => { await blocked.promise; await route.fallback(); });
  try {
    await page.goto("/approvals"); await page.getByRole("button", { name: "查看审批", exact: true }).first().click();
    await page.getByRole("button", { name: "关闭独立审批详情" }).click();
    await page.getByRole("button", { name: "查看审批", exact: true }).last().click();
    const drawer = page.getByRole("dialog", { name: "独立审批详情" });
    await expect(drawer.getByText("第二条摘要", { exact: true })).toBeVisible();
    blocked.release();
    await expect.poll(() => approvals.calls.details.includes("approval-1")).toBe(true);
    await expect(drawer.getByText("第二条摘要", { exact: true })).toBeVisible();
    await expect(drawer.getByText("最小支持摘要", { exact: true })).toHaveCount(0);
  } finally { blocked.release(); }
});

for (const status of [403, 503]) {
  test(`W16.10 列表${status}清空旧记录并允许刷新`, async ({ page }) => {
    await page.goto("/approvals");
    await expect(page.getByRole("button", { name: "查看审批", exact: true })).toHaveCount(1);
    await page.route(/\/v1\/approvals\?/, (route) => route.fulfill({ status, json: { error: { message: "列表暂时无法访问" } } }), { times: 1 });
    await page.getByRole("button", { name: "刷新列表", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveText("列表暂时无法访问");
    await expect(page.getByRole("button", { name: "查看审批", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "刷新列表", exact: true }).click();
    await expect(page.getByRole("button", { name: "查看审批", exact: true })).toHaveCount(1);
  });
}

for (const status of [403, 409]) {
  test(`W16.10 提交${status}清除旧决定入口且重新授权后才能再操作`, async ({ page, approvals }) => {
    await page.goto("/approvals"); await page.getByRole("button", { name: "查看审批", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "独立审批详情" });
    await drawer.getByRole("textbox", { name: /审批理由/ }).fill("提交前重新核对");
    await page.route("**/v1/approvals/approval-1/decision", (route) => route.fulfill({ status, json: { error: { message: "当前授权或版本已变化" } } }));
    await drawer.getByRole("button", { name: "批准冻结动作" }).click();
    await expect(drawer.getByRole("alert")).toHaveText("当前授权或版本已变化");
    await expect(drawer.getByText("最小支持摘要", { exact: true })).toHaveCount(0);
    await expect(drawer.getByRole("button", { name: "批准冻结动作" })).toHaveCount(0);
    Object.assign(approvals.items[0], { can_decide: false, status: "invalidated" });
    await drawer.getByRole("button", { name: "重新读取当前状态" }).click();
    await expect(drawer.getByRole("status")).toHaveText("授权范围变化，批准失效");
    expect(approvals.calls.writes).toHaveLength(0);
  });
}

test("W16.10 本人或只读详情没有批准入口", async ({ page, approvals }) => {
  Object.assign(approvals.items[0], { applicant_id: "user-1", approver_id: "reviewer-2", can_decide: false, can_cancel: true });
  await page.goto("/approvals"); await page.getByRole("button", { name: "查看审批", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "独立审批详情" });
  await expect(drawer.getByRole("button", { name: "取消未消费申请" })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "批准冻结动作" })).toHaveCount(0);
  await expect(drawer.getByRole("textbox", { name: /审批理由/ })).toHaveCount(0);
  expect(approvals.calls.writes).toHaveLength(0);
});

for (const mobile of [false, true]) {
  test(`W16.10 ${mobile ? "320px" : "桌面"}管理员授权字段、禁止自审与关闭焦点`, async ({ page }, testInfo) => {
    if (mobile) await page.setViewportSize({ width: 320, height: 640 });
    await page.route("**/v1/auth/me", (route) => route.fulfill({ json: { id: "user-1", tenant_id: "tenant-1", display_name: "管理员", roles: ["platform_admin"], email: "approval@example.test", organization_unit_id: null, job_title: null } }));
    const writes: unknown[] = [];
    await page.route("**/v1/admin/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const items = path.endsWith("/users") ? [{ id: "owner-1", display_name: "项目资源主体" }, { id: "reviewer-2", display_name: "独立审批人员" }] : [];
      if (route.request().method() === "POST") writes.push(route.request().postDataJSON());
      await route.fulfill({ json: { items, total: items.length, page: 1, page_size: 20, pages: 1 } });
    });
    await page.goto("/approvals");
    const trigger = page.getByRole("button", { name: "授予权限", exact: true });
    await trigger.focus(); await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: "授予 Mock 项目权限" });
    const owner = dialog.getByRole("combobox", { name: "资源主体", exact: true });
    await owner.focus(); await page.keyboard.press("Enter");
    await page.getByRole("option", { name: "项目资源主体", exact: true }).click();
    const user = dialog.getByRole("combobox", { name: "授权人员", exact: true });
    await user.focus(); await page.keyboard.press("Enter");
    await page.getByRole("option", { name: "项目资源主体", exact: true }).click();
    await expect(user).toHaveAttribute("aria-invalid", "true");
    await expect(dialog.getByRole("alert")).toHaveText("不能授予本人自审权限。");
    await expect(dialog.getByRole("button", { name: "授予权限", exact: true })).toBeDisabled();
    await user.click(); await page.getByRole("option", { name: "独立审批人员", exact: true }).click();
    await expect(page.getByRole("listbox")).toHaveCount(0);
    await expect(user).not.toHaveAttribute("aria-invalid", "true");
    await expect(dialog.getByRole("button", { name: "授予权限", exact: true })).toBeEnabled();
    expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    if (mobile) await page.screenshot({ path: testInfo.outputPath("approval-grant-mobile.png") });
    await dialog.getByRole("button", { name: "取消", exact: true }).focus();
    await page.keyboard.press("Escape"); await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    expect(writes).toHaveLength(0);
  });
}
