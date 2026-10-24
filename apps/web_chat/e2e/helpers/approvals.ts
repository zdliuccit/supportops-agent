import type { Page } from "@playwright/test";
import type { ApprovalDetail, ApprovalStatus, BusinessActionDetail } from "../../src/types";

export const approvalTime = "2026-10-24T00:00:00Z";
export const approvalLabels: Record<ApprovalStatus, string> = {
  pending: "等待指定人员审批", approved: "已批准，尚未消费", rejected: "已拒绝，未执行",
  expired: "已过期", cancelled: "已取消", invalidated: "授权范围变化，批准失效", consumed: "批准已一次性消费",
};

export function actionFixture(overrides: Partial<BusinessActionDetail> = {}): BusinessActionDetail {
  return {
    id: "approval-action", run_id: "evidence-run", tool_id: "mock_r4_operation", status: "waiting_approval",
    data_environment: "mock", risk_level: "R4", approval_required: true, implementation_version: 1,
    catalog_version: 1, effective_catalog_version: 1, execution_catalog_version: null, execution_run_id: null,
    parameters: { schema_version: "1", title: "冻结 Mock 操作", summary: "最小支持摘要", issue_key: "approval-issue" },
    parameters_digest: "a".repeat(64), generation: 0, reconciliation_count: 0, reconciliation_limit: 3,
    external_id: null, error_code: null, created_at: approvalTime, finished_at: null, ...overrides,
  };
}

export function approvalFixture(overrides: Partial<ApprovalDetail> = {}): ApprovalDetail {
  const action = actionFixture();
  return {
    id: "approval-1", action_id: action.id, run_id: action.run_id, applicant_id: "applicant-1", approver_id: "user-1",
    tool_id: action.tool_id, implementation_version: 1, catalog_version: 1, parameters_digest: action.parameters_digest,
    status: "pending", revision: 4, expires_at: "2099-10-24T01:00:00Z", created_at: approvalTime,
    finished_at: null, data_environment: "mock", parameters: action.parameters, risk_level: "R4",
    action_status: "waiting_approval", decision_reason: null, can_decide: true, can_cancel: false, ...overrides,
  };
}

export function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

/** 仅模拟 UI 契约；不作为后端审批授权、一次消费或副作用验收。 */
export async function installApprovalApi(page: Page, initial = [approvalFixture()]) {
  const items = initial;
  const calls = { lists: [] as URL[], details: [] as string[], writes: [] as { path: string; body: Record<string, unknown> }[] };
  await page.route(/\/v1\/approvals(?:\/|\?|$)/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/v1/approvals") {
      calls.lists.push(url);
      const filtered = items.filter((item) => (!url.searchParams.has("state") || item.status === url.searchParams.get("state"))
        && (!url.searchParams.has("action_id") || item.action_id === url.searchParams.get("action_id")));
      const current = Number(url.searchParams.get("page"));
      const size = Number(url.searchParams.get("page_size"));
      await route.fulfill({ json: { items: filtered.slice((current - 1) * size, current * size), total: filtered.length, page: current, page_size: size, pages: Math.ceil(filtered.length / size) } });
      return;
    }
    const item = items.find((entry) => url.pathname === `/v1/approvals/${entry.id}` || url.pathname.startsWith(`/v1/approvals/${entry.id}/`));
    if (!item) { await route.fulfill({ status: 404, json: { error: { message: "审批不存在或无权读取" } } }); return; }
    if (request.method() === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      calls.writes.push({ path: url.pathname, body });
      item.status = url.pathname.endsWith("/cancel") ? "cancelled" : body.decision as ApprovalStatus;
      item.revision += 1; item.can_decide = false; item.can_cancel = false;
      item.decision_reason = typeof body.reason === "string" ? body.reason : null;
      item.action_status = item.status === "approved" ? "queued" : "cancelled";
    } else calls.details.push(item.id);
    await route.fulfill({ json: item });
  });
  return { items, calls };
}
