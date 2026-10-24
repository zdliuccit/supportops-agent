import { expect, test } from "@playwright/test";

const timestamp = "2026-10-23T00:00:00Z";
const document = {
  id: "document-1", external_key: "分页文档", source_id: "source-1",
  source_name: "分页来源", source_environment: "mock", status: "draft",
  owner_user_id: "user-1", created_by: "user-1", current_version_id: null,
  created_at: timestamp, updated_at: timestamp,
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    // 仅用于被路由桩隔离的浏览器契约测试，不是后端签名令牌。
    const claims = { jti: "test-session", sub: "user-1", tenant_id: "tenant-1", exp: Math.floor(Date.now()/1000)+3600 };
    sessionStorage.setItem("supportops-auth-session-v1", `test.${btoa(JSON.stringify(claims))}.test`);
  });
  await page.route("**/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const current = Number(url.searchParams.get("page") ?? 1);
    const size = Number(url.searchParams.get("page_size") ?? 20);
    let body: unknown;
    if (path === "/v1/auth/me") body = {
      user_id: "user-1", tenant_id: "tenant-1", display_name: "测试管理员",
      roles: ["platform_admin"], email: "admin@example.test",
      organization_unit_id: null, job_title: null, company_name: "测试企业", company_logo_url: null,
    };
    else if (path === "/v1/admin/organization-units") body = { items: [] };
    else if (path === "/v1/admin/knowledge/documents/document-1") body = document;
    else {
      let items: unknown[] = [];
      if (path === "/v1/admin/tools") items = Array.from({ length: 12 }, (_, i) => ({
        id: `tool-${i+1}`, tool_id: `tool-${i+1}`, name: `分页工具${i+1}`,
        description: "分页工具说明", implementation_key: `tool-${i+1}`,
        required_roles: ["employee"], risk_level: "low", version: 1,
        is_enabled: true, is_revoked: false, created_at: timestamp, updated_at: timestamp,
      }));
      else if (path === "/v1/admin/knowledge/sources") items = [{ id: "source-1", name: "分页来源" }];
      else if (path === "/v1/admin/knowledge/documents") {
        items = url.searchParams.has("keywords") ? [] : Array.from({ length: 12 }, (_, i) => ({ ...document, id: `document-${i+1}`, external_key: `分页文档${i+1}` }));
      } else if (path.endsWith("/versions")) items = Array.from({ length: 12 }, (_, i) => ({
        id: `version-${12-i}`, document_id: document.id, version_number: 12-i,
        title: `分页版本${12-i}`, content_markdown: `正文${12-i}`,
        content_digest: "a".repeat(64), status: "draft", created_by: "user-1",
        created_at: timestamp, review_revision: 0, review_decision: null,
      }));
      body = { items: items.slice((current-1)*size, current*size), total: items.length, page: current, page_size: size, pages: Math.ceil(items.length/size) };
    }
    await route.fulfill({ json: body });
  });
});

for (const width of [390, 1280]) {
  test.describe(`分页交互 ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("工具目录通过键盘翻页并只请求当前页", async ({ page }) => {
      await page.goto("/agent-management/tools");
      await expect(page.getByRole("heading", { name: "分页工具1", exact: true }).or(page.getByText("分页工具1", { exact: true }))).toBeVisible();
      const next = page.getByRole("button", { name: "下一页", exact: true });
      await next.focus();
      const request = page.waitForRequest(r => r.url().includes("/v1/admin/tools?") && new URL(r.url()).searchParams.get("page") === "2");
      await next.press("Enter");
      await request;
      await expect(page.getByText("分页工具11", { exact: true })).toBeVisible();
      await expect(page.getByText("分页工具1", { exact: true })).toHaveCount(0);
      await expect(next).toBeDisabled();
    });

    test("知识来源选择及搜索将文档页码重置为1", async ({ page }) => {
      await page.goto("/knowledge");
      await expect(page.getByRole("button", { name: "查看 分页文档1", exact: true })).toBeVisible();
      await page.getByRole("button", { name: "下一页", exact: true }).click();
      await expect(page.getByRole("button", { name: "查看 分页文档11", exact: true })).toBeVisible();
      const request = page.waitForRequest(r => r.url().includes("/v1/admin/knowledge/documents?") && new URL(r.url()).searchParams.get("keywords") === "不存在");
      await page.getByRole("textbox", { name: "搜索知识文档" }).fill("不存在");
      expect(new URL((await request).url()).searchParams.get("page")).toBe("1");
      await expect(page.getByText("当前筛选条件下暂无知识文档")).toBeVisible();
      await page.getByRole("textbox", { name: "搜索知识文档" }).fill("");
      await expect(page.getByRole("button", { name: "查看 分页文档1", exact: true })).toBeVisible();
      await page.getByRole("combobox").filter({ hasText: "全部来源" }).click();
      await page.getByRole("option", { name: "分页来源" }).click();
      await expect(page.getByRole("button", { name: "上一页", exact: true })).toBeDisabled();
    });

    test("版本历史翻页不覆盖正在编辑的草稿", async ({ page }) => {
      await page.goto("/knowledge/document-1");
      await expect(page.getByRole("heading", { name: "v12 · 分页版本12" })).toBeVisible();
      await page.getByLabel("标题", { exact: true }).fill("尚未保存的编辑");
      const next = page.getByRole("button", { name: "下一页", exact: true });
      await next.focus();
      await next.press("Enter");
      await expect(page.getByRole("heading", { name: "v2 · 分页版本2" })).toBeVisible();
      await expect(page.getByLabel("标题", { exact: true })).toHaveValue("尚未保存的编辑");
      await expect(next).toBeDisabled();
    });
  });
}

test("工具列表错误可重试且保留分页契约", async ({ page }) => {
  await page.route("**/v1/admin/tools?**", route => route.fulfill({ status: 503, json: { error: { code: "UNAVAILABLE", message: "工具目录暂时不可用", correlation_id: "test" } } }));
  await page.goto("/agent-management/tools");
  await expect(page.getByRole("alert")).toContainText("工具目录暂时不可用");
  await page.unroute("**/v1/admin/tools?**");
  await page.getByRole("button", { name: "刷新列表", exact: true }).click();
  await expect(page.getByText("分页工具1", { exact: true })).toBeVisible();
});

for (const revoked of [false, true]) {
  test(`知识正文权限${revoked ? "刷新撤销后清除旧草稿" : "缺失时仅显示治理信息"}`, async ({ page }) => {
    let readable = revoked;
    let writes = 0;
    await page.route("**/v1/admin/knowledge/documents/document-1/versions*", async (route) => {
      if (route.request().method() !== "GET") { writes += 1; throw new Error("无正文授权不应保存旧草稿"); }
      await route.fulfill({ json: {
        items: [{ id: "version-1", document_id: document.id, version_number: 1, status: "draft",
          title: "受限版本", content_markdown: readable ? "原来获准的正文" : null,
          content_digest: "a".repeat(64), section_anchors: [], change_summary: "", created_by: "user-1",
          created_at: timestamp, review_revision: 0, review_decision: null }],
        total: 1, page: 1, page_size: 10, pages: 1,
      } });
    });
    await page.goto("/knowledge/document-1");
    await expect(page.getByRole("heading", { name: "v1 · 受限版本" })).toBeVisible();
    const body = page.getByLabel("Markdown 正文", { exact: true });
    if (revoked) {
      await expect(body).toHaveValue("原来获准的正文");
      await body.fill("尚未保存的受限编辑");
      readable = false;
      await page.locator('[data-list-toolbar="true"]').filter({ hasText: "版本列表" }).getByRole("button", { name: "刷新列表", exact: true }).click();
    }
    await expect(page.getByRole("status").filter({ hasText: "当前文档访问范围未授权" })).toBeVisible();
    await expect(body).toHaveValue("");
    await expect(body).toBeDisabled();
    await expect(page.getByLabel("标题", { exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toBeDisabled();
    await expect(page.getByText("原来获准的正文", { exact: true })).toHaveCount(0);
    expect(writes).toBe(0);
  });
}
