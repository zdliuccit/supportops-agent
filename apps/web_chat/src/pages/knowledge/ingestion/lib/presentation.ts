import type { KnowledgeIngestionJob, KnowledgeIngestionSourceReadiness, KnowledgeIngestionStatus } from "@/types";

export const ingestionStatusLabels: Record<KnowledgeIngestionStatus, string> = {
  queued: "排队中", running: "处理中", retry_wait: "等待有限重试",
  completed: "处理完成", failed: "处理失败", cancelled: "已取消",
};

const failureLabels: Record<string, string> = {
  source_unapproved: "来源尚未批准", authorization_changed: "授权范围已变化",
  credential_unavailable: "来源凭据不可用", source_unavailable: "来源暂时不可用",
  rate_limited: "来源限流", cursor_invalid: "检查点不可用",
  source_version_conflict: "来源版本冲突，需人工复核", parse_rejected: "内容解析被拒绝",
  output_too_large: "内容超过大小限制", budget_exhausted: "累计预算已耗尽",
  commit_conflict: "提交范围或版本冲突", recovery_required: "任务已到期，需复核恢复条件",
};

export function isIngestionStatus(value: string): value is KnowledgeIngestionStatus {
  return Object.prototype.hasOwnProperty.call(ingestionStatusLabels, value);
}

export function ingestionContractSupported(job: KnowledgeIngestionJob): boolean {
  return job.schema_version === "knowledge_ingestion_job_view_v1"
    && job.integrity_status === "recorded"
    && job.automatic_publish_enabled === false
    && isIngestionStatus(job.status)
    && Number.isSafeInteger(job.revision) && (job.revision ?? 0) >= 1;
}

export function ingestionStatusLabel(job: KnowledgeIngestionJob): string {
  return ingestionContractSupported(job) && job.status !== "unknown"
    ? ingestionStatusLabels[job.status] : "状态不可确认";
}

export function ingestionFailureLabel(code: string | null): string {
  return code ? failureLabels[code] ?? "来源返回了尚不支持的失败码，请刷新或联系维护人员" : "—";
}

export function ingestionTime(value: string | null): string {
  if (!value) return "—";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? "时间不可确认" : time.toLocaleString();
}

export type IngestionCommand = {
  job: KnowledgeIngestionJob;
  kind: "cancel" | "expire" | "execute";
};

export function sourceReady(value: KnowledgeIngestionSourceReadiness | null): value is KnowledgeIngestionSourceReadiness & {
  source_id: string; scope_digest: string; object_count: number; valid_until: string;
} {
  return value?.schema_version === "knowledge_ingestion_source_readiness_v1" && value.available === true
    && typeof value.create_available === "boolean"
    && value.automatic_publish_enabled === false && typeof value.source_id === "string" && value.source_id.length > 0
    && typeof value.scope_digest === "string" && /^[0-9a-f]{64}$/.test(value.scope_digest)
    && Number.isSafeInteger(value.object_count) && (value.object_count ?? 0) > 0 && (value.object_count ?? 101) <= 100
    && typeof value.valid_until === "string" && Number.isFinite(Date.parse(value.valid_until))
    && typeof value.observed_at === "string" && Number.isFinite(Date.parse(value.observed_at))
    && Date.parse(value.valid_until) > Date.parse(value.observed_at) && Date.parse(value.valid_until) > Date.now();
}

export function canExecuteIngestion(job: KnowledgeIngestionJob, source: KnowledgeIngestionSourceReadiness | null): boolean {
  return sourceReady(source) && source.source_id === job.source_id && source.scope_digest === job.scope_digest
    && ingestionContractSupported(job)
    && (job.status === "queued" || job.status === "retry_wait")
    && Number.isSafeInteger(job.attempts) && Number.isSafeInteger(job.max_attempts)
    && (job.attempts ?? 0) < (job.max_attempts ?? 0) && job.committed_pages === 0
    && job.deadline_at !== null && Date.parse(job.deadline_at) > Date.now()
    && (job.retry_at === null || Date.parse(job.retry_at) <= Date.now());
}
