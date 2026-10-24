import { expect, type Page } from "@playwright/test";
import type { KnowledgeDocument, KnowledgeVersion } from "../src/types";

export const api = process.env.SUPPORTOPS_KNOWLEDGE_E2E_API_URL!;
const password = process.env.SUPPORTOPS_KNOWLEDGE_E2E_PASSWORD!;
export const fixture = JSON.parse(process.env.SUPPORTOPS_KNOWLEDGE_E2E_FIXTURE!) as Record<"source_id" | "author_id" | "reviewer_id" | "reader_id" | "department_id", string>;
export const path = "/v1/admin/knowledge";
export const unique = () => `browser-${crypto.randomUUID()}`;
export async function token(page: Page, who = "author") {
  const response = await page.request.post(`${api}/v1/auth/login`, { data: { email: `${who}@knowledge.example.test`, password } });
  expect(response.status()).toBe(200);
  return (await response.json()).access_token as string;
}
export async function login(page: Page, who = "author", location = "/knowledge") {
  const access = await token(page, who);
  await page.goto("/login");
  await page.evaluate(value => sessionStorage.setItem("supportops-auth-session-v1", value), access);
  await page.goto(location);
  return { Authorization: `Bearer ${access}` };
}
export async function setup(page: Page, options: { acl?: boolean; who?: string; key?: string; title?: string; body?: string } = {}) {
  const headers = { Authorization: `Bearer ${await token(page, options.who)}` };
  const key = options.key ?? unique();
  const response = await page.request.post(`${api}${path}/documents`, { headers, data: {
    source_id: fixture.source_id, external_key: key, owner_user_id: fixture.author_id,
    title: options.title ?? "隔离知识标题", content_markdown: options.body ?? "# 章节\n\n隔离知识正文",
  } });
  expect(response.status()).toBe(201);
  const document = await response.json() as KnowledgeDocument;
  if (options.acl !== false) expect((await page.request.put(`${api}${path}/documents/${document.id}/acl`, {
    headers, data: { entries: [{ subject_type: "role", subject_id: "platform_admin" }] },
  })).ok()).toBeTruthy();
  return { document, headers, key };
}
export async function versions(page: Page, id: string, headers: Record<string, string>) {
  const response = await page.request.get(`${api}${path}/documents/${id}/versions`, { headers });
  expect(response.status()).toBe(200);
  return (await response.json()).items as KnowledgeVersion[];
}
export async function confirm(page: Page, label: string, scope = page.locator("body")) {
  await scope.getByRole("button", { name: label, exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: `确认${label === "发布" ? "发布版本" : label === "失效" ? "失效版本" : label === "重新送审" ? "提交审核" : label}`, exact: true }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
}
export async function createForm(page: Page, key: string) {
  await page.getByRole("button", { name: "创建知识", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "创建知识", exact: true });
  await dialog.getByRole("combobox", { name: "知识来源", exact: true }).click();
  await page.getByRole("option", { name: "浏览器验收来源", exact: true }).click();
  await dialog.getByRole("textbox", { name: "文档标识", exact: true }).fill(key);
  await dialog.getByRole("combobox", { name: "负责人", exact: true }).click();
  await page.getByRole("option", { name: /验收author/ }).click();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await dialog.getByRole("textbox", { name: "初始标题", exact: true }).fill("浏览器创建的知识");
  await dialog.getByRole("textbox", { name: "初始正文", exact: true }).fill("浏览器初始正文");
  return dialog;
}
export async function addRoleAcl(page: Page) {
  await page.getByRole("button", { name: "配置访问范围", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "配置访问范围", exact: true });
  await expect(dialog.getByRole("button", { name: "保存访问范围", exact: true })).toBeEnabled();
  await dialog.getByRole("button", { name: "添加授权", exact: true }).click();
  await dialog.getByRole("combobox", { name: "第1条授权对象" }).click();
  await page.getByRole("option", { name: "平台管理员", exact: true }).click();
  await dialog.getByRole("button", { name: "保存访问范围", exact: true }).click();
  await expect(dialog).toHaveCount(0);
}
export async function approve(page: Page, who: string, id: string) {
  await login(page, who, `/knowledge/${id}`);
  await page.getByRole("button", { name: "作出审核决策", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "提交审核决策", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发布", exact: true })).toBeEnabled();
}
