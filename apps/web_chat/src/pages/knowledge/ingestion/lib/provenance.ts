import type { KnowledgeIngestionProvenance } from "@/types";

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

const uuid = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);

/** 未知版本、字段、部分记录或超预算均隐藏；锚点标题与正文同样视为不可信文本。 */
export function provenanceSupported(value: unknown): value is KnowledgeIngestionProvenance {
  if (!object(value) || !exact(value, ["schema_version", "job_id", "materialization_id", "status", "record",
    "source_files_read", "automatic_publish_enabled"]) || value.schema_version !== "knowledge_ingestion_provenance_v1"
    || !uuid(value.job_id) || !uuid(value.materialization_id) || value.source_files_read !== false
    || value.automatic_publish_enabled !== false) return false;
  if (value.status !== "available") return value.record === null && typeof value.status === "string"
    && ["content_unavailable", "target_unavailable", "newer_candidate", "candidate_withdrawn", "scope_changed",
      "integrity_failed", "too_large"].includes(value.status);
  const record = value.record;
  if (!object(record) || !exact(record, ["source_id", "document_id", "knowledge_version_id", "version_number",
    "materialized_at", "object_key", "scope_digest", "source_contract_digest", "adapter_manifest_digest",
    "parser_manifest_digest", "source_revision_digest", "raw_content_digest", "parsed_content_digest",
    "anchor_origin", "section_anchors"]) || !uuid(record.source_id) || !uuid(record.document_id)
    || !uuid(record.knowledge_version_id) || typeof record.version_number !== "number"
    || !Number.isSafeInteger(record.version_number) || record.version_number < 1
    || typeof record.materialized_at !== "string" || !Number.isFinite(Date.parse(record.materialized_at))
    || !/(?:Z|[+-]\d{2}:\d{2})$/.test(record.materialized_at)
    || record.anchor_origin !== "stored_candidate" || !Array.isArray(record.section_anchors)
    || record.section_anchors.length > 1000) return false;
  if (![record.object_key, record.scope_digest, record.source_contract_digest, record.adapter_manifest_digest,
    record.parser_manifest_digest, record.raw_content_digest, record.parsed_content_digest].every(digest)
    || (record.source_revision_digest !== null && !digest(record.source_revision_digest))) return false;
  const ids = new Set<string>();
  for (const anchor of record.section_anchors) {
    if (!object(anchor) || !exact(anchor, ["id", "title", "level"]) || typeof anchor.id !== "string"
      || anchor.id.length < 1 || anchor.id.length > 65536 || ids.has(anchor.id) || typeof anchor.title !== "string"
      || anchor.title.length > 65536 || typeof anchor.level !== "string" || !/^[1-6]$/.test(anchor.level)) return false;
    ids.add(anchor.id);
  }
  return new TextEncoder().encode(JSON.stringify(value)).length <= 65536;
}

export const provenanceUnavailableLabels: Record<Exclude<KnowledgeIngestionProvenance["status"], "available">, string> = {
  content_unavailable: "当前没有有效发布版本或知识权限不允许读取章节信息。管理员和本人任务身份不授予正文权限。",
  target_unavailable: "关联文档或候选当前不可用。",
  newer_candidate: "已有更新候选或原候选已被取代，请复核最新知识版本。",
  candidate_withdrawn: "本次候选已撤回，来源追溯信息已隐藏。",
  scope_changed: "当前负责人或知识权限已偏离原导入映射，请独立复核范围。",
  integrity_failed: "任务回执、候选摘要或章节记录无法确认，已隐藏来源追溯信息。",
  too_large: "完整来源追溯信息超过 64 KiB 预算，未返回部分摘要或章节。",
};
