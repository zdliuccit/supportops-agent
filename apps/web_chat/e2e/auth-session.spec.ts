import { expect, test } from "@playwright/test";

const key = "supportops-auth-session-v1";

test("过期令牌进入登录页并清除旧存储", async ({ page }) => {
  await page.addInitScript((storageKey) => {
    const claims = { jti: "session", sub: "user", tenant_id: "tenant", exp: Math.floor(Date.now()/1000)-10 };
    sessionStorage.setItem(storageKey, `test.${btoa(JSON.stringify(claims))}.test`);
    localStorage.setItem("supportops-access-token-v4", "legacy-test-token");
  }, key);
  await page.goto("/agents");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "登录您的账户" })).toBeVisible();
  expect(await page.evaluate((storageKey) => sessionStorage.getItem(storageKey), key)).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("supportops-access-token-v4"))).toBeNull();
});

test("服务器撤销或停用后的401清理会话并回到登录", async ({ page }) => {
  await page.addInitScript((storageKey) => {
    const claims = { jti: "session", sub: "user", tenant_id: "tenant", exp: Math.floor(Date.now()/1000)+3600 };
    sessionStorage.setItem(storageKey, `test.${btoa(JSON.stringify(claims))}.test`);
  }, key);
  await page.route("**/v1/auth/me", route => route.fulfill({ status: 401, json: {
    error: { code: "AUTH_SESSION_INVALID", message: "登录会话已失效，请重新登录", correlation_id: "test" },
  } }));
  await page.goto("/agents");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "登录您的账户" })).toBeVisible();
  expect(await page.evaluate((storageKey) => sessionStorage.getItem(storageKey), key)).toBeNull();
});

test("登录错误展示统一消息且不保存令牌", async ({ page }) => {
  await page.route("**/v1/auth/login", route => route.fulfill({ status: 401, json: {
    error: { code: "AUTH_LOGIN_FAILED", message: "邮箱或密码错误", correlation_id: "test" },
  } }));
  await page.goto("/login");
  await expect(page.getByPlaceholder("请输入邮箱地址", { exact: true })).toHaveValue("");
  await expect(page.getByPlaceholder("请输入密码", { exact: true })).toHaveValue("");
  await page.getByPlaceholder("请输入邮箱地址", { exact: true }).fill("unknown@example.test");
  await page.getByPlaceholder("请输入密码", { exact: true }).fill("invalid-test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("邮箱或密码错误", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
  expect(await page.evaluate((storageKey) => sessionStorage.getItem(storageKey), key)).toBeNull();
});
