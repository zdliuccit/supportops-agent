import type { KnowledgeIngestionCreationStatus, KnowledgeIngestionJob } from "@/types";
import { ingestionContractSupported } from "./presentation";

/** 仅在本标签页保存本人尚未确认的请求定位，不保存令牌、路径、正文或批准策略。 */
export interface PendingIngestionCreation {
  requestKey: string;
  sourceId: string;
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface CreationRequestTarget {
  requestKey: string;
  sourceId?: string;
}

/** 手动查询只接受原UUID；规范大小写和粘贴空白，不接受路径、URL或任务ID推断。 */
export function normalizeCreationRequestKey(value: string): string | null {
  const key = value.trim().toLowerCase();
  return uuidPattern.test(key) ? key : null;
}

export function validateCreatedJob(job: KnowledgeIngestionJob, request: CreationRequestTarget): void {
  if (!job || typeof job !== "object" || typeof job.id !== "string" || !uuidPattern.test(job.id)
    || !ingestionContractSupported(job) || typeof job.source_id !== "string" || !uuidPattern.test(job.source_id)
    || job.creation_request_key !== request.requestKey
    || (request.sourceId !== undefined && job.source_id !== request.sourceId)) {
    throw new Error("创建回执不可确认，请按原请求标识查询。");
  }
}

export function creationRequestOutcome(value: KnowledgeIngestionCreationStatus, request: CreationRequestTarget):
  { status: "recorded"; job: KnowledgeIngestionJob } | { status: "stopped" } | { status: "unobserved" } {
  if (!value || typeof value !== "object" || value.schema_version !== "knowledge_ingestion_creation_status_v1" || value.request_key !== request.requestKey
    || typeof value.observed_at !== "string" || !Number.isFinite(Date.parse(value.observed_at))) {
    throw new Error("原请求状态回执不可确认，请保留请求标识重新查询。");
  }
  if (value.status === "recorded" && value.job !== null && value.stopped_at === null) {
    validateCreatedJob(value.job, request);
    return { status: "recorded", job: value.job };
  }
  if (value.status === "stopped" && value.job === null && typeof value.stopped_at === "string"
    && Number.isFinite(Date.parse(value.stopped_at))) return { status: "stopped" };
  if (value.status === "unobserved" && value.job === null && value.stopped_at === null) return { status: "unobserved" };
  throw new Error("原请求状态或停止事实不可确认，请保留请求标识重新查询。");
}

function storageKey(tenantId: string, userId: string): string {
  if (!uuidPattern.test(tenantId) || !uuidPattern.test(userId)) throw new Error("当前身份范围不可确认，请重新登录后查询任务历史。");
  return `supportops.knowledge-ingestion.pending-create.v1:${tenantId}:${userId}`;
}

export function readPendingCreation(tenantId: string, userId: string): PendingIngestionCreation | null {
  try {
    const raw = sessionStorage.getItem(storageKey(tenantId, userId));
    if (raw === null) return null;
    if (raw.length > 512) throw new Error("record size");
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("record type");
    const record = value as Record<string, unknown>;
    if (Object.keys(record).length !== 3 || record.schema_version !== "knowledge_ingestion_pending_create_v1"
      || typeof record.request_key !== "string" || !uuidPattern.test(record.request_key)
      || typeof record.source_id !== "string" || !uuidPattern.test(record.source_id)) throw new Error("record fields");
    return { requestKey: record.request_key, sourceId: record.source_id };
  } catch {
    // 损坏记录不能被当作不存在或静默覆盖，避免用新请求掩盖未知创建。
    throw new Error("本标签页的待确认请求记录无法读取，请先从任务历史核对；当前不发送新的创建请求。");
  }
}

export function reservePendingCreation(tenantId: string, userId: string, request: PendingIngestionCreation): void {
  if (readPendingCreation(tenantId, userId) !== null) throw new Error("已有待确认创建，请刷新后查询原请求。");
  if (!uuidPattern.test(request.requestKey) || !uuidPattern.test(request.sourceId)) throw new Error("创建请求定位无效，未发送请求。");
  try {
    sessionStorage.setItem(storageKey(tenantId, userId), JSON.stringify({
      schema_version: "knowledge_ingestion_pending_create_v1", request_key: request.requestKey, source_id: request.sourceId,
    }));
    const recorded = readPendingCreation(tenantId, userId);
    if (recorded === null || recorded.requestKey !== request.requestKey || recorded.sourceId !== request.sourceId) throw new Error("record mismatch");
  } catch {
    throw new Error("无法保存本次请求标识，未发送创建请求。请恢复会话存储后刷新并核对待确认记录。");
  }
}

export function clearConfirmedCreation(tenantId: string, userId: string, request: PendingIngestionCreation): void {
  const recorded = readPendingCreation(tenantId, userId);
  if (recorded !== null && (recorded.requestKey !== request.requestKey || recorded.sourceId !== request.sourceId)) {
    throw new Error("待确认记录已变化，已保留新的记录，请刷新核对。");
  }
  try {
    sessionStorage.removeItem(storageKey(tenantId, userId));
    if (readPendingCreation(tenantId, userId) !== null) throw new Error("record retained");
  } catch {
    throw new Error("任务已确认，但本标签页的请求定位尚未清除；请刷新后再次查询原请求，勿重新创建。");
  }
}
