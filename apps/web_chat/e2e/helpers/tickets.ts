import type { Page } from "@playwright/test";
import type { EscalationPackageDetail, MockTicket, MockTicketCommand, MockTicketCommandKind } from "../../src/types";

export const ticketTime = "2026-10-24T00:00:00Z";
export function ticketFixture(overrides: Partial<MockTicket> = {}): MockTicket {
  return { id: "ticket-1", external_id: "MOCK-001", title: "429 排查工单", summary: "当前获准的工单摘要", status: "open", version: 4,
    assigned_to: null, owner_name: "申请者", handler_name: null, takeover_active: false, can_claim: true, can_update: false,
    source_action_id: "approval-action", can_read_context: true, created_at: ticketTime, updated_at: ticketTime, data_environment: "mock", ...overrides };
}
export function packageFixture(overrides: Partial<EscalationPackageDetail> = {}): EscalationPackageDetail {
  return { id: "package-1", action_id: "approval-action", run_id: "evidence-run", attempt_id: "attempt-1", created_at: ticketTime, data_environment: "mock",
    payload: { schema_version: "1", data_environment: "mock", question: "获准的原始排查问题", run_id: "evidence-run", attempt_id: "attempt-1", trace_id: "trace-1",
      observed_facts: [], confirmed_causes: [], unconfirmed_candidates: [], sources: [], steps: [], evidence_ids: [], unresolved_items: ["需要人工确认资源范围"], reason_codes: ["NO_EVIDENCE"], captured_at: ticketTime }, ...overrides };
}
type CommandInput = { kind: MockTicketCommandKind; body: string; expected_version: number; idempotency_key: string };

/** 仅提供浏览器隔离契约，不代替 W17.11 的真实权限、并发与目标验收。 */
export async function installTicketApi(page: Page) {
  const items = [ticketFixture()];
  const commands: MockTicketCommand[] = [];
  const packages = [packageFixture()];
  const calls = { lists: [] as URL[], records: [] as URL[], packages: [] as URL[], details: [] as string[], writes: [] as CommandInput[] };
  const saved = new Map<string, MockTicketCommand>();
  await page.route(/\/v1\/mock-tickets(?:\/|\?|$)/, async (route) => {
    const request = route.request(); const url = new URL(request.url());
    function paginated<T>(rows: T[]) {
      const current = Number(url.searchParams.get("page") ?? 1), size = Number(url.searchParams.get("page_size") ?? 20);
      return { items: rows.slice((current - 1) * size, current * size), total: rows.length, page: current, page_size: size, pages: Math.ceil(rows.length / size) };
    }
    if (url.pathname === "/v1/mock-tickets") {
      calls.lists.push(url);
      await route.fulfill({ json: paginated(items.filter((item) => (!url.searchParams.has("state") || item.status === url.searchParams.get("state")) && item.title.includes(url.searchParams.get("keywords") ?? ""))) }); return;
    }
    const item = items.find((entry) => url.pathname === `/v1/mock-tickets/${entry.external_id}` || url.pathname.startsWith(`/v1/mock-tickets/${entry.external_id}/`));
    if (!item) { await route.fulfill({ status: 404, json: { error: { message: "工单不存在或无权读取" } } }); return; }
    if (url.pathname.endsWith("/commands")) {
      if (request.method() === "POST") {
        const input = request.postDataJSON() as CommandInput; calls.writes.push(input);
        let record = saved.get(input.idempotency_key);
        if (!record) {
          item.version += 1;
          if (input.kind === "claim") Object.assign(item, { status: "in_progress", assigned_to: "user-1", handler_name: "客服人员", takeover_active: true, can_claim: false, can_update: true });
          if (input.kind === "close") Object.assign(item, { status: "closed", takeover_active: false, can_claim: false, can_update: false });
          if (input.kind === "release") Object.assign(item, { status: "open", assigned_to: null, handler_name: null, takeover_active: false, can_claim: true, can_update: false });
          if (input.kind === "escalate") item.status = "escalated";
          record = { id: `command-${commands.length}`, kind: input.kind, body: input.body, actor_name: "客服人员", ticket_version: item.version, ticket_status: item.status, created_at: ticketTime, data_environment: "mock" };
          saved.set(input.idempotency_key, record); commands.unshift(record);
        }
        await route.fulfill({ json: record });
      } else { calls.records.push(url); await route.fulfill({ json: paginated(commands) }); }
    } else if (url.pathname.endsWith("/escalations")) { calls.packages.push(url); await route.fulfill({ json: paginated(packages) }); }
    else if (url.pathname.includes("/escalations/")) { await route.fulfill({ json: packages.find((entry) => url.pathname.endsWith(`/${entry.id}`)) }); }
    else { calls.details.push(item.external_id); await route.fulfill({ json: item }); }
  });
  return { items, commands, packages, calls };
}
