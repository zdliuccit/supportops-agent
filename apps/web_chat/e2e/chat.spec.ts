import { createServer, type ServerResponse } from "node:http";
import { expect, test as base, type Page } from "@playwright/test";

import type { Agent, Conversation, KnowledgeEvidenceDetail, Message, RunEvent, SupportResult } from "../src/types";
import { actionFixture, approvalFixture, approvalLabels, installApprovalApi } from "./helpers/approvals";
import { installTicketApi, packageFixture } from "./helpers/tickets";

const timestamp = "2026-10-10T00:00:00Z";
const agent: Agent = {
  id: "chat-agent", slug: "chat-agent", name: "聊天测试 Agent", logo_url: null,
  description: "聊天交互回归", welcome_message: "欢迎使用", suggested_prompts: [],
  active_version_id: "version-1",
};

function gate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

interface MockRun {
  id: string;
  input: Message;
  answer: string;
  events: RunEvent[];
  response: ServerResponse | null;
  terminal: boolean;
}

async function installChat(page: Page) {
  const conversation: Conversation = {
    id: "chat-conversation", agent_id: agent.id, agent_version_id: "version-1",
    current_agent_version_id: "version-1", agent_version_number: 1,
    model_endpoint_version_id: "model-version-1", agent, title: "测试对话",
    is_pinned: false, status: "active", created_at: timestamp, updated_at: timestamp, messages: [],
  };
  const requests = { profiles: 0, histories: 0, details: 0, creates: 0, sends: 0, cancels: 0, streams: 0 };
  const runs: MockRun[] = [];
  let exists = false;
  let nextDetail: ReturnType<typeof gate> | null = null;
  let nextSend: ReturnType<typeof gate> | null = null;
  let nextCreate: ReturnType<typeof gate> | null = null;
  let nextCancel: ReturnType<typeof gate> | null = null;
  let failSend = false;
  let eventId = 0;
  let rejectedStreams = 0;
  let cancellationPending = false;
  const streamCursors: number[] = [];

  const writeEvent = (run: MockRun, type: string, data: Record<string, unknown>) => {
    const event = { id: ++eventId, type, data };
    run.events.push(event);
    run.response?.write(`id: ${event.id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
  };

  // 使用真实分段 SSE，验证空片段、首字等待、断流及重连，而非一次性返回整段正文。
  const server = createServer((request, response) => {
    response.setHeader("Access-Control-Allow-Origin", "*");
    response.setHeader("Access-Control-Allow-Headers", "Authorization, Last-Event-ID, Accept");
    if (request.method === "OPTIONS") { response.writeHead(204); response.end(); return; }
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    const run = runs.find((entry) => url.pathname === `/v1/runs/${entry.id}/events`);
    if (!run) { response.writeHead(404); response.end(); return; }
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    response.flushHeaders();
    run.response = response;
    const cursor = Number(url.searchParams.get("cursor") ?? 0);
    for (const event of run.events.filter((entry) => entry.id > cursor)) {
      response.write(`id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event.data)}\n\n`);
    }
    if (run.terminal) response.end();
    response.on("close", () => { if (run.response === response) run.response = null; });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("测试 SSE 服务未启动");

  await page.addInitScript(() => {
    const claims = { jti: "chat-session", sub: "user-1", tenant_id: "tenant-1", exp: Math.floor(Date.now() / 1000) + 3600 };
    sessionStorage.setItem("supportops-auth-session-v1", `test.${btoa(JSON.stringify(claims))}.test`);
  });
  await page.route("**/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    let body: unknown;
    if (path.endsWith("/events")) {
      requests.streams += 1;
      streamCursors.push(Number(url.searchParams.get("cursor") ?? 0));
      if (rejectedStreams > 0) {
        rejectedStreams -= 1;
        await route.fulfill({ status: 503, body: "stream unavailable" });
        return;
      }
      await route.continue({ url: `http://127.0.0.1:${address.port}${path}${url.search}` });
      return;
    }
    if (path === "/v1/auth/me") body = {
      id: "user-1", tenant_id: "tenant-1", display_name: "测试用户",
      email: "chat@example.test", roles: [], organization_unit_id: null, job_title: "测试",
    };
    else if (path === "/v1/agents") body = { items: [agent], total: 1, page: 1, page_size: 100, pages: 1 };
    else if (path === `/v1/agents/${agent.id}`) { requests.profiles += 1; body = agent; }
    else if (path.endsWith("/support-takeover")) body = { active: false, active_case_count: 0, observed_at: timestamp };
    else if (path.endsWith("/client-first-text")) body = { recorded: true };
    else if (path === "/v1/conversations" && request.method() === "POST") {
      requests.creates += 1;
      const blocked = nextCreate; nextCreate = null;
      if (blocked) await blocked.promise;
      exists = true;
      conversation.title = request.postDataJSON().title;
      body = conversation;
    }
    else if (path === "/v1/conversations") {
      requests.histories += 1;
      body = { items: exists ? [conversation] : [], total: Number(exists), page: 1, page_size: 20, pages: 1 };
    }
    else if (path === `/v1/conversations/${conversation.id}`) {
      requests.details += 1;
      const blocked = nextDetail; nextDetail = null;
      if (blocked) await blocked.promise;
      const run = runs.at(-1);
      const terminal = [...(run?.events ?? [])].reverse().find((event) => ["run.started", "run.cancelling", "run.cancelled", "run.completed", "run.failed", "run.waiting_approval", "run.waiting_action"].includes(event.type))?.type;
      body = { ...conversation, latest_run: run ? {
        id: run.id, input_message_id: run.input.id, content: run.answer,
        status: terminal === "run.completed" ? "completed"
          : terminal === "run.cancelled" ? "cancelled"
          : terminal === "run.waiting_approval" ? "waiting_approval"
          : terminal === "run.waiting_action" ? "waiting_action"
          : terminal === "run.failed" ? "failed"
          : terminal === "run.cancelling" ? "cancelling" : "running",
        last_event_id: run.events.at(-1)?.id ?? 0,
      } : null };
    }
    else if (path.endsWith("/messages")) {
      requests.sends += 1;
      const blocked = nextSend; nextSend = null;
      if (blocked) await blocked.promise;
      if (failSend) {
        failSend = false;
        await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { message: "消息提交失败" } }) });
        return;
      }
      const input: Message = { id: `user-${requests.sends}`, role: "user", content: request.postDataJSON().content, created_at: timestamp };
      conversation.messages.push(input);
      const run: MockRun = { id: `run-${runs.length + 1}`, input, answer: "", events: [], response: null, terminal: false };
      runs.push(run);
      writeEvent(run, "run.started", { status: "running" });
      body = { message_id: input.id, run_id: run.id, status: "queued" };
    }
    else if (path.endsWith("/cancel")) {
      requests.cancels += 1;
      const run = runs.find((entry) => path === `/v1/runs/${entry.id}/cancel`);
      if (!run) throw new Error("取消的运行不存在");
      const status = cancellationPending ? "cancelling" : "cancelled";
      run.terminal = !cancellationPending;
      writeEvent(run, `run.${status}`, { status });
      if (run.terminal) run.response?.end();
      body = { status };
      const blocked = nextCancel; nextCancel = null;
      if (blocked) await blocked.promise;
    }
    else throw new Error(`未模拟的请求：${request.method()} ${path}`);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  return {
    conversation, requests, runs, streamCursors,
    seed(messages: Message[]) { exists = true; conversation.messages = messages; },
    blockCreate() { nextCreate = gate(); return nextCreate; },
    blockSend() { nextSend = gate(); return nextSend; },
    blockDetail() { nextDetail = gate(); return nextDetail; },
    blockCancel() { nextCancel = gate(); return nextCancel; },
    failNextSend() { failSend = true; },
    rejectStreams(count: number) { rejectedStreams = count; },
    deferCancellation() { cancellationPending = true; },
    async connected(index?: number) {
      await expect.poll(() => runs[index ?? runs.length - 1]?.response != null).toBe(true);
    },
    delta(text: string, index = runs.length - 1) {
      const run = runs[index]; run.answer += text;
      writeEvent(run, "text.delta", { delta: text });
    },
    replace(text: string, index = runs.length - 1) {
      const run = runs[index]; run.answer = text;
      writeEvent(run, "text.replace", { content: text });
    },
    event(type: string, data: Record<string, unknown>, index = runs.length - 1) {
      writeEvent(runs[index], type, data);
    },
    wait(status: "waiting_approval" | "waiting_action", result?: SupportResult) {
      const run = runs.at(-1)!;
      if (result) conversation.messages = [...conversation.messages.filter((message) => message.id !== `assistant-${run.id}`),
        { id: `assistant-${run.id}`, role: "assistant", content: result.answer, created_at: timestamp, support_result: result }];
      writeEvent(run, `run.${status}`, { status });
      if (result) writeEvent(run, "result.ready", { message_id: `assistant-${run.id}`, schema_version: "1" });
    },
    async fragmentedDelta(text: string, index = runs.length - 1) {
      const run = runs[index]; run.answer += text;
      const event = { id: ++eventId, type: "text.delta", data: { delta: text } };
      run.events.push(event);
      const wire = Buffer.from(`id: ${event.id}\r\nevent: text.delta\r\ndata: ${JSON.stringify(event.data)}\r\n\r\n`);
      const cuts = [wire.indexOf("\r\n") + 1, wire.indexOf("🙂") + 2, wire.length];
      let start = 0;
      for (const end of cuts) {
        run.response?.write(wire.subarray(start, end)); start = end;
        await new Promise<void>((resolve) => setTimeout(resolve, 30));
      }
      // 重放同一个 ID，前端应忽略重复片段。
      run.response?.write(wire);
    },
    finish(index = runs.length - 1, result?: SupportResult) {
      const run = runs[index]; run.terminal = true;
      conversation.updated_at = new Date().toISOString();
      if (run.answer) conversation.messages = [...conversation.messages.filter((message) => message.id !== `assistant-${run.id}`), { id: `assistant-${run.id}`, role: "assistant", content: run.answer, created_at: timestamp, support_result: result ?? null }];
      if (result) writeEvent(run, "result.ready", { message_id: `assistant-${run.id}`, schema_version: "1" });
      writeEvent(run, "run.completed", { status: "completed" });
      run.response?.end();
    },
    disconnect(index = runs.length - 1) { runs[index].response?.end(); },
    confirmCancellation(index = runs.length - 1) {
      const run = runs[index]; run.terminal = true;
      writeEvent(run, "run.cancelled", { status: "cancelled" });
      run.response?.end();
    },
    async close() {
      nextCreate?.release(); nextSend?.release(); nextDetail?.release(); nextCancel?.release();
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}

const test = base.extend<{ chat: Awaited<ReturnType<typeof installChat>> }>({
  chat: async ({ page }, use) => {
    const chat = await installChat(page);
    try { await use(chat); } finally { await chat.close(); }
  },
});

async function openChat(page: Page, suffix = "") {
  await page.goto(`/agents/${agent.id}/chat${suffix}`);
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
}

async function send(page: Page, content: string) {
  await page.getByLabel("输入支持问题").fill(content);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
}

function citationResult(): SupportResult {
  return {
    schema_version: "1", run_id: "evidence-run", attempt_id: "evidence-attempt",
    answer: "引用验收回答", summary: null, suggested_actions: [], escalation_status: "not_requested",
    citations: [{
      evidence_id: "evidence-1", status: "available", snapshot_id: "snapshot-1", version_id: "version-1",
      title: "获准知识", section_id: "section-1", section_title: "排查步骤",
      content_digest: "a".repeat(64), retrieved_at: timestamp,
    }],
  };
}

function evidenceDetail(content = "当前获准的证据正文"): KnowledgeEvidenceDetail {
  return {
    evidence_id: "evidence-1", snapshot_id: "snapshot-1", version_id: "version-1",
    title: "获准知识", section_id: "section-1", section_title: "排查步骤",
    content_digest: "a".repeat(64), retrieved_at: timestamp, content,
  };
}

function seedResult(chat: Awaited<ReturnType<typeof installChat>>, result = citationResult()) {
  chat.seed([{ id: "evidence-answer", role: "assistant", content: result.answer, created_at: timestamp, support_result: result }]);
}

function approvalResult(): SupportResult {
  return { ...citationResult(), citations: [], business_actions: [{ action_id: "approval-action", availability: "available", status: "waiting_approval", risk_level: "R4", data_environment: "mock" }] };
}

async function installActionApi(page: Page, action = actionFixture()) {
  const writes: Record<string, unknown>[] = [];
  await page.route("**/v1/actions/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/receipts") || path.endsWith("/escalations")) await route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20, pages: 0 } });
    else if (path.endsWith("/approvers")) await route.fulfill({ json: { items: [{ user_id: "reviewer-2", display_name: "项目独立审批人" }], total: 1, page: 1, page_size: 20, pages: 1 } });
    else if (path.endsWith("/approval")) {
      writes.push(route.request().postDataJSON()); action.status = "waiting_approval";
      await route.fulfill({ json: approvalFixture({ can_decide: false, can_cancel: true, approver_id: "reviewer-2" }) });
    } else if (path === `/v1/actions/${action.id}`) await route.fulfill({ json: action });
    else throw new Error(`未模拟的动作请求 ${path}`);
  });
  return { action, writes };
}

for (const standalone of [false, true]) {
  test(`W17.7 ${standalone ? "独立320px" : "工作台"}接管提示刷新保持且只读排查可继续`, async ({ page, chat }, testInfo) => {
    if (standalone) await page.setViewportSize({ width: 320, height: 640 });
    seedResult(chat, { ...citationResult(), citations: [] });
    chat.conversation.support_takeover = { active: true, active_case_count: 1, observed_at: timestamp };
    await page.route("**/v1/conversations/*/support-takeover", (route) => route.fulfill({ json: { active: true, active_case_count: 1, observed_at: timestamp } }));
    await openChat(page, `${standalone ? "/standalone" : ""}/c/${chat.conversation.id}`);
    await expect(page.getByRole("status").filter({ hasText: "客服已接管关联问题" })).toBeVisible();
    await page.reload(); await expect(page.getByRole("status").filter({ hasText: "客服已接管关联问题" })).toBeVisible();
    await send(page, "继续只读排查"); await chat.connected(); chat.delta("仅执行只读排查"); chat.finish();
    await expect(page.getByLabel("输入支持问题")).toBeEnabled();
    await expect(page.getByRole("status").filter({ hasText: "客服已接管关联问题" })).toBeVisible();
    expect(chat.requests.sends).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (standalone) await page.screenshot({ animations: "disabled", path: testInfo.outputPath("chat-takeover-mobile.png") });
  });
}

test("W17.7 接管轮询按20秒更新，错误显示未确认并可恢复释放状态", async ({ page, chat }) => {
  await page.clock.install(); seedResult(chat, { ...citationResult(), citations: [] });
  let reads = 0;
  await page.route("**/v1/conversations/*/support-takeover", async (route) => {
    reads += 1;
    await route.fulfill(reads === 2 ? { status: 503, json: { error: { message: "暂不可用" } } } : { json: { active: reads === 1, active_case_count: Number(reads === 1), observed_at: timestamp } });
  });
  await openChat(page, `/c/${chat.conversation.id}`);
  await expect(page.getByText(/客服已接管关联问题/)).toBeVisible();
  await page.clock.fastForward(19_000); expect(reads).toBe(1);
  await page.clock.fastForward(1_100);
  await expect(page.getByText(/暂无法更新客服接管状态/)).toBeVisible();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await page.clock.fastForward(20_100); await expect.poll(() => reads).toBe(3);
  await expect(page.getByText(/暂无法更新客服接管状态|客服已接管关联问题/)).toHaveCount(0);
  expect(chat.requests.sends).toBe(0);
});

test("W17.7 隐藏页面不请求接管，切换新对话清除旧未确认提示", async ({ page, chat }) => {
  await page.clock.install(); seedResult(chat, { ...citationResult(), citations: [] });
  await page.addInitScript(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
  });
  let reads = 0;
  await page.route("**/v1/conversations/*/support-takeover", async (route) => { reads += 1; await route.fulfill({ status: 503, json: { error: { message: "暂不可用" } } }); });
  await openChat(page, `/c/${chat.conversation.id}`);
  await page.clock.fastForward(40_100); expect(reads).toBe(0);
  await page.evaluate(() => Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" }));
  await page.clock.fastForward(20_100); await expect(page.getByText(/暂无法更新客服接管状态/)).toBeVisible();
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(page.getByText(/暂无法更新客服接管状态/)).toHaveCount(0);
  const before = reads; await page.clock.fastForward(40_100); expect(reads).toBe(before);
  expect(chat.requests.creates).toBe(0);
});

for (const succeeded of [false, true]) {
  test(`W17.7 ${succeeded ? "已复核" : "未确认"}动作${succeeded ? "链接独立工单并展示升级包" : "不生成成功工单链接"}`, async ({ page, chat }) => {
    const result = approvalResult(); result.business_actions![0].status = succeeded ? "succeeded" : "unknown";
    seedResult(chat, result);
    const { writes } = await installActionApi(page, actionFixture({ status: succeeded ? "succeeded" : "unknown", external_id: "MOCK-001", approval_required: false, risk_level: "R2" }));
    const tickets = await installTicketApi(page);
    await installApprovalApi(page, []);
    await openChat(page, `/c/${chat.conversation.id}`);
    await page.getByRole("region", { name: "独立业务动作" }).getByRole("button").first().click();
    const drawer = page.getByRole("dialog", { name: "冻结动作与执行状态" });
    const link = drawer.getByRole("link", { name: "查看已复核 Mock 工单：MOCK-001" });
    if (succeeded) {
      await expect(link).toHaveAttribute("href", "/mock-tickets/MOCK-001"); await link.click();
      await expect(page.getByRole("heading", { name: "Mock 工单详情", exact: true })).toBeVisible();
      await expect(page.getByText("当前获准的工单摘要", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "查看排查升级包" }).click();
      await expect(page.getByRole("dialog", { name: "排查升级包", exact: true }).getByText(packageFixture().payload.question, { exact: true })).toBeVisible();
    } else await expect(link).toHaveCount(0);
    expect(writes).toHaveLength(0); expect(tickets.calls.writes).toHaveLength(0); expect(chat.requests.sends).toBe(0);
  });
}

for (const standalone of [false, true]) {
  for (const waiting of ["waiting_approval", "waiting_action"] as const) {
    test(`W16.10 ${standalone ? "独立320px" : "工作台"}${waiting}刷新续传、阻止新提交与取消`, async ({ page, chat }) => {
      if (standalone) await page.setViewportSize({ width: 320, height: 640 });
      await openChat(page, standalone ? "/standalone" : "");
      await send(page, "等待业务完成"); await chat.connected();
      chat.delta("已形成冻结提案"); chat.wait(waiting);
      const label = waiting === "waiting_approval" ? "等待指定人员审批" : "等待业务动作执行与复核";
      await expect(page.getByRole("status").filter({ hasText: label })).toBeVisible();
      await expect(page.getByLabel("输入支持问题")).toBeDisabled();
      await expect(page.getByRole("button", { name: "发送消息", exact: true })).toHaveCount(0);
      await page.reload(); await chat.connected();
      await expect(page.getByRole("status").filter({ hasText: label })).toBeVisible();
      await expect(page.getByLabel("输入支持问题")).toBeDisabled();
      expect(chat.requests.creates).toBe(1); expect(chat.requests.sends).toBe(1);
      expect(chat.streamCursors.at(-1)).toBe(chat.runs[0].events.at(-1)?.id);
      const stop = page.getByRole("button", { name: "停止生成", exact: true });
      await stop.focus(); await page.keyboard.press("Enter");
      await expect(page.getByRole("status").filter({ hasText: "已取消" })).toBeVisible();
      await expect(page.getByLabel("输入支持问题")).toBeEnabled();
      expect(chat.requests.cancels).toBe(1); expect(chat.requests.sends).toBe(1);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
  }
}

test("W16.10 审批等待到业务复核的SSE进度与断线恢复不误报完成", async ({ page, chat }) => {
  await openChat(page); await send(page, "等待独立审批"); await chat.connected();
  chat.delta("已形成冻结提案"); chat.wait("waiting_approval");
  await expect(page.getByRole("status").filter({ hasText: "等待指定人员审批" })).toBeVisible();
  chat.wait("waiting_action");
  await expect(page.getByRole("status").filter({ hasText: "等待业务动作执行与复核" })).toBeVisible();
  const streams = chat.requests.streams;
  chat.disconnect();
  await expect.poll(() => chat.requests.streams).toBeGreaterThan(streams);
  await chat.connected();
  await expect(page.getByRole("status").filter({ hasText: "等待业务动作执行与复核" })).toBeVisible();
  await expect(page.getByLabel("输入支持问题")).toBeDisabled();
  chat.finish();
  await expect(page.getByRole("status").filter({ hasText: "已完成" })).toBeVisible();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  expect(chat.requests.sends).toBe(1); expect(chat.requests.cancels).toBe(0);
});

for (const [event, status, label] of [
  ["approval.requested", "waiting_approval", "等待独立审批"],
  ["approval.consumed", "running", "动作处理中，尚未确认结果"],
  ["approval.cancelled", "cancelled", "已取消"],
] as const) {
  test(`W16.10 ${event}事件刷新已持久化的动作状态`, async ({ page, chat }) => {
    await openChat(page); await send(page, "审批事件进度"); await chat.connected();
    const result = approvalResult(); result.run_id = chat.runs[0].id;
    result.business_actions![0].status = "proposed";
    chat.delta(result.answer); chat.wait("waiting_approval", result);
    await expect(page.getByRole("button", { name: "提案已保存，尚未执行", exact: true })).toBeVisible();
    const reads = chat.requests.details;
    chat.conversation.messages = chat.conversation.messages.map((message) => message.support_result ? {
      ...message, support_result: { ...result, business_actions: [{ ...result.business_actions![0], status }] },
    } : message);
    chat.event(event, { approval_id: "approval-1", action_id: "approval-action" });
    await expect.poll(() => chat.requests.details).toBeGreaterThan(reads);
    await expect(page.getByRole("button", { name: label, exact: true })).toBeVisible();
    await expect(page.getByLabel("输入支持问题")).toBeDisabled();
    expect(chat.requests.sends).toBe(1);
  });
}

for (const status of ["rejected", "expired", "cancelled", "invalidated"] as const) {
  test(`W16.10 聊天${status}反馈与嵌套详情键盘焦点`, async ({ page, chat }) => {
    seedResult(chat, approvalResult());
    await installActionApi(page, actionFixture({ status: "cancelled" }));
    await installApprovalApi(page, [approvalFixture({ status, can_decide: false })]);
    await openChat(page, `/c/${chat.conversation.id}`);
    await page.getByRole("button", { name: "等待独立审批", exact: true }).click();
    const actionDrawer = page.getByRole("dialog", { name: "冻结动作与执行状态" });
    const view = actionDrawer.getByRole("button", { name: "查看", exact: true });
    await view.focus(); await page.keyboard.press("Enter");
    const approval = page.getByRole("dialog", { name: "独立审批详情" });
    await expect(approval.getByRole("status")).toHaveText(approvalLabels[status]);
    await expect(approval.getByRole("button", { name: "批准冻结动作" })).toHaveCount(0);
    await page.keyboard.press("Escape"); await expect(approval).toHaveCount(0);
    await expect(view).toBeFocused();
    expect(chat.requests.sends).toBe(0);
  });
}

for (const mobile of [false, true]) {
  test(`W16.10 ${mobile ? "320px" : "桌面"}选择获准人员后申请保持详情与父层状态`, async ({ page, chat }, testInfo) => {
    if (mobile) await page.setViewportSize({ width: 320, height: 640 });
    seedResult(chat, approvalResult());
    const api = await installActionApi(page, actionFixture({ status: "proposed" }));
    const approvals = await installApprovalApi(page, []);
    await page.route("**/v1/actions/approval-action/approval", async (route) => {
      approvals.items.push(approvalFixture({ can_decide: false, can_cancel: true, approver_id: "reviewer-2" }));
      await route.fallback();
    });
    await openChat(page, `/c/${chat.conversation.id}`);
    const original = page.getByRole("button", { name: "等待独立审批", exact: true });
    await original.click();
    const actionDrawer = page.getByRole("dialog", { name: "冻结动作与执行状态" });
    const select = actionDrawer.getByRole("combobox", { name: "指定审批人", exact: true });
    await select.focus(); await page.keyboard.press("Enter");
    const option = page.getByRole("option", { name: "项目独立审批人" });
    await expect(option).toBeVisible();
    await option.click();
    await actionDrawer.getByRole("button", { name: "申请独立审批", exact: true }).click();
    const approval = page.getByRole("dialog", { name: "独立审批详情" });
    await expect(approval.getByRole("status")).toHaveText("等待指定人员审批");
    await expect.poll(() => api.action.status).toBe("waiting_approval");
    await expect(approval.getByRole("button", { name: "取消未消费申请" })).toBeEnabled();
    expect(api.writes).toEqual([{ expected_generation: 0, expected_parameters_digest: "a".repeat(64), expected_catalog_version: 1, context_run_id: "evidence-run", approver_id: "reviewer-2" }]);
    if (mobile) await page.screenshot({ path: testInfo.outputPath("chat-approval-mobile.png") });
    await page.route("**/v1/approvals/approval-1/cancel", async (route) => { api.action.status = "cancelled"; await route.fallback(); });
    await approval.getByRole("button", { name: "取消未消费申请" }).click();
    await expect(approval.getByRole("status").filter({ hasText: "已取消" })).toBeVisible();
    await expect(approval.getByRole("button", { name: "取消未消费申请" })).toHaveCount(0);
    expect(approvals.calls.writes).toHaveLength(1);
    await approval.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(approval).toHaveCount(0);
    await expect(actionDrawer.getByRole("status").filter({ hasText: "已取消" })).toBeVisible();
    await expect(actionDrawer.getByRole("button", { name: "申请独立审批", exact: true })).toHaveCount(0);
    await expect(actionDrawer.getByRole("region", { name: "动作独立审批" })).toBeFocused();
    expect(await actionDrawer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.keyboard.press("Escape"); await expect(actionDrawer).toHaveCount(0);
    await expect(original).toBeFocused();
    expect(chat.requests.sends).toBe(0);
  });
}

test("W10.8 会话加载有可访问状态且不会显示旧内容或允许提交", async ({ page, chat }) => {
  seedResult(chat);
  const blocked = chat.blockDetail();
  try {
    await page.goto(`/agents/${agent.id}/chat/c/${chat.conversation.id}`);
    await expect.poll(() => chat.requests.details).toBe(1);
    await expect(page.getByRole("status").filter({ hasText: "读取会话" })).toBeVisible();
    await expect(page.locator("article")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "发送消息", exact: true })).not.toBeVisible();
    blocked.release();
    await expect(page.getByRole("button", { name: /^查看引用 1/ })).toBeVisible();
    await expect(page.getByLabel("输入支持问题")).toBeEnabled();
    expect(chat.requests.sends).toBe(0);
  } finally { blocked.release(); }
});

for (const mobile of [false, true]) {
  test(`W10.8 ${mobile ? "320px窄屏" : "桌面"}引用加载、键盘焦点圈定及关闭后返回入口`, async ({ page, chat }, testInfo) => {
    if (mobile) await page.setViewportSize({ width: 320, height: 640 });
    const result = citationResult();
    result.citations[0].title = "LongKnowledgeTitle".repeat(30);
    seedResult(chat, result);
    const blocked = gate();
    await page.route("**/v1/runs/*/evidence/*", async (route) => {
      await blocked.promise;
      await route.fulfill({ json: { ...evidenceDetail("LongUnbrokenContent".repeat(300) + '<script>alert("x")</script>'), title: "LongTitle".repeat(50) } });
    });
    try {
      await openChat(page, `/c/${chat.conversation.id}`);
      const citation = page.getByRole("button", { name: /^查看引用 1/ });
      const citationBounds = await citation.boundingBox();
      expect(citationBounds!.x + citationBounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
      await citation.focus();
      await page.keyboard.press("Enter");
      const drawer = page.getByRole("dialog", { name: "知识证据" });
      await expect(drawer.getByRole("status")).toContainText("正在读取获准证据");
      const close = drawer.getByRole("button", { name: "关闭知识证据", exact: true });
      await expect(close).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(drawer.getByRole("button", { name: "关闭", exact: true })).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(close).toBeFocused();
      blocked.release();
      await expect(drawer.getByText(/LongUnbrokenContent/)).toBeVisible();
      await expect(drawer.locator("script")).toHaveCount(0);
      await expect.poll(async () => {
        const bounds = await drawer.boundingBox();
        return bounds !== null && bounds.x >= 0 && bounds.x + bounds.width <= page.viewportSize()!.width + 1;
      }).toBe(true);
      expect(await drawer.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
      if (mobile) await page.screenshot({ path: testInfo.outputPath("citation-mobile.png") });
      await page.keyboard.press("Escape");
      await expect(drawer).toHaveCount(0);
      await expect(citation).toBeFocused();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    } finally { blocked.release(); }
  });
}

for (const status of [403, 404]) {
  test(`W10.8 引用${status}隐藏原详情并标记不可读，关闭不复活旧正文`, async ({ page, chat }) => {
    seedResult(chat);
    let reads = 0;
    await page.route("**/v1/runs/*/evidence/*", async (route) => {
      reads += 1;
      if (reads === 1) await route.fulfill({ json: evidenceDetail("不应继续展示的旧正文") });
      else await route.fulfill({ status, json: { error: { message: "引用已失效或无权读取" } } });
    });
    await openChat(page, `/c/${chat.conversation.id}`);
    await page.getByRole("button", { name: /^查看引用 1/ }).click();
    await expect(page.getByText("不应继续展示的旧正文", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "关闭知识证据", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "知识证据" })).toHaveCount(0);
    await page.getByRole("button", { name: /^查看引用 1/ }).click();
    const drawer = page.getByRole("dialog", { name: "知识证据" });
    await expect(drawer.getByRole("alert")).toContainText("引用已失效或无权读取");
    await expect(page.getByText("不应继续展示的旧正文", { exact: true })).toHaveCount(0);
    await drawer.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(drawer).toHaveCount(0);
    await expect(page.getByText("引用 1 已失效或无权读取", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.activeElement?.closest("article")?.textContent)).toContain("引用 1 已失效或无权读取");
    await expect(page.getByRole("button", { name: /^查看引用 1/ })).toHaveCount(0);
    expect(reads).toBe(2);
    expect(chat.requests.sends).toBe(0);
  });
}

test("W10.8 缺失引用仅显示不可读状态，空引用不伪造入口", async ({ page, chat }) => {
  const result = citationResult();
  result.citations[0] = { ...result.citations[0], status: "unavailable", title: null, snapshot_id: null, version_id: null, section_id: null, section_title: null, content_digest: null, retrieved_at: null };
  seedResult(chat, result);
  await openChat(page, `/c/${chat.conversation.id}`);
  await expect(page.getByText("引用 1 已失效或无权读取", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^查看引用/ })).toHaveCount(0);
  seedResult(chat, { ...result, citations: [] });
  await page.reload();
  await expect(page.locator("article")).toContainText(result.answer);
  await expect(page.getByRole("region", { name: "答案引用" })).toHaveCount(0);
});

test("W10.8 引用临时错误可键盘重试，加载时清空错误与旧正文", async ({ page, chat }) => {
  seedResult(chat);
  const blocked = gate();
  let reads = 0;
  await page.route("**/v1/runs/*/evidence/*", async (route) => {
    if (++reads === 1) await route.fulfill({ status: 503, json: { error: { message: "证据服务暂不可用" } } });
    else { await blocked.promise; await route.fulfill({ json: evidenceDetail() }); }
  });
  try {
    await openChat(page, `/c/${chat.conversation.id}`);
    await page.getByRole("button", { name: /^查看引用 1/ }).click();
    const drawer = page.getByRole("dialog", { name: "知识证据" });
    await expect(drawer.getByRole("alert")).toContainText("证据服务暂不可用");
    await drawer.getByRole("button", { name: "重新读取", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(drawer.getByRole("status")).toContainText("正在读取获准证据");
    await expect(drawer.getByRole("alert")).toHaveCount(0);
    blocked.release();
    await expect(drawer.getByText("当前获准的证据正文", { exact: true })).toBeVisible();
    expect(reads).toBe(2);
    expect(chat.requests.sends).toBe(0);
  } finally { blocked.release(); }
});

test("W10.8 关闭加载中的引用后，迟到响应不能覆盖重新打开的详情", async ({ page, chat }) => {
  seedResult(chat);
  const blocked = gate();
  let reads = 0;
  await page.route("**/v1/runs/*/evidence/*", async (route) => {
    if (++reads === 1) { await blocked.promise; await route.fulfill({ json: evidenceDetail("过期响应正文") }); }
    else await route.fulfill({ json: evidenceDetail("重新授权的正文") });
  });
  try {
    await openChat(page, `/c/${chat.conversation.id}`);
    await page.getByRole("button", { name: /^查看引用 1/ }).click();
    await expect.poll(() => reads).toBe(1);
    await page.getByRole("button", { name: "关闭知识证据", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "知识证据" })).toHaveCount(0);
    await page.getByRole("button", { name: /^查看引用 1/ }).click();
    await expect(page.getByText("重新授权的正文", { exact: true })).toBeVisible();
    const late = page.waitForResponse((response) => response.url().includes("/evidence/") && response.status() === 200);
    blocked.release();
    await late;
    await expect(page.getByText("过期响应正文", { exact: true })).toHaveCount(0);
    await expect(page.getByText("重新授权的正文", { exact: true })).toBeVisible();
  } finally { blocked.release(); }
});

test("W10.8 Agent授权撤销的历史消息可读，输入与发送均禁用", async ({ page, chat }) => {
  seedResult(chat, { ...citationResult(), citations: [] });
  chat.conversation.current_agent_version_id = null;
  await page.route(`**/v1/conversations/${chat.conversation.id}`, (route) => route.fulfill({ json: { ...chat.conversation, agent: { ...agent, active_version_id: null } } }));
  await page.route(`**/v1/agents/${agent.id}`, (route) => route.fulfill({ status: 404, json: { error: { message: "资源不存在" } } }));
  await page.goto(`/agents/${agent.id}/chat/c/${chat.conversation.id}`);
  await expect(page.getByRole("status").filter({ hasText: "该 Agent 已停用或你的授权已撤销" })).toBeVisible();
  await expect(page.locator("article")).toContainText("引用验收回答");
  await expect(page.getByLabel("输入支持问题")).toBeDisabled();
  await expect(page.getByRole("button", { name: "发送消息", exact: true })).toBeDisabled();
  expect(chat.requests.sends).toBe(0);
});

for (const pending of [false, true]) {
  test(`W10.8 结果更新时清理${pending ? "正在加载" : "已打开"}的旧引用并拒绝迟到响应`, async ({ page, chat }) => {
    const blocked = gate();
    let reads = 0;
    await page.route("**/v1/runs/*/evidence/*", async (route) => {
      reads += 1;
      if (pending) await blocked.promise;
      await route.fulfill({ json: evidenceDetail("原结果证据正文") });
    });
    try {
      await openChat(page);
      await send(page, "结果替换引用保护");
      await chat.connected();
      chat.delta("引用验收回答");
      const result = { ...citationResult(), run_id: chat.runs[0].id };
      const message: Message = { id: "refreshed-answer", role: "assistant", content: result.answer, created_at: timestamp, support_result: result };
      chat.conversation.messages.push(message);
      chat.event("result.ready", { message_id: message.id, schema_version: "1" });
      await page.getByRole("button", { name: /^查看引用 1/ }).click();
      await expect.poll(() => reads).toBe(1);
      const drawer = page.getByRole("dialog", { name: "知识证据" });
      if (pending) await expect(drawer.getByRole("status")).toContainText("正在读取获准证据");
      else await expect(drawer.getByText("原结果证据正文", { exact: true })).toBeVisible();
      message.support_result = { ...result, citations: [{ ...result.citations[0], status: "unavailable", title: null }] };
      chat.event("result.ready", { message_id: message.id, schema_version: "1" });
      await expect(page.getByText("引用 1 已失效或无权读取", { exact: true })).toBeVisible();
      await expect(drawer).toHaveCount(0);
      if (pending) {
        const late = page.waitForResponse((response) => response.url().includes("/evidence/"));
        blocked.release();
        await late;
      }
      await expect(page.getByText("原结果证据正文", { exact: true })).toHaveCount(0);
      await expect(drawer).toHaveCount(0);
      expect(chat.requests.sends).toBe(1);
    } finally { blocked.release(); }
  });
}

test("W10.8 320px窄屏的长消息和建议不会挤出消息视口", async ({ page, chat }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  const result = { ...citationResult(), answer: "LongUnbrokenAnswer".repeat(80), suggested_actions: [{ description: "LongSuggestedAction".repeat(80), status: "proposed" as const }] };
  seedResult(chat, result);
  chat.conversation.messages.unshift({ id: "long-input", role: "user", content: "LongUnbrokenQuestion".repeat(80), created_at: timestamp });
  await openChat(page, `/c/${chat.conversation.id}`);
  const viewport = page.getByRole("region", { name: "聊天消息" }).locator("[data-radix-scroll-area-viewport]");
  expect(await viewport.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(page.getByLabel("输入支持问题")).toBeInViewport();
});

test("W10.8 输入键盘支持换行、空输入保护、回车提交和停止", async ({ page, chat }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await openChat(page);
  const composer = page.getByLabel("输入支持问题");
  await expect(page.getByRole("button", { name: "发送消息", exact: true })).toBeDisabled();
  await composer.fill("第一行");
  await composer.press("Shift+Enter");
  await page.keyboard.insertText("第二行");
  await expect(composer).toHaveValue("第一行\n第二行");
  expect(chat.requests.sends).toBe(0);
  await composer.press("Enter");
  await chat.connected();
  await expect(composer).toBeDisabled();
  const stop = page.getByRole("button", { name: "停止生成", exact: true });
  await stop.focus();
  await page.keyboard.press("Enter");
  await expect(composer).toBeEnabled();
  expect(chat.requests.sends).toBe(1);
  expect(chat.requests.cancels).toBe(1);
});

test("W10.8 窄屏提交错误完整可读，失败后可用键盘再次提交", async ({ page, chat }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  const message = "服务暂不可用，请稍后重试。".repeat(6);
  let submissions = 0;
  await page.route("**/v1/conversations/*/messages", async (route) => {
    if (++submissions === 1) await route.fulfill({ status: 503, json: { error: { message } } });
    else await route.fallback();
  });
  await openChat(page);
  const composer = page.getByLabel("输入支持问题");
  await composer.fill("错误恢复");
  await composer.press("Enter");
  const error = page.getByRole("alert").getByText(message, { exact: true });
  await expect(error).toBeVisible();
  expect(await error.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(composer).toBeEnabled();
  await composer.fill("再次提交");
  await composer.press("Enter");
  await chat.connected();
  chat.delta("重试成功"); chat.finish();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator("article").last()).toHaveText("重试成功");
  expect(submissions).toBe(2);
});

for (const standalone of [false, true]) {
  for (const mobile of [false, true]) {
    test(`${standalone ? "独立窗口" : "工作台"} / ${mobile ? "移动端" : "桌面"}：首次与连续发送保留页面、等待状态及消息节点`, async ({ page, chat }) => {
      if (mobile) await page.setViewportSize({ width: 390, height: 844 });
      const suffix = standalone ? "/standalone" : "";
      await openChat(page, suffix);
      const baseline = { ...chat.requests };
      const composer = await page.getByLabel("输入支持问题").elementHandle();
      const position = await page.getByLabel("输入支持问题").boundingBox();
      const create = chat.blockCreate();
      const submit = chat.blockSend();
      await send(page, "第一个问题");
      await expect(page.getByRole("status", { name: "正在思考" })).toBeVisible();
      await expect(page.getByRole("button", { name: "停止生成", exact: true })).toBeDisabled();
      const user = await page.locator("article").first().elementHandle();
      create.release();
      await expect(page).toHaveURL(new RegExp(`/chat${suffix}/c/${chat.conversation.id}$`));
      await expect(page.getByRole("status", { name: "正在思考" })).toBeVisible();
      expect(await composer!.evaluate((element) => element.isConnected)).toBe(true);
      expect(await user!.evaluate((element) => element.isConnected)).toBe(true);
      expect(chat.requests.profiles).toBe(baseline.profiles);
      expect(chat.requests.histories).toBe(baseline.histories);
      expect(chat.requests.details).toBe(0);
      submit.release();
      await chat.connected();
      chat.delta("");
      await expect(page.getByRole("status", { name: "正在思考" })).toBeVisible();
      await expect(page.locator("article")).toHaveCount(1);
      const final = chat.blockDetail();
      chat.delta("首轮回答");
      await expect(page.locator("article").last()).toHaveText("首轮回答");
      await expect(page.getByRole("status", { name: "正在思考" })).toHaveCount(0);
      const answer = await page.locator("article").last().elementHandle();
      chat.finish();
      await expect.poll(() => chat.requests.details).toBe(1);
      final.release();
      await expect(page.getByLabel("输入支持问题")).toBeEnabled();
      expect(await answer!.evaluate((element) => element.isConnected)).toBe(true);
      expect(await user!.evaluate((element) => element.isConnected)).toBe(true);
      await send(page, "第二个问题");
      await chat.connected(1);
      await expect(page.getByRole("status", { name: "正在思考" })).toBeVisible();
      chat.delta("第二轮回答"); chat.finish();
      await expect(page.getByLabel("输入支持问题")).toBeEnabled();
      await expect(page.locator("article")).toHaveText(["第一个问题", "首轮回答", "第二个问题", "第二轮回答"]);
      expect(await composer!.evaluate((element) => element.isConnected)).toBe(true);
      expect(chat.requests.creates).toBe(1);
      expect(chat.requests.histories).toBe(baseline.histories);
      const after = await page.getByLabel("输入支持问题").boundingBox();
      expect(after!.y).toBe(position!.y);
      expect(await page.evaluate(() => window.scrollY)).toBe(0);
      await expect(page.getByText("正在加载历史会话…", { exact: true })).toHaveCount(0);
    });
  }
}

test("长回复仅滚动消息区域，阅读历史时不强制跟随，回到底部后继续跟随", async ({ page, chat }) => {
  chat.seed([
    { id: "history-user", role: "user", content: "历史问题", created_at: timestamp },
    { id: "history-answer", role: "assistant", content: "历史回答\n".repeat(80), created_at: timestamp },
  ]);
  await openChat(page, `/c/${chat.conversation.id}`);
  const viewport = page.getByRole("region", { name: "聊天消息" }).locator("[data-radix-scroll-area-viewport]");
  const position = await page.getByLabel("输入支持问题").boundingBox();
  await send(page, "继续提问");
  await chat.connected();
  await expect.poll(() => viewport.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(2);
  await viewport.evaluate((element) => { element.scrollTop = 0; });
  await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBe(0);
  chat.delta("新增回复\n".repeat(20));
  await expect(page.locator("article").last()).toContainText("新增回复");
  expect(await viewport.evaluate((element) => element.scrollTop)).toBe(0);
  await viewport.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect.poll(() => viewport.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(2);
  chat.delta("继续生成\n".repeat(10));
  await expect(page.locator("article").last()).toContainText("继续生成");
  await expect.poll(() => viewport.evaluate((element) => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(2);
  chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  expect((await page.getByLabel("输入支持问题").boundingBox())!.y).toBe(position!.y);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test("停止生成清除等待状态，下一轮不会使用上一轮运行或回复", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "等待后停止");
  await chat.connected();
  await page.getByRole("button", { name: "停止生成", exact: true }).click();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.getByRole("status", { name: "正在思考" })).toHaveCount(0);
  expect(chat.requests.cancels).toBe(1);
  const submitted = chat.blockSend();
  await send(page, "重新提问");
  await expect(page.getByRole("status", { name: "正在思考" })).toBeVisible();
  await expect(page.getByRole("button", { name: "停止生成", exact: true })).toBeDisabled();
  submitted.release();
  await chat.connected(1);
  chat.delta("新的回答"); chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.locator("article")).toHaveText(["等待后停止", "重新提问", "新的回答"]);
});

for (const standalone of [false, true]) {
  for (const pending of [false, true]) {
    for (const hasContent of [false, true]) {
      test(`${standalone ? "独立窗口" : "工作台"}明确停止后刷新：${pending ? "停止中" : "已取消"}、${hasContent ? "有正文" : "尚无正文"}${pending ? "续传直到确认" : "不重新订阅"}`, async ({ page, chat }) => {
        if (pending) chat.deferCancellation();
        await openChat(page, standalone ? "/standalone" : "");
        await send(page, "停止后刷新");
        await chat.connected();
        if (hasContent) {
          chat.delta("停止前已收到的正文🙂");
          await expect(page.locator("article").last()).toHaveText("停止前已收到的正文🙂");
        }
        await page.getByRole("button", { name: "停止生成", exact: true }).click();
        if (pending) await expect(page.getByLabel("输入支持问题")).toBeDisabled();
        else await expect(page.getByLabel("输入支持问题")).toBeEnabled();
        expect(chat.requests.cancels).toBe(1);
        const streams = chat.requests.streams;

        await page.reload();
        if (pending) {
          await expect(page.getByLabel("输入支持问题")).toBeDisabled();
          await expect.poll(() => chat.requests.streams).toBe(streams + 1);
          await chat.connected();
        } else await expect(page.getByLabel("输入支持问题")).toBeEnabled();
        await expect(page.getByText(pending ? "正在停止" : "已取消", { exact: true })).toBeVisible();
        await expect(page.locator("article")).toHaveText(hasContent
          ? ["停止后刷新", "停止前已收到的正文🙂"] : ["停止后刷新"]);
        await expect(page.getByRole("status", { name: "正在思考" })).toHaveCount(0);
        if (pending) {
          chat.delta("不应接纳的迟到正文");
          chat.confirmCancellation();
          await expect(page.getByLabel("输入支持问题")).toBeEnabled();
          await expect(page.getByText("已取消", { exact: true })).toBeVisible();
          await expect(page.locator("article")).toHaveText(hasContent
            ? ["停止后刷新", "停止前已收到的正文🙂"] : ["停止后刷新"]);
        }
        await expect(page.getByRole("button", { name: "停止生成", exact: true })).toHaveCount(0);
        expect(chat.requests.streams).toBe(streams + (pending ? 1 : 0));
        expect(chat.requests.sends).toBe(1);
      });
    }
  }
}

test("流式断开可以重连，保留部分回复节点并从游标继续追加", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "测试重连");
  await chat.connected();
  chat.delta("第一段");
  await expect(page.locator("article").last()).toHaveText("第一段");
  const answer = await page.locator("article").last().elementHandle();
  const streams = chat.requests.streams;
  chat.disconnect();
  await expect.poll(() => chat.requests.streams).toBe(streams + 1);
  await chat.connected();
  await expect(page.getByRole("status", { name: "正在思考" })).toHaveCount(0);
  chat.delta("第二段"); chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.locator("article").last()).toHaveText("第一段第二段");
  expect(await answer!.evaluate((element) => element.isConnected)).toBe(true);
  expect(chat.requests.sends).toBe(1);
});

test("提交失败恢复草稿与输入状态，重发复用已经创建的会话", async ({ page, chat }) => {
  await openChat(page);
  chat.failNextSend();
  await send(page, "失败后重发");
  await expect(page.getByRole("alert")).toContainText("消息提交失败");
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.getByLabel("输入支持问题")).toHaveValue("失败后重发");
  await expect(page.getByRole("status", { name: "正在思考" })).toHaveCount(0);
  await expect(page.locator("article")).toHaveCount(0);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await chat.connected();
  chat.delta("重发成功"); chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.locator("article")).toHaveText(["失败后重发", "重发成功"]);
  expect(chat.requests.creates).toBe(1);
});

test("上一轮已完成时，下一轮断流仍自动续传并保留思考状态", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "第一轮完成");
  await chat.connected();
  chat.delta("已完成的回复"); chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await send(page, "第二轮断流");
  await chat.connected(1);
  const streams = chat.requests.streams;
  chat.disconnect();
  await expect.poll(() => chat.requests.streams).toBe(streams + 1);
  await chat.connected(1);
  await expect(page.getByRole("status", { name: "正在思考" })).toBeVisible();
  chat.delta("重连后的回复"); chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.locator("article")).toHaveText(["第一轮完成", "已完成的回复", "第二轮断流", "重连后的回复"]);
});

test("新建对话与浏览器后退只切换内容，保留聊天工作区", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "可返回的历史问题");
  await chat.connected();
  chat.delta("历史回答"); chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  const baseline = { ...chat.requests };
  const composer = await page.getByLabel("输入支持问题").elementHandle();
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.locator("article")).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/c/${chat.conversation.id}$`));
  await expect(page.locator("article")).toHaveText(["可返回的历史问题", "历史回答"]);
  expect(await composer!.evaluate((element) => element.isConnected)).toBe(true);
  expect(chat.requests.profiles).toBe(baseline.profiles);
  expect(chat.requests.histories).toBe(baseline.histories);
});

test("提交等待期间离开聊天，迟到的结果不会在已卸载页面建立订阅", async ({ page, chat }) => {
  await openChat(page);
  const submitted = chat.blockSend();
  await send(page, "提交期间离开");
  await expect.poll(() => chat.requests.sends).toBe(1);
  await page.getByRole("button", { name: "返回工作台", exact: true }).click();
  await expect(page.getByRole("heading", { name: "选择一个 Agent" })).toBeVisible();
  const response = page.waitForResponse((entry) => entry.url().endsWith("/messages"));
  submitted.release();
  await response;
  await page.waitForLoadState("networkidle");
  expect(chat.requests.streams).toBe(0);
  await expect(page).toHaveURL(/\/agents$/);
});

test("收到大段真实增量就完整显示，完成事件立即结束生成状态", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "测试直接显示真实增量");
  await chat.connected();
  const answer = "连续生成🙂中文回答。".repeat(60);
  chat.delta(answer);
  await expect(page.locator("article").last()).toHaveText(answer);
  await expect(page.getByLabel("输入支持问题")).toBeDisabled();
  await expect(page.getByText("正在生成", { exact: true })).toBeVisible();
  chat.finish();
  await expect(page.locator("article").last()).toHaveText(answer);
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
});

for (const standalone of [false, true]) {
  for (const completedDuringReload of [false, true]) {
    test(`${standalone ? "独立窗口" : "工作台"}刷新恢复：直接显示快照并${completedDuringReload ? "确认完成" : "续传真实增量"}`, async ({ page, chat }) => {
      await openChat(page, standalone ? "/standalone" : "");
      await send(page, "刷新继续回答");
      await chat.connected();
      const first = "这是正在生成的回答🙂。".repeat(80);
      chat.delta(first);
      await expect(page.locator("article").last()).toHaveText(first);
      const blocked = chat.blockDetail();
      const details = chat.requests.details;
      const reload = page.reload();
      await expect.poll(() => chat.requests.details).toBe(details + 1);
      const extra = "刷新期间的新内容。".repeat(20);
      chat.delta(extra);
      if (completedDuringReload) chat.finish();
      const snapshotCursor = chat.runs[0].events.at(-1)!.id;
      blocked.release();
      await reload;
      await expect(page.locator("article").last()).toHaveText(first + extra);
      if (!completedDuringReload) {
        await expect.poll(() => chat.requests.streams).toBe(2);
        await chat.connected();
        expect(chat.streamCursors.at(-1)).toBe(snapshotCursor);
        chat.delta("最后一段"); chat.finish();
      }
      await expect(page.getByLabel("输入支持问题")).toBeEnabled();
      await expect(page.locator("article")).toHaveText([
        "刷新继续回答", first + extra + (completedDuringReload ? "" : "最后一段"),
      ]);
      expect(chat.requests.sends).toBe(1);
      expect(chat.requests.creates).toBe(1);
      expect(chat.requests.streams).toBe(completedDuringReload ? 1 : 2);
    });
  }
}

test("结构化校验重试改写正文时不会拼接旧答案，刷新仍保持正确正文", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "模型改写测试");
  await chat.connected();
  chat.delta("初始答案");
  await expect(page.locator("article").last()).toHaveText("初始答案");
  chat.replace("修正后的回答🙂");
  await expect(page.locator("article").last()).toHaveText("修正后的回答🙂");
  await page.reload();
  await expect.poll(() => chat.requests.streams).toBe(2);
  await chat.connected();
  chat.delta("后续正文"); chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.locator("article")).toHaveText(["模型改写测试", "修正后的回答🙂后续正文"]);
});

test("跨网络分片的 CRLF 和 emoji 不丢失，重放事件不会重复显示", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "分片编码测试");
  await chat.connected();
  await chat.fragmentedDelta("分片🙂保持正确");
  chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.locator("article")).toHaveText(["分片编码测试", "分片🙂保持正确"]);
});

test("连续重连失败后保留正文与手动重试，不允许在原 Run 未结束时重复提交", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "重连失败恢复");
  await chat.connected();
  chat.delta("已接收的正文");
  await expect(page.locator("article").last()).toHaveText("已接收的正文");
  chat.rejectStreams(5);
  chat.disconnect();
  await expect(page.getByRole("alert")).toContainText("事件流连接中断", { timeout: 20_000 });
  await expect(page.getByLabel("输入支持问题")).toBeDisabled();
  await expect(page.getByRole("button", { name: "停止生成", exact: true })).toBeEnabled();
  const cursor = chat.runs[0].events.at(-1)!.id;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await chat.connected();
  expect(chat.streamCursors.at(-1)).toBe(cursor);
  chat.delta("恢复后的正文"); chat.finish();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.locator("article")).toHaveText(["重连失败恢复", "已接收的正文恢复后的正文"]);
  expect(chat.requests.sends).toBe(1);
});

test("旧纯正文消息刷新保持兼容，不补造引用或已执行动作", async ({ page, chat }) => {
  chat.seed([
    { id: "legacy-question", role: "user", content: "旧问题", created_at: timestamp },
    { id: "legacy-answer", role: "assistant", content: "旧纯正文🙂", created_at: timestamp, support_result: null },
  ]);
  await openChat(page, `/c/${chat.conversation.id}`);
  await expect(page.locator("article")).toHaveText(["旧问题", "旧纯正文🙂"]);
  await page.reload();
  await expect(page.locator("article")).toHaveText(["旧问题", "旧纯正文🙂"]);
  await expect(page.getByRole("region", { name: "答案引用" })).toHaveCount(0);
  await expect(page.getByText("建议动作（尚未执行）", { exact: true })).toHaveCount(0);
  expect(chat.requests.sends).toBe(0);
  expect(chat.requests.streams).toBe(0);
});

test("停止中仍采纳服务端空正文撤回，不能复活无效草稿或继续思考", async ({ page, chat }) => {
  chat.deferCancellation();
  await openChat(page);
  await send(page, "撤回无效草稿");
  await chat.connected();
  chat.delta("待核验草稿");
  await expect(page.locator("article").last()).toHaveText("待核验草稿");
  await page.getByRole("button", { name: "停止生成", exact: true }).click();
  await expect(page.getByText("正在停止", { exact: true })).toBeVisible();
  chat.replace("");
  await expect(page.locator("article")).toHaveText(["撤回无效草稿"]);
  await expect(page.getByRole("status", { name: "正在思考" })).toHaveCount(0);
  chat.delta("不能复活的迟到正文");
  chat.confirmCancellation();
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.locator("article")).toHaveText(["撤回无效草稿"]);
});

test("结果就绪后刷新完整恢复建议和升级状态，不冒充执行成功", async ({ page, chat }) => {
  await openChat(page);
  await send(page, "结构化结果恢复");
  await chat.connected();
  chat.delta("已校验的完整回答");
  const result: SupportResult = {
    schema_version: "1", run_id: chat.runs[0].id, attempt_id: "attempt-1",
    answer: "已校验的完整回答", summary: "恢复摘要", citations: [],
    suggested_actions: [{ description: "联系支持人员复核", status: "proposed" }],
    escalation_status: "recommended", business_actions: [],
  };
  chat.finish(0, result);
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
  await expect(page.getByText("建议动作（尚未执行）", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText("建议动作（尚未执行）", { exact: true })).toBeVisible();
  await expect(page.getByText("联系支持人员复核", { exact: true })).toBeVisible();
  await expect(page.getByText("建议转人工处理；尚未创建工单或执行升级。", { exact: true })).toBeVisible();
  expect(chat.requests.sends).toBe(1);
  expect(chat.requests.streams).toBe(1);
});

test("完成终态先于取消回执到达时，迟到的停止中不能覆盖完成", async ({ page, chat }) => {
  chat.deferCancellation();
  await openChat(page);
  await send(page, "取消回执与完成竞争");
  await chat.connected();
  chat.delta("最终回答");
  const acknowledgement = chat.blockCancel();
  try {
    await page.getByRole("button", { name: "停止生成", exact: true }).click();
    await expect.poll(() => chat.requests.cancels).toBe(1);
    chat.finish();
    await expect(page.getByLabel("输入支持问题")).toBeEnabled();
    const ack = page.waitForResponse((response) => response.url().endsWith("/cancel"));
    acknowledgement.release();
    await ack;
    await expect(page.getByText("正在停止", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("输入支持问题")).toBeEnabled();
    await send(page, "完成后的新一轮");
    await chat.connected(1);
    chat.delta("新的回答"); chat.finish();
    await expect(page.getByLabel("输入支持问题")).toBeEnabled();
    await expect(page.locator("article")).toHaveText(["取消回执与完成竞争", "最终回答", "完成后的新一轮", "新的回答"]);
  } finally {
    acknowledgement.release();
  }
});
