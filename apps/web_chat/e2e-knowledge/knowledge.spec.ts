import { expect, test } from "@playwright/test";
import { api, fixture, path, unique, token, login, setup, versions, confirm, createForm, addRoleAcl, approve } from "./helpers";

test("创建、显式ACL、版本、独立审核、发布、失效、回滚及归档真实主链", async ({ page }) => {
  test.setTimeout(90_000);
  const headers = await login(page);
  const dialog = await createForm(page, unique());
  await dialog.getByRole("button", { name: "创建草稿", exact: true }).click();
  await expect(page.getByRole("heading", { name: "v1 · 浏览器创建的知识", exact: true })).toBeVisible();
  const id = new URL(page.url()).pathname.split("/").at(-1)!;
  await expect(page.getByLabel("Markdown 正文", { exact: true })).toBeDisabled();
  await addRoleAcl(page);
  await expect(page.getByLabel("Markdown 正文", { exact: true })).toHaveValue("浏览器初始正文");
  await page.getByLabel("标题", { exact: true }).fill("第二版");
  await page.getByLabel("Markdown 正文", { exact: true }).fill("第二版正文");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  const v2 = page.getByRole("article").filter({ has: page.getByRole("heading", { name: "v2 · 第二版", exact: true }) });
  await confirm(page, "提交审核", v2);
  // 真实服务端拒绝创建人自审，页面保留可恢复错误。
  await v2.getByRole("button", { name: "作出审核决策", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "提交审核决策", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("不能审核自己的知识版本");
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await approve(page, "reviewer", id);
  await confirm(page, "发布");
  let state = await versions(page, id, headers);
  expect(state.find(item => item.version_number === 2)?.status).toBe("published");
  await page.getByLabel("标题", { exact: true }).fill("第三版");
  await page.getByLabel("Markdown 正文", { exact: true }).fill("第三版正文");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  const v3 = page.getByRole("article").filter({ has: page.getByRole("heading", { name: "v3 · 第三版", exact: true }) });
  await confirm(page, "提交审核", v3);
  await approve(page, "author", id);
  await confirm(page, "发布");
  state = await versions(page, id, headers);
  expect(state.filter(item => item.status === "published")).toHaveLength(1);
  expect(state.find(item => item.version_number === 2)?.status).toBe("superseded");
  await confirm(page, "创建回滚草稿", v2);
  await expect(page.getByRole("heading", { name: "v4 · 第二版", exact: true })).toBeVisible();
  state = await versions(page, id, headers);
  expect(state[0].status).toBe("draft");
  expect(state[0].review_decision).toBeNull();
  expect(state.find(item => item.version_number === 3)?.status).toBe("published");
  await confirm(page, "失效", v3);
  expect((await versions(page, id, headers)).find(item => item.version_number === 3)?.status).toBe("retired");
  await confirm(page, "归档文档");
  await expect(page).toHaveURL(/\/knowledge$/);
  expect((await page.request.get(`${api}${path}/documents/${id}`, { headers })).status()).toBe(404);
  expect((await page.request.get(`${api}${path}/documents/${id}/acl`, { headers })).status()).toBe(404);
});

test("创建表单必填及变更校验、唯一键冲突保留输入后可修正", async ({ page }) => {
  const { key } = await setup(page);
  await login(page);
  await page.getByRole("button", { name: "创建知识", exact: true }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "创建草稿", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: "文档标识", exact: true })).toHaveAttribute("aria-invalid", "true");
  await dialog.getByRole("textbox", { name: "文档标识", exact: true }).fill("有效标识");
  await expect(dialog.getByRole("textbox", { name: "文档标识", exact: true })).toHaveAttribute("aria-invalid", "false");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  dialog = await createForm(page, key);
  await dialog.getByRole("button", { name: "创建草稿", exact: true }).click();
  await expect(page.locator("[data-sonner-toast]").last()).toContainText(/已存在|重复|冲突/);
  await expect(dialog.getByRole("textbox", { name: "文档标识", exact: true })).toHaveValue(key);
  await dialog.getByRole("textbox", { name: "文档标识", exact: true }).fill(unique());
  await dialog.getByRole("button", { name: "创建草稿", exact: true }).click();
  await expect(page.getByRole("heading", { name: "v1 · 浏览器创建的知识", exact: true })).toBeVisible();
});

test("三类ACL保存并重读、空集合撤权即清正文与编辑副本", async ({ page }) => {
  const { document, headers } = await setup(page, { acl: false });
  await login(page, "author", `/knowledge/${document.id}`);
  await page.getByRole("button", { name: "配置访问范围", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("status")).toContainText("所有用户均无正文访问权限");
  for (const [index, kind, label] of [[1, "角色", "平台管理员"], [2, "用户", "验收reader"], [3, "直属部门", "浏览器验收部门"]] as const) {
    await dialog.getByRole("button", { name: "添加授权", exact: true }).click();
    if (kind !== "角色") {
      await dialog.getByRole("combobox", { name: `第${index}条授权类型` }).click();
      await page.getByRole("option", { name: kind, exact: true }).click();
    }
    await dialog.getByRole("combobox", { name: `第${index}条授权对象` }).click();
    await page.getByRole("option", { name: new RegExp(label) }).click();
  }
  await dialog.getByRole("button", { name: "保存访问范围", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel("Markdown 正文", { exact: true })).toHaveValue("# 章节\n\n隔离知识正文");
  const result = await page.request.get(`${api}${path}/documents/${document.id}/acl`, { headers });
  expect(await result.json()).toHaveLength(3);
  await page.getByRole("button", { name: "配置访问范围", exact: true }).click();
  await expect(dialog.getByRole("button", { name: /^移除第/ })).toHaveCount(3);
  for (let i = 0; i < 3; i++) await dialog.getByRole("button", { name: "移除第1条授权", exact: true }).click();
  await dialog.getByRole("button", { name: "保存访问范围", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel("Markdown 正文", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toBeDisabled();
  expect((await versions(page, document.id, headers))[0].content_markdown).toBeNull();
});

test("乐观修订冲突重新读取且不创建额外版本", async ({ page }) => {
  const { document, headers } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  await page.getByLabel("标题", { exact: true }).fill("本地编辑");
  expect((await page.request.post(`${api}${path}/documents/${document.id}/versions`, { headers, data: {
    title: "另一位编辑的新版本", content_markdown: "并发正文", expected_revision: 1,
  } })).ok()).toBeTruthy();
  const rejected = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith(`/documents/${document.id}/versions`));
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect((await rejected).status()).toBe(409);
  await expect(page.getByRole("heading", { name: "v2 · 另一位编辑的新版本", exact: true })).toBeVisible();
  await expect(page.getByLabel("标题", { exact: true })).toHaveValue("本地编辑");
  expect(await versions(page, document.id, headers)).toHaveLength(2);
});

test("批准后ACL变化阻止旧范围发布，重新送审及独立批准后才可发布", async ({ page }) => {
  const { document, headers } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  await confirm(page, "提交审核");
  await approve(page, "reviewer", document.id);
  expect((await page.request.put(`${api}${path}/documents/${document.id}/acl`, { headers, data: {
    entries: [{ subject_type: "role", subject_id: "platform_admin" }, { subject_type: "user", subject_id: fixture.reader_id }],
  } })).ok()).toBeTruthy();
  const response = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/publish"));
  await confirm(page, "发布");
  expect((await response).status()).toBe(409);
  expect((await versions(page, document.id, headers))[0].status).toBe("in_review");
  await login(page, "author", `/knowledge/${document.id}`);
  await confirm(page, "重新送审");
  await approve(page, "reviewer", document.id);
  await confirm(page, "发布");
  expect((await versions(page, document.id, headers))[0].status).toBe("published");
});

test("旧审核修订被拒绝，关闭刷新后可读取新审核轮次", async ({ page }) => {
  const { document, headers } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  await confirm(page, "提交审核");
  await login(page, "reviewer", `/knowledge/${document.id}`);
  await page.getByRole("button", { name: "作出审核决策", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const original = (await versions(page, document.id, headers))[0];
  const competing = await page.request.post(`${api}${path}/versions/${original.id}/review`, {
    headers: { Authorization: `Bearer ${await token(page, "reviewer")}` }, data: {
      decision: "rejected", comment: "其他窗口退回", expected_review_revision: original.review_revision, expected_content_digest: original.content_digest,
    },
  });
  expect(competing.ok()).toBeTruthy();
  expect((await page.request.post(`${api}${path}/versions/${original.id}/submit-review`, { headers,
    data: { expected_review_revision: (await competing.json()).review_revision },
  })).ok()).toBeTruthy();
  const rejected = page.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith("/review"));
  await dialog.getByRole("button", { name: "提交审核决策", exact: true }).click();
  expect((await rejected).status()).toBe(409);
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.reload();
  await page.getByRole("button", { name: "作出审核决策", exact: true }).click();
  await dialog.getByRole("button", { name: "提交审核决策", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect((await versions(page, document.id, headers))[0].review_decision).toBe("approved");
});

test("新版本已提交但响应丢失会读取原事实，不自动重复保存", async ({ page }) => {
  const { document, headers } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  let writes = 0;
  await page.route(`**/documents/${document.id}/versions*`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    writes += 1;
    expect((await route.fetch()).ok()).toBeTruthy();
    await route.abort("failed");
  });
  await page.getByLabel("标题", { exact: true }).fill("响应丢失的新版本");
  await page.getByLabel("Markdown 正文", { exact: true }).fill("实际改变的第二版正文");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("heading", { name: "v2 · 响应丢失的新版本", exact: true })).toBeVisible();
  expect(writes).toBe(1);
  expect(await versions(page, document.id, headers)).toHaveLength(2);
});

for (const status of [403, 404]) test(`详情复检${status}隐藏旧治理信息并支持重读`, async ({ page }) => {
  const { document, key } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  await expect(page.getByRole("heading", { name: key, exact: true })).toBeVisible();
  await page.route(`**/documents/${document.id}`, route => route.fulfill({ status, json: { error: { code: "NOT_FOUND", message: "资源当前不可访问" } } }));
  await page.locator('[data-list-toolbar="true"]').filter({ hasText: "版本列表" }).getByRole("button", { name: "刷新列表" }).click();
  await expect(page.getByRole("alert")).toContainText("资源当前不可访问");
  await expect(page.getByRole("heading", { name: key, exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Markdown 正文", { exact: true })).toHaveCount(0);
  await page.unroute(`**/documents/${document.id}`);
  await page.getByRole("button", { name: "重新加载知识详情", exact: true }).click();
  await expect(page.getByRole("heading", { name: key, exact: true })).toBeVisible();
});

test("审核拒绝原因校验、退回草稿后重新送审", async ({ page }) => {
  const { document, headers } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  await confirm(page, "提交审核");
  await login(page, "reviewer", `/knowledge/${document.id}`);
  await page.getByRole("button", { name: "作出审核决策", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("combobox", { name: "审核决策" }).click();
  await page.getByRole("option", { name: "拒绝并退回草稿" }).click();
  await expect(dialog.getByRole("button", { name: "提交审核决策", exact: true })).toBeDisabled();
  await dialog.getByRole("textbox", { name: "审核说明（必填）", exact: true }).fill("需要补充适用范围");
  await dialog.getByRole("button", { name: "提交审核决策", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect((await versions(page, document.id, headers))[0].review_decision).toBe("rejected");
  await login(page, "author", `/knowledge/${document.id}`);
  await confirm(page, "提交审核");
  expect((await versions(page, document.id, headers))[0].review_decision).toBe("pending");
});

for (const area of ["详情", "访问范围"] as const) test(`${area}读取503不展示旧数据且可以重试`, async ({ page }) => {
  const { document } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  await expect(page.getByLabel("Markdown 正文", { exact: true })).toHaveValue("# 章节\n\n隔离知识正文");
  const url = area === "详情" ? `**/documents/${document.id}/versions*` : `**/documents/${document.id}/acl`;
  await page.route(url, route => route.fulfill({ status: 503, json: { error: { code: "UNAVAILABLE", message: "知识读取暂时不可用" } } }));
  if (area === "详情") await page.locator('[data-list-toolbar="true"]').filter({ hasText: "版本列表" }).getByRole("button", { name: "刷新列表" }).click();
  else await page.getByRole("button", { name: "配置访问范围", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "知识读取暂时不可用" })).toBeVisible();
  if (area === "详情") {
    await expect(page.getByLabel("Markdown 正文", { exact: true })).toHaveValue("");
    await expect(page.getByText("隔离知识正文", { exact: true })).toHaveCount(0);
  } else await expect(page.getByRole("dialog").getByRole("button", { name: "保存访问范围", exact: true })).toBeDisabled();
  await page.unroute(url);
  if (area === "详情") await page.locator('[data-list-toolbar="true"]').filter({ hasText: "版本列表" }).getByRole("button", { name: "刷新列表" }).click();
  else {
    await page.getByRole("button", { name: "重新加载访问范围" }).click();
    await expect(page.getByRole("dialog").getByRole("button", { name: "保存访问范围", exact: true })).toBeEnabled();
  }
});

test("创建已提交但响应丢失只提示核对，双击不重复创建", async ({ page }) => {
  const headers = await login(page);
  let writes = 0;
  await page.route(`**${path}/documents`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    writes += 1;
    const response = await route.fetch();
    expect(response.status()).toBe(201);
    await route.abort("failed");
  });
  const key = unique();
  const dialog = await createForm(page, key);
  await dialog.getByRole("button", { name: "创建草稿", exact: true }).dblclick();
  await expect(dialog.getByRole("alert")).toContainText("创建结果未确认");
  await expect(dialog.getByRole("button", { name: "创建草稿", exact: true })).toBeDisabled();
  expect(writes).toBe(1);
  const response = await page.request.get(`${api}${path}/documents?keywords=${key}`, { headers });
  expect((await response.json()).items).toHaveLength(1);
});

test("ACL已提交但响应丢失仅重新读取，不自动重放保存", async ({ page }) => {
  const { document, headers } = await setup(page);
  let writes = 0;
  await login(page, "author", `/knowledge/${document.id}`);
  await page.getByRole("button", { name: "配置访问范围", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "移除第1条授权" })).toBeVisible();
  await page.route(`**/documents/${document.id}/acl`, async route => {
    if (route.request().method() !== "PUT") return route.continue();
    writes += 1;
    expect((await route.fetch()).ok()).toBeTruthy();
    await route.abort("failed");
  });
  await dialog.getByRole("button", { name: "移除第1条授权" }).click();
  await dialog.getByRole("button", { name: "保存访问范围", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("保存结果未确认");
  await dialog.getByRole("button", { name: "重新加载访问范围" }).click();
  await expect(dialog.getByRole("status")).toContainText("所有用户均无正文访问权限");
  expect(writes).toBe(1);
  expect(await (await page.request.get(`${api}${path}/documents/${document.id}/acl`, { headers })).json()).toEqual([]);
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByLabel("Markdown 正文", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toBeDisabled();
});

test("审核批准提交后响应丢失锁定重发，刷新后查得原批准", async ({ page }) => {
  const { document, headers } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  await confirm(page, "提交审核");
  await login(page, "reviewer", `/knowledge/${document.id}`);
  let writes = 0;
  await page.route(`**${path}/versions/*/review`, async route => {
    writes += 1;
    expect((await route.fetch()).ok()).toBeTruthy();
    await route.abort("failed");
  });
  await page.getByRole("button", { name: "作出审核决策", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "提交审核决策", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("审核结果未确认");
  await expect(dialog.getByRole("button", { name: "提交审核决策", exact: true })).toBeDisabled();
  expect(writes).toBe(1);
  expect((await versions(page, document.id, headers))[0].review_decision).toBe("approved");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("button", { name: "发布", exact: true })).toBeEnabled();
});

test("关闭后迟到的ACL读取不会覆盖重新打开的当前配置", async ({ page }) => {
  const { document, headers } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  let release!: () => void;
  let read!: () => void;
  const blocked = new Promise<void>(resolve => { release = resolve; });
  const observed = new Promise<void>(resolve => { read = resolve; });
  let requests = 0;
  await page.route(`**/documents/${document.id}/acl`, async route => {
    if (route.request().method() !== "GET" || ++requests !== 1) return route.continue();
    const response = await route.fetch();
    read(); await blocked;
    await route.fulfill({ response });
  });
  try {
    await page.getByRole("button", { name: "配置访问范围", exact: true }).click();
    await observed;
    await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
    expect((await page.request.put(`${api}${path}/documents/${document.id}/acl`, { headers, data: { entries: [] } })).ok()).toBeTruthy();
    await page.getByRole("button", { name: "配置访问范围", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("status")).toContainText("所有用户均无正文访问权限");
    const late = page.waitForResponse(response => response.request().method() === "GET" && response.url().endsWith(`/documents/${document.id}/acl`));
    release();
    await late;
    await expect(page.getByRole("dialog").getByRole("button", { name: /^移除第/ })).toHaveCount(0);
  } finally { release(); }
});

test("归档确认可取消，提交失败后查证并恢复操作", async ({ page }) => {
  const { document, headers } = await setup(page);
  await login(page, "author", `/knowledge/${document.id}`);
  await page.getByRole("button", { name: "归档文档", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "取消", exact: true }).click();
  expect((await page.request.get(`${api}${path}/documents/${document.id}`, { headers })).ok()).toBeTruthy();
  let writes = 0;
  await page.route(`**/documents/${document.id}`, route => {
    if (route.request().method() !== "DELETE") return route.continue();
    writes += 1;
    return route.fulfill({ status: 503, json: { error: { message: "归档暂不可用", code: "UNAVAILABLE" } } });
  });
  await confirm(page, "归档文档");
  await expect(page.locator("[data-sonner-toast]").last()).toContainText("归档暂不可用");
  await expect(page.getByRole("button", { name: "归档文档", exact: true })).toBeEnabled();
  expect(writes).toBe(1);
  await page.unroute(`**/documents/${document.id}`);
  await confirm(page, "归档文档");
  await expect(page).toHaveURL(/\/knowledge$/);
});

test("非管理员无法进入知识治理入口，真实ACL接口拒绝", async ({ page }) => {
  const { document } = await setup(page);
  const headers = await login(page, "reader", `/knowledge/${document.id}`);
  await expect(page.getByRole("button", { name: "保存草稿", exact: true })).toHaveCount(0);
  expect((await page.request.get(`${api}${path}/documents/${document.id}/acl`, { headers })).status()).toBe(403);
});
