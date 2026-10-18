import type { KnowledgeIngestionGovernance, KnowledgeIngestionWithdrawalReason } from "@/types";

export const withdrawalReasons: Record<KnowledgeIngestionWithdrawalReason, string> = {
  source_scope_changed: "来源或权限范围需要重新确认",
  manual_resolution_required: "交由人工重新整理候选",
  no_longer_needed: "本次候选已不再需要",
};

export function isWithdrawalReason(value: string): value is KnowledgeIngestionWithdrawalReason {
  return value === "source_scope_changed" || value === "manual_resolution_required" || value === "no_longer_needed";
}

export function withdrawalAvailable(view: KnowledgeIngestionGovernance | null): view is EditableIngestionGovernance {
  return editableGovernance(view) && view.withdraw_available === true;
}

export type EditableIngestionGovernance = KnowledgeIngestionGovernance & {
  document_id: string; owner_user_id: string; governance_digest: string;
};

export function editableGovernance(view: KnowledgeIngestionGovernance | null): view is EditableIngestionGovernance {
  if (!view) return false;
  const observed = new Date(view.observed_at).getTime();
  const due = view.review_due_at === null ? null : new Date(view.review_due_at).getTime();
  const timing = due === null ? "unscheduled" : due <= observed ? "due" : "scheduled";
  return Boolean(view.schema_version === "knowledge_ingestion_governance_v1"
    && view.automatic_publish_enabled === false && view.status === "available" && view.update_available === true
    && view.document_id && view.owner_user_id && typeof view.owner_active === "boolean"
    && view.governance_digest && /^[0-9a-f]{64}$/.test(view.governance_digest)
    && ["draft", "in_review", "published"].includes(view.candidate_status ?? "")
    && [null, "pending", "approved", "rejected"].includes(view.review_decision)
    && view.review_timing === timing && Number.isFinite(observed) && (due === null || Number.isFinite(due)));
}

export function localReviewInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const pad = (number: number, size = 2) => String(number).padStart(size, "0");
  return `${pad(date.getFullYear(), 4)}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** 控件用本地时间，提交用带时区ISO；未改动时保留原始精度。 */
export function reviewInputPayload(value: string, snapshot: EditableIngestionGovernance): string | null {
  if (!value) return null;
  const canonical = value.length === 16 ? `${value}:00` : value;
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(canonical)) throw new Error("请输入有效的复核日期和时间");
  const date = new Date(canonical);
  if (!Number.isFinite(date.getTime()) || date.getFullYear() < 1 || localReviewInput(date.toISOString()) !== canonical) {
    throw new Error("复核时间无效或处于本地时区不存在的时刻");
  }
  return canonical === localReviewInput(snapshot.review_due_at) ? snapshot.review_due_at : date.toISOString();
}

export function governanceFormErrors(owner: string, due: string, snapshot: EditableIngestionGovernance) {
  let ownerError = "", dueError = "";
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(owner)) ownerError = "请选择文档负责人";
  else if (owner === snapshot.owner_user_id && !snapshot.owner_active) ownerError = "当前负责人已停用，请选择有效用户";
  try { reviewInputPayload(due, snapshot); } catch (cause) { dueError = cause instanceof Error ? cause.message : "复核时间无效"; }
  return { owner: ownerError, due: dueError };
}
