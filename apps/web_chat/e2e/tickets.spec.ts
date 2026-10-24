import { expect, test as base } from "@playwright/test";
import { deferred } from "./helpers/approvals";
import { installTicketApi, packageFixture, ticketFixture, ticketTime } from "./helpers/tickets";

const test = base.extend<{ tickets: Awaited<ReturnType<typeof installTicketApi>> }>({
  tickets: [async ({ page }, use) => {
    await page.addInitScript(() => {
      const payload = btoa(JSON.stringify({ sub: "user-1", tenant_id: "tenant-1", jti: "ticket-session", exp: Math.floor(Date.now() / 1000) + 3600 }));
      sessionStorage.setItem("supportops-auth-session-v1", `e30.${payload}.signature`);
    });
    await page.route("**/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/v1/auth/me") await route.fulfill({ json: { id: "user-1", tenant_id: "tenant-1", display_name: "客服人员", email: "support@example.test", roles: [], organization_unit_id: null, job_title: null } });
      else if (path.endsWith("/company")) await route.fulfill({ json: { name: "隔离 UI 验收" } });
      else throw new Error(`未模拟的请求 ${path}`);
    });
    await use(await installTicketApi(page));
  }, { auto: true }],
});

test("W17.7 列表加载、键盘翻页、状态筛选与搜索空态", async ({ page, tickets }) => {
  tickets.items.splice(0, 1, ...Array.from({ length: 21 }, (_, i) => ticketFixture({ id: `ticket-${i}`, external_id: `MOCK-${i}`, title: `工单 ${i}`, status: i === 20 ? "closed" : "open" })));
  const blocked = deferred();
  await page.route(/\/v1\/mock-tickets\?/, async (route) => { await blocked.promise; await route.fallback(); }, { times: 1 });
  try {
    await page.goto("/mock-tickets"); await expect(page.getByRole("status").filter({ hasText: "正在读取当前权限工单" })).toBeVisible();
    blocked.release();
    await expect(page.getByRole("link", { name: /^工单 \d/ })).toHaveCount(20);
    await page.getByRole("button", { name: "下一页", exact: true }).focus(); await page.keyboard.press("Enter");
    await expect(page.getByRole("link", { name: "工单 20", exact: true })).toBeVisible();
    expect(tickets.calls.lists.at(-1)?.searchParams.get("page")).toBe("2");
    await page.getByRole("combobox", { name: "工单状态" }).click(); await page.getByRole("option", { name: "已关闭", exact: true }).click();
    await expect(page.getByRole("link", { name: /^工单 \d/ })).toHaveCount(1);
    expect(tickets.calls.lists.at(-1)?.searchParams.get("page")).toBe("1");
    expect(tickets.calls.lists.at(-1)?.searchParams.get("state")).toBe("closed");
    await page.getByRole("textbox", { name: "搜索工单标题" }).fill("无匹配标题"); await page.keyboard.press("Enter");
    await expect(page.getByText("暂无当前权限工单")).toBeVisible();
    expect(tickets.calls.writes).toHaveLength(0);
  } finally { blocked.release(); }
});

for (const status of [403, 503]) {
  test(`W17.7 列表${status}清除旧数据并可刷新`, async ({ page }) => {
    await page.goto("/mock-tickets"); await expect(page.getByRole("link", { name: "429 排查工单", exact: true })).toBeVisible();
    await page.route(/\/v1\/mock-tickets\?/, (route) => route.fulfill({ status, json: { error: { message: `工单列表失败 ${status}` } } }), { times: 1 });
    await page.getByRole("button", { name: "刷新列表" }).click();
    await expect(page.getByRole("alert")).toHaveText(`工单列表失败 ${status}`);
    await expect(page.getByRole("link", { name: "429 排查工单", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "刷新列表" }).click();
    await expect(page.getByRole("link", { name: "429 排查工单", exact: true })).toBeVisible();
  });
}

test("W17.7 工单处理记录和升级包分别服务端翻页", async ({ page, tickets }) => {
  tickets.commands.push(...Array.from({ length: 21 }, (_, i) => ({ id: `record-${i}`, kind: "reply" as const, body: `处理记录 ${i}`, actor_name: "客服人员", ticket_version: i + 1, ticket_status: "open" as const, created_at: ticketTime, data_environment: "mock" as const })));
  tickets.packages.splice(0, 1, ...Array.from({ length: 21 }, (_, i) => packageFixture({ id: `package-${i}` })));
  await page.goto("/mock-tickets/MOCK-001");
  await expect(page.getByText("处理记录 0", { exact: true })).toBeVisible();
  const history = page.getByRole("region", { name: "排查升级历史" });
  await history.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(history.getByRole("button", { name: "查看排查升级包", exact: true })).toHaveCount(1);
  expect(tickets.calls.packages.at(-1)?.searchParams.get("page")).toBe("2");
  await page.getByRole("button", { name: "下一页", exact: true }).last().click();
  await expect(page.getByText("处理记录 20", { exact: true })).toBeVisible();
  expect(tickets.calls.records.at(-1)?.searchParams.get("page")).toBe("2");
});

test("W17.7 认领、备注、回复、升级、释放和关闭后独立重读业务状态", async ({ page, tickets }) => {
  await page.goto("/mock-tickets/MOCK-001");
  for (const [operation, label] of [["claim", "认领并接管"], ["note", "内部备注"], ["reply", "回复申请者"], ["escalate", "升级"], ["release", "释放接管"], ["claim", "认领并接管"], ["close", "关闭"]] as const) {
    await page.getByRole("button", { name: label, exact: true }).click();
    const dialog = page.getByRole("dialog");
    const confirm = dialog.getByRole("button", { name: "确认操作", exact: true });
    if (operation === "note" || operation === "reply") {
      await expect(confirm).toBeDisabled(); await dialog.getByRole("textbox").fill("   ");
      await expect(dialog.getByRole("textbox")).toHaveAttribute("aria-invalid", "true");
      await expect(dialog.getByRole("alert")).toHaveText("请输入非空的备注或回复。");
      await dialog.getByRole("textbox").fill(`  ${label}正文  `);
    }
    const before = tickets.calls.details.length;
    await confirm.click(); await expect(dialog).toHaveCount(0);
    await expect.poll(() => tickets.calls.details.length).toBeGreaterThan(before);
    await expect(page.getByText("当前获准的工单摘要", { exact: true })).toBeVisible();
    expect(tickets.calls.writes.at(-1)?.kind).toBe(operation);
    expect(tickets.calls.writes.at(-1)?.expected_version).toBe(4 + tickets.calls.writes.length - 1);
    if (operation === "claim") await expect(page.getByText(/客服接管中，关联问题/)).toBeVisible();
    if (operation === "release" || operation === "close") await expect(page.getByText(/客服接管中，关联问题/)).toHaveCount(0);
  }
  await expect(page.getByRole("status").filter({ hasText: "已关闭" })).toBeVisible();
  await expect(page.getByRole("button", { name: "认领并接管" })).toHaveCount(0);
  expect(tickets.commands).toHaveLength(7);
});

test("W17.7 提交中重复键盘操作不重发、不关闭，成功后恢复内容焦点", async ({ page, tickets }) => {
  const blocked = deferred();
  await page.route("**/v1/mock-tickets/MOCK-001/commands", async (route) => { if (route.request().method() === "POST") await blocked.promise; await route.fallback(); });
  try {
    await page.goto("/mock-tickets/MOCK-001"); await page.getByRole("button", { name: "认领并接管" }).click();
    const dialog = page.getByRole("dialog"); await dialog.getByRole("button", { name: "确认操作" }).focus(); await page.keyboard.press("Enter");
    await expect(dialog.getByRole("button", { name: "正在提交…" })).toBeDisabled();
    await page.keyboard.press("Enter"); await page.keyboard.press("Escape");
    await expect(dialog).toBeVisible(); await expect(dialog.getByRole("button", { name: "关闭", exact: true })).toBeDisabled();
    blocked.release(); await expect(dialog).toHaveCount(0);
    await expect(page.getByLabel("工单详情内容")).toBeFocused();
    expect(tickets.calls.writes).toHaveLength(1);
  } finally { blocked.release(); }
});

test("W17.7 网络未知后只重试同键、同版本和原参数", async ({ page, tickets }) => {
  Object.assign(tickets.items[0], { can_claim: false, can_update: true });
  let original: unknown;
  await page.route("**/v1/mock-tickets/MOCK-001/commands", async (route) => {
    if (route.request().method() !== "POST") { await route.fallback(); return; }
    original = route.request().postDataJSON(); await route.abort("failed");
  }, { times: 1 });
  await page.goto("/mock-tickets/MOCK-001"); await page.getByRole("button", { name: "内部备注", exact: true }).click();
  const dialog = page.getByRole("dialog"); await dialog.getByRole("textbox").fill("  原始备注  ");
  await dialog.getByRole("button", { name: "确认操作" }).click();
  await expect(dialog.getByText(/重试会复用同一命令和原参数/)).toBeVisible();
  await expect(dialog.getByRole("textbox")).toBeDisabled(); await expect(dialog.getByRole("button", { name: "取消" })).toBeDisabled();
  await page.keyboard.press("Escape"); await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "重试同一命令" }).click(); await expect(dialog).toHaveCount(0);
  expect(tickets.calls.writes).toEqual([original]); expect(tickets.calls.writes[0].body).toBe("原始备注");
});

for (const status of [403, 404, 409]) {
  test(`W17.7 命令${status}隐藏旧详情和操作，关闭后重新授权`, async ({ page, tickets }) => {
    await page.goto("/mock-tickets/MOCK-001"); await page.getByRole("button", { name: "认领并接管" }).click();
    await page.route("**/v1/mock-tickets/MOCK-001/commands", async (route) => {
      if (route.request().method() === "POST") await route.fulfill({ status, json: { error: { message: "工单权限或版本变化" } } }); else await route.fallback();
    });
    const dialog = page.getByRole("dialog"); await dialog.getByRole("button", { name: "确认操作" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("工单权限或版本变化"); await expect(dialog.getByRole("button", { name: "确认操作" })).toBeDisabled();
    await expect(page.getByText("当前获准的工单摘要", { exact: true })).toHaveCount(0);
    Object.assign(tickets.items[0], { can_claim: false, can_update: false, can_read_context: false });
    await dialog.getByRole("button", { name: "取消" }).click();
    await page.getByRole("button", { name: "刷新列表" }).first().click();
    await expect(page.getByText(/当前仅具备工单权限/)).toBeVisible();
    await expect(page.getByRole("button", { name: "认领并接管" })).toHaveCount(0);
    expect(tickets.calls.writes).toHaveLength(0);
  });
}

for (const status of [403, 404, 503]) {
  test(`W17.7 详情${status}不显示旧摘要、记录和升级包`, async ({ page }) => {
    await page.goto("/mock-tickets/MOCK-001"); await expect(page.getByText("当前获准的工单摘要", { exact: true })).toBeVisible();
    await page.route("**/v1/mock-tickets/MOCK-001", (route) => route.fulfill({ status, json: { error: { message: `详情不可用 ${status}` } } }), { times: 1 });
    await page.getByRole("button", { name: "刷新列表" }).first().click();
    await expect(page.getByRole("alert")).toHaveText(`详情不可用 ${status}`);
    await expect(page.getByText("当前获准的工单摘要", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "排查升级历史" })).toHaveCount(0);
    await page.getByRole("button", { name: "刷新列表" }).first().click();
    await expect(page.getByText("当前获准的工单摘要", { exact: true })).toBeVisible();
  });
}

for (const status of [403, 404, 503]) {
  test(`W17.7 升级包${status}重新校验清除旧正文`, async ({ page }) => {
    await page.goto("/mock-tickets/MOCK-001"); await page.getByRole("button", { name: "查看排查升级包" }).click();
    const drawer = page.getByRole("dialog", { name: "排查升级包", exact: true });
    await expect(drawer.getByText("获准的原始排查问题", { exact: true })).toBeVisible();
    await page.route("**/v1/mock-tickets/MOCK-001/escalations/package-1", (route) => route.fulfill({ status, json: { error: { message: `来源不可用 ${status}` } } }), { times: 1 });
    await drawer.getByRole("button", { name: "重新校验来源与权限" }).click();
    await expect(drawer.getByRole("alert")).toHaveText(`来源不可用 ${status}`);
    await expect(drawer.getByText("获准的原始排查问题", { exact: true })).toHaveCount(0);
    await drawer.getByRole("button", { name: "重新校验来源与权限" }).click();
    await expect(drawer.getByText("获准的原始排查问题", { exact: true })).toBeVisible();
  });
}

test("W17.7 关闭升级包后迟到响应不能覆盖新选择", async ({ page, tickets }) => {
  tickets.packages.push(packageFixture({ id: "package-2", payload: { ...packageFixture().payload, question: "第二份来源问题" } }));
  const blocked = deferred();
  await page.route("**/v1/mock-tickets/MOCK-001/escalations/package-1", async (route) => { await blocked.promise; await route.fallback(); });
  try {
    await page.goto("/mock-tickets/MOCK-001"); await page.getByRole("button", { name: "查看排查升级包" }).first().click();
    await expect(page.getByText("正在校验升级来源…")).toBeVisible();
    await page.getByRole("button", { name: "关闭排查升级包" }).click();
    await page.getByRole("button", { name: "查看排查升级包" }).last().click();
    const drawer = page.getByRole("dialog", { name: "排查升级包", exact: true });
    await expect(drawer.getByText("第二份来源问题", { exact: true })).toBeVisible();
    const response = page.waitForResponse((value) => value.url().endsWith("/escalations/package-1")); blocked.release(); await response;
    await expect(drawer.getByText("第二份来源问题", { exact: true })).toBeVisible();
    await expect(drawer.getByText("获准的原始排查问题", { exact: true })).toHaveCount(0);
  } finally { blocked.release(); }
});

test("W17.7 只读工单没有命令和上下文请求，升级列表可为空", async ({ page, tickets }) => {
  Object.assign(tickets.items[0], { can_claim: false, can_read_context: false });
  await page.goto("/mock-tickets/MOCK-001"); await expect(page.getByText(/当前仅具备工单权限/)).toBeVisible();
  await expect(page.getByRole("button", { name: "认领并接管" })).toHaveCount(0); expect(tickets.calls.packages).toHaveLength(0);
  tickets.items[0].can_read_context = true; tickets.packages.splice(0);
  await page.getByRole("button", { name: "刷新列表" }).first().click();
  await expect(page.getByText("尚无升级包", { exact: true })).toBeVisible();
  await expect(page.getByText("暂无人工操作记录", { exact: true })).toBeVisible();
});

for (const mobile of [false, true]) {
  test(`W17.7 ${mobile ? "320px" : "桌面"}长工单、命令与升级抽屉的键盘和焦点`, async ({ page, tickets }, testInfo) => {
    if (mobile) await page.setViewportSize({ width: 320, height: 640 });
    Object.assign(tickets.items[0], { title: "LongTitle".repeat(80), summary: "LongSummary".repeat(200) });
    tickets.packages[0].payload.question = "LongQuestion".repeat(300);
    await page.goto("/mock-tickets/MOCK-001");
    const trigger = page.getByRole("button", { name: "认领并接管" }); await trigger.focus(); await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog"); await expect(dialog.getByRole("textbox")).toBeFocused();
    await page.keyboard.press("Shift+Tab"); await expect(dialog.getByRole("button", { name: "关闭", exact: true })).toBeFocused();
    await page.keyboard.press("Tab"); await expect(dialog.getByRole("textbox")).toBeFocused();
    expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    if (mobile) await page.screenshot({ animations: "disabled", path: testInfo.outputPath("ticket-command-mobile.png") });
    await page.keyboard.press("Escape"); await expect(trigger).toBeFocused();
    const packageTrigger = page.getByRole("button", { name: "查看排查升级包" }); await packageTrigger.focus(); await page.keyboard.press("Enter");
    const drawer = page.getByRole("dialog", { name: "排查升级包", exact: true });
    await expect(drawer.getByText(/^LongQuestion/)).toBeVisible();
    await page.keyboard.press("Shift+Tab"); await expect(drawer.getByRole("button", { name: "关闭", exact: true })).toBeFocused();
    await page.keyboard.press("Tab"); await expect(drawer.getByRole("button", { name: "关闭排查升级包" })).toBeFocused();
    expect(await drawer.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (mobile) await page.screenshot({ animations: "disabled", path: testInfo.outputPath("ticket-package-mobile.png") });
    await page.keyboard.press("Escape"); await expect(packageTrigger).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (mobile) await page.screenshot({ animations: "disabled", path: testInfo.outputPath("ticket-detail-mobile.png") });
  });
}
