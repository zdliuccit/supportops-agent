import type { PaginatedListResponse, RoutingConfigResolutionView, RoutingConfigResolveRequest, RoutingPolicyVersionView } from "@/types";

export type AuthenticatedRead = <T>(operation: (token: string) => Promise<T>) => Promise<T>;
export const policyLabels = { candidate: "候选", expired: "已过期", revoked: "永久撤销" };
export const reasonLabels: Record<string, string> = {
  REFERENCE_UNAVAILABLE: "固定引用不可用", ENDPOINT_DISABLED: "端点已停用", MODEL_ARCHIVED: "模型已归档",
  CREDENTIAL_UNAVAILABLE: "凭据不可用", CREDENTIAL_REVISION_CHANGED: "凭据修订已变化", CREDENTIAL_REVOKED: "凭据已撤销",
  CREDENTIAL_PROVIDER_UNAVAILABLE: "凭据提供者不可用", REQUEST_DIGEST_MISMATCH: "模型请求已变化",
  CAPABILITY_DIGEST_MISMATCH: "能力声明已变化", PRICING_DIGEST_MISMATCH: "价格已变化", LATEST_PROBE_UNVERIFIED: "最新能力探测未确认",
  CAPABILITY_UNSUPPORTED: "所需能力不支持", CAPABILITY_UNCONFIRMED: "所需能力缺证明", CONTEXT_LIMIT_UNKNOWN: "上下文上限未知",
  CONTEXT_LIMIT_EXCEEDED: "输入与输出预留超过上下文", OUTPUT_LIMIT_UNKNOWN: "输出上限未知", OUTPUT_LIMIT_EXCEEDED: "输出预留超限",
  DATA_BOUNDARY_UNCONFIRMED: "缺少独立数据范围批准", DATA_BOUNDARY_MISMATCH: "数据范围不匹配", DATA_BOUNDARY_EXPIRED: "数据批准已过期",
  DATA_BOUNDARY_REVOKED: "数据批准已撤销", DATA_REGION_NOT_ALLOWED: "处理地域不在允许范围", DATA_CLASSIFICATION_NOT_ALLOWED: "数据分类未获准",
  USD_PRICING_UNKNOWN: "美元价格未确认", CALL_COST_EXCEEDED: "估价超过费用上限", POLICY_NOT_USABLE: "策略已过期或撤销",
  POLICY_SCOPE_MISMATCH: "用途、地域或数据分类不匹配", CANDIDATES_EXCLUDED: "完整候选集合未满足要求",
  CANDIDATE_CONFIG_INVALID: "候选不兼容当前配置", FIXED_MODEL_UNAVAILABLE: "原固定模型不可用",
  GENERATION_OPTION_UNCONFIRMED: "生成选项未确认", REFERENCES_CHANGED: "原引用已变化", PLATFORM_LIMIT_EXCEEDED: "超过平台限制",
};
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const uuid = (value: unknown): value is string => typeof value === "string" && uuidPattern.test(value);
const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const date = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
const labels = (value: unknown): value is string[] => Array.isArray(value) && value.length > 0 && value.length <= 16
  && value.every((item) => typeof item === "string" && /^[a-z][a-z0-9_-]{0,63}$/.test(item)) && new Set(value).size === value.length;
const fail = (): never => { throw new Error("路由响应契约或固定范围无法确认，请刷新后重新预览。"); };

/** 未知状态、执行开关或候选集合不进入页面预览。 */
export function requirePolicy(value: unknown): RoutingPolicyVersionView {
  if (!object(value) || !object(value.record) || !object(value.record.definition)) return fail();
  const record = value.record, definition = value.record.definition;
  if (record.schema_version !== "routing_policy_version_record_v1" || !uuid(record.version_id) || !uuid(record.tenant_id)
    || !uuid(record.policy_id) || !uuid(record.created_by) || !Number.isSafeInteger(record.revision) || Number(record.revision) < 1
    || !date(record.created_at) || !date(definition.valid_until) || !digest(value.policy_digest)
    || !["candidate", "expired", "revoked"].includes(String(value.status)) || value.candidate_validation_status !== "not_assessed"
    || value.approvals_verified !== false || value.routing_enabled !== false || value.execution_permitted !== false
    || definition.schema_version !== "model_routing_policy_v1" || definition.selection_rule !== "first_eligible_priority_v1"
    || !labels(definition.allowed_purposes) || !labels(definition.allowed_regions) || !labels(definition.allowed_data_classifications)
    || !uuid(definition.primary_candidate_id) || !Array.isArray(definition.candidates)
    || definition.candidates.length < 1 || definition.candidates.length > 8
    || !["go_no_go_reference_digest", "data_boundary_approval_digest", "evaluation_reference_digest", "fallback_policy_digest", "budget_policy_digest"]
      .every((key) => digest(definition[key]))) return fail();
  if (record.revision === 1 ? record.previous_version_id !== null || record.previous_policy_digest !== null
    : !uuid(record.previous_version_id) || !digest(record.previous_policy_digest)) return fail();
  const ids = new Set<string>(), models = new Set<string>(), priorities = new Set<number>();
  let primaryPriority = Infinity;
  for (const candidate of definition.candidates) {
    if (!object(candidate) || ![candidate.candidate_id, candidate.endpoint_id, candidate.endpoint_model_id, candidate.model_version_id].every(uuid)
      || !Number.isSafeInteger(candidate.credential_revision) || Number(candidate.credential_revision) < 1
      || !Number.isSafeInteger(candidate.priority) || Number(candidate.priority) < 0 || Number(candidate.priority) > 7
      || ![candidate.model_request_digest, candidate.capability_digest, candidate.pricing_digest, candidate.data_boundary_digest].every(digest)) return fail();
    const id = String(candidate.candidate_id), model = String(candidate.model_version_id), priority = Number(candidate.priority);
    if (ids.has(id) || models.has(model) || priorities.has(priority)) return fail();
    ids.add(id); models.add(model); priorities.add(priority);
    if (id === definition.primary_candidate_id) primaryPriority = priority;
  }
  if (primaryPriority !== Math.min(...priorities)) return fail();
  if (value.status === "revoked") {
    if (!object(value.revocation) || !digest(value.revocation_digest) || value.revocation.schema_version !== "routing_policy_revocation_v1"
      || value.revocation.tenant_id !== record.tenant_id || value.revocation.policy_id !== record.policy_id
      || value.revocation.version_id !== record.version_id || value.revocation.policy_digest !== value.policy_digest
      || !uuid(value.revocation.revocation_id) || !uuid(value.revocation.revoked_by) || !date(value.revocation.revoked_at)
      || !["operator_disabled", "policy_replaced", "go_no_go_withdrawn", "data_boundary_withdrawn", "evaluation_withdrawn"].includes(String(value.revocation.reason))) return fail();
  } else if (value.revocation !== null || value.revocation_digest !== null) return fail();
  return value as unknown as RoutingPolicyVersionView;
}

export function requirePolicyPage(value: PaginatedListResponse<RoutingPolicyVersionView>, page: number, pageSize: number, tenantId: string) {
  if (!object(value) || !Array.isArray(value.items) || value.items.length > pageSize || value.page !== page || value.page_size !== pageSize
    || !Number.isSafeInteger(value.total) || value.total < 0 || value.pages !== Math.ceil(value.total / pageSize)) return fail();
  const items = value.items.map(requirePolicy);
  if (new Set(items.map((row) => row.record.version_id)).size !== items.length || items.some((row) => row.record.tenant_id !== tenantId)) return fail();
  return { ...value, items };
}

export function requireResolution(value: RoutingConfigResolutionView, policy: RoutingPolicyVersionView, request: RoutingConfigResolveRequest) {
  if (!object(value) || !object(value.resolution) || !digest(value.resolution_digest)) return fail();
  const result = value.resolution;
  if (result.schema_version !== "routing_config_resolution_v1" || result.mode !== "versioned_routing_v1"
    || result.tenant_id !== policy.record.tenant_id || result.agent_id !== request.agent_id
    || result.base_agent_version_id !== request.base_agent_version_id || result.base_config_digest !== request.expected_base_config_digest
    || result.policy_version_id !== policy.record.version_id || result.policy_digest !== policy.policy_digest
    || !digest(result.config_digest) || !digest(result.candidate_set_digest) || !date(result.observed_at)
    || !Array.isArray(result.issues) || result.issues.length > 64 || !["resolved", "blocked"].includes(result.status)
    || ![result.approvals_verified, result.publication_permitted, result.routing_enabled, result.budget_reserved, result.execution_permitted].every((flag) => flag === false)
    || !object(result.resolved_tool_versions) || Object.keys(result.resolved_tool_versions).length > 30
    || Object.values(result.resolved_tool_versions).some((version) => !Number.isSafeInteger(version) || Number(version) < 1)) return fail();
  const candidates = new Map(policy.record.definition.candidates.map((candidate) => [candidate.candidate_id, candidate]));
  for (const issue of result.issues) {
    if (!object(issue) || typeof issue.code !== "string" || !Object.hasOwn(reasonLabels, issue.code)
      || issue.candidate_id !== null && !candidates.has(String(issue.candidate_id))) return fail();
  }
  const report = result.candidate_filter;
  if (report !== null) {
    if (!object(report) || report.schema_version !== "routing_candidate_filter_v1" || report.tenant_id !== result.tenant_id
      || report.agent_version_id !== request.base_agent_version_id || report.policy_version_id !== result.policy_version_id
      || report.policy_digest !== result.policy_digest || report.candidate_set_digest !== result.candidate_set_digest
      || !digest(report.requirements_digest) || !date(report.observed_at) || !Array.isArray(report.candidates)
      || report.candidates.length !== candidates.size || ![report.routing_enabled, report.budget_reserved, report.execution_permitted].every((flag) => flag === false)) return fail();
    const seen = new Set<string>();
    for (const row of report.candidates) {
      if (!object(row)) return fail();
      const original = candidates.get(String(row.candidate_id));
      if (!original || seen.has(original.candidate_id) || row.model_version_id !== original.model_version_id || row.priority !== original.priority
        || !["eligible", "excluded"].includes(String(row.status)) || row.execution_permitted !== false
        || !Array.isArray(row.reasons) || row.reasons.length > 32 || row.reasons.some((reason) => typeof reason !== "string" || !Object.hasOwn(reasonLabels, reason))
        || (row.status === "eligible" ? row.reasons.length !== 0 : row.reasons.length === 0)
        || row.status === "eligible" && (row.estimated_cost_microusd === null || row.latest_probe_id === null)
        || row.latest_probe_id !== null && !uuid(row.latest_probe_id)
        || row.estimated_cost_microusd !== null && (typeof row.estimated_cost_microusd !== "number" || !Number.isInteger(row.estimated_cost_microusd) || row.estimated_cost_microusd < 0)) return fail();
      seen.add(original.candidate_id);
    }
  }
  if (result.status === "blocked") {
    if (!result.issues.length || result.resolved_models !== null || Object.keys(result.resolved_tool_versions).length) return fail();
  } else {
    if (result.issues.length || !report || report.candidates.some((row) => row.status !== "eligible")
      || !Array.isArray(result.resolved_models) || result.resolved_models.length !== candidates.size) return fail();
    const seen = new Set<string>();
    for (const row of result.resolved_models) {
      if (!object(row)) return fail();
      const original = candidates.get(String(row.candidate_id));
      if (!original || seen.has(original.candidate_id) || row.endpoint_id !== original.endpoint_id || row.endpoint_model_id !== original.endpoint_model_id
        || row.model_version_id !== original.model_version_id || row.credential_revision_at_resolution !== original.credential_revision
        || row.model_request_digest !== original.model_request_digest || row.capability_digest !== original.capability_digest
        || row.pricing_digest !== original.pricing_digest || !uuid(row.latest_probe_id)
        || row.latest_probe_id !== report.candidates.find((candidate) => candidate.candidate_id === row.candidate_id)?.latest_probe_id
        || row.credential_binding !== "recheck_before_run_and_dispatch") return fail();
      seen.add(original.candidate_id);
    }
  }
  return value;
}

export function formatCost(value: number | null): string {
  if (value === null) return "价格未确认";
  if (!Number.isSafeInteger(value) || value < 0) return "超出客户端精确显示范围";
  const amount = BigInt(value);
  return `$${amount / 1_000_000n}.${String(amount % 1_000_000n).padStart(6, "0")}`;
}
