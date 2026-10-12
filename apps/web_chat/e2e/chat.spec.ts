import { createServer, type ServerResponse } from "node:http";
import { expect, test as base, type Page } from "@playwright/test";

import type { Agent, Conversation, Message, RunEvent } from "../src/types";

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
  let failSend = false;
  let eventId = 0;
  let rejectedStreams = 0;
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

  await page.addInitScript(() => localStorage.setItem("supportops-access-token-v4", "chat-test-token"));
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
      const terminal = run?.events.at(-1)?.type;
      body = { ...conversation, latest_run: run ? {
        id: run.id, input_message_id: run.input.id, content: run.answer,
        status: terminal === "run.completed" ? "completed" : terminal === "run.cancelled" ? "cancelled" : "running",
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
      body = { message_id: input.id, run_id: run.id, status: "queued" };
    }
    else if (path.endsWith("/cancel")) {
      requests.cancels += 1;
      const run = runs.find((entry) => path === `/v1/runs/${entry.id}/cancel`);
      if (!run) throw new Error("取消的运行不存在");
      run.terminal = true;
      writeEvent(run, "run.cancelled", { status: "cancelled" });
      run.response?.end();
      body = { status: "cancelled" };
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
    failNextSend() { failSend = true; },
    rejectStreams(count: number) { rejectedStreams = count; },
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
    finish(index = runs.length - 1) {
      const run = runs[index]; run.terminal = true;
      conversation.updated_at = new Date().toISOString();
      if (run.answer) conversation.messages.push({ id: `assistant-${run.id}`, role: "assistant", content: run.answer, created_at: timestamp });
      writeEvent(run, "run.completed", { status: "completed" });
      run.response?.end();
    },
    disconnect(index = runs.length - 1) { runs[index].response?.end(); },
    async close() {
      nextCreate?.release(); nextSend?.release(); nextDetail?.release();
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

test("大段增量与完成事件同时到达时仍逐步显示，显示完成前保持生成状态", async ({ page, chat }) => {
  await page.clock.install();
  await openChat(page);
  await send(page, "测试平滑显示");
  await chat.connected();
  const answer = "连续生成🙂中文回答。".repeat(60);
  chat.delta(answer); chat.finish();
  await expect.poll(async () => (await page.locator("article").last().textContent())?.length ?? 0).toBeGreaterThan(30);
  const partial = await page.locator("article").last().textContent();
  expect(partial!.length).toBeLessThan(answer.length);
  expect(answer.startsWith(partial!)).toBe(true);
  expect(partial).not.toContain("�");
  await expect(page.getByLabel("输入支持问题")).toBeDisabled();
  await expect(page.getByText("正在生成", { exact: true })).toBeVisible();
  await page.clock.runFor(Math.ceil(answer.length / 45 * 1000) + 1000);
  await expect(page.locator("article").last()).toHaveText(answer);
  await expect(page.getByLabel("输入支持问题")).toBeEnabled();
});

for (const standalone of [false, true]) {
  for (const completedDuringReload of [false, true]) {
    test(`${standalone ? "独立窗口" : "工作台"}刷新恢复：刷新期间${completedDuringReload ? "完成" : "继续生成"}也从显示位置续接`, async ({ page, chat }) => {
      await page.clock.install();
      await openChat(page, standalone ? "/standalone" : "");
      await send(page, "刷新继续回答");
      await chat.connected();
      const first = "这是正在生成的回答🙂。".repeat(80);
      chat.delta(first);
      await expect.poll(async () => (await page.locator("article").last().textContent())?.length ?? 0).toBeGreaterThan(30);
      const visible = (await page.locator("article").last().textContent())!;
      expect(visible.length).toBeLessThan(first.length);
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
      await expect.poll(async () => (await page.locator("article").last().textContent())?.length ?? 0).toBeGreaterThanOrEqual(visible.length);
      const restored = (await page.locator("article").last().textContent())!;
      expect((first + extra).startsWith(restored)).toBe(true);
      expect(restored.length).toBeLessThan(first.length + extra.length);
      if (!completedDuringReload) {
        await expect.poll(() => chat.requests.streams).toBe(2);
        await chat.connected();
        expect(chat.streamCursors.at(-1)).toBe(snapshotCursor);
        chat.delta("最后一段"); chat.finish();
      }
      await page.clock.runFor(Math.ceil((first.length + extra.length + 5) / 45 * 1000) + 1000);
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
