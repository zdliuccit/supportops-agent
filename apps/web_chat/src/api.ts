import type {
  ToolDiscoveryPolicy,
  ToolDiscoveryView,
  ToolConnectionView,
  ToolConnectionProbe,
  DiscoveredToolDescriptor,
  ErasureRequestInput,
  ErasureRequestSummary,
  ErasureRequestDetail,
  RetentionPolicyInput,
  RetentionPolicySummary,
  RetentionPolicyDetail,
  CompensationReadiness,
  CompensationSummary,
  CompensationDetail,
  AuthSession,
  DashboardQuery,
  DashboardRankings,
  ApprovalSummary,
  ApprovalDetail,
  ApprovalApprover,
  MockResourceGrant,
  EscalationPackageSummary,
  EscalationPackageDetail,
  MockTicket,
  MockTicketCommand,
  MockTicketCommandKind,
  Conversation,
  DiagnosticReport,
  BusinessActionDetail,
  ActionReceipt,
  ActionAuditSummary,
  RunSecurityEvent,
  RuntimeAlert,
  RuntimeAlertList,
  ModelCredentialRevision,
  ModelCredentialAudit,
  ConversationListResponse,
  ConversationSummary,
  ConversationUpdate,
  MessageAccepted,
  RunEvent,
  RunAttempt,
  RunDetail,
  HttpMcpDispatchSummary,
  Agent,
  AdminAgent,
  Identity,
  ModelEndpoint,
  ModelProviderPreset,
  ModelTestRun,
  ModelEndpointVersion,
  AgentAuditEvent,
  AgentConfig,
  AgentDraft,
  AgentGrant,
  AgentVersion,
  ReleaseReadiness,
  ReleaseEvaluationSummary,
  ReleaseRolloutSummary,
  ReleaseRolloutDetail,
  ReleaseRolloutCreate,
  ReleaseRolloutCommand,
  ReleaseRolloutEvent,
  LoginResponse,
  Company,
  CompanyUpdateInput,
  OrganizationUnit,
  OrganizationUnitCreateInput,
  OrganizationUnitUpdateInput,
  AdminUser,
  AdminUserCreateInput,
  AdminUserUpdateInput,
  PaginatedListResponse,
  ToolCatalogEntry,
  DashboardSummary,
  DashboardTimeseriesPoint,
  DashboardRun,
  DashboardError,
  DashboardErrorEvent,
  DashboardErrorEventDetail,
  DashboardTrace,
  SystemAgentStatus,
  KnowledgeDocument,
  KnowledgeSource,
  KnowledgeVersion,
  KnowledgeEvidenceDetail,
  KnowledgeIndexState,
  KnowledgeIndexJob,
  KnowledgeIngestionJob,
  KnowledgeIngestionSourceReadiness,
  KnowledgeIngestionCreationStatus,
  KnowledgeIngestionDraft,
  KnowledgeIngestionComparison,
  KnowledgeIngestionExecutionRequest,
  KnowledgeIngestionProvenance,
  KnowledgeIngestionGovernance,
  KnowledgeIngestionGovernanceInput,
  KnowledgeIngestionOwnerOption,
  KnowledgeIngestionWithdrawalInput,
  KnowledgeIngestionStatus,
} from "./types";

/** 浏览器端统一使用的 REST/SSE API 地址，可由 Vite 环境变量覆盖。 */
import { isRunStatus } from "./lib/runContract";
import type { AgentReleaseComparison } from "./types";

const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export function getKnowledgeEvidence(token: string, runId: string, evidenceId: string): Promise<KnowledgeEvidenceDetail> {
  return apiRequest<KnowledgeEvidenceDetail>(`/v1/runs/${runId}/evidence/${evidenceId}`, token, { cache: "no-store" });
}

export function listKnowledgeIngestionJobs(token: string, options: {
  page: number; pageSize: number; status?: KnowledgeIngestionStatus; sourceId?: string;
}): Promise<PaginatedListResponse<KnowledgeIngestionJob>> {
  const query = new URLSearchParams({ page: String(options.page), page_size: String(options.pageSize) });
  if (options.status) query.set("status", options.status);
  if (options.sourceId) query.set("source_id", options.sourceId);
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs?${query}`, token);
}

export function getKnowledgeIngestionJob(token: string, jobId: string): Promise<KnowledgeIngestionJob> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}`, token);
}

export function getKnowledgeIngestionSourceReadiness(token: string): Promise<KnowledgeIngestionSourceReadiness> {
  return apiRequest("/v1/admin/knowledge/ingestion-jobs/source-readiness", token);
}

export function createKnowledgeIngestionJob(token: string, scopeDigest: string, requestKey: string): Promise<KnowledgeIngestionJob> {
  return apiRequest("/v1/admin/knowledge/ingestion-jobs", token, { method: "POST",
    headers: { "Idempotency-Key": requestKey }, body: JSON.stringify({ expected_scope_digest: scopeDigest }) });
}

export function getKnowledgeIngestionJobByRequest(token: string, requestKey: string): Promise<KnowledgeIngestionJob> {
  const query = new URLSearchParams({ request_key: requestKey });
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/by-request?${query}`, token);
}

export function getKnowledgeIngestionCreationStatus(token: string, requestKey: string, signal?: AbortSignal): Promise<KnowledgeIngestionCreationStatus> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/creation-requests/${encodeURIComponent(requestKey)}`, token, { signal });
}

export function stopKnowledgeIngestionCreation(token: string, requestKey: string): Promise<KnowledgeIngestionCreationStatus> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/creation-requests/${encodeURIComponent(requestKey)}/stop`, token,
    { method: "POST", body: JSON.stringify({ confirm_stop: true }) });
}

export function executeKnowledgeIngestionJob(token: string, jobId: string, expectedRevision: number): Promise<KnowledgeIngestionJob> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/execute`, token,
    { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) });
}

export function cancelKnowledgeIngestionJob(token: string, jobId: string, expectedRevision: number): Promise<KnowledgeIngestionJob> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/cancel`, token,
    { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) });
}

function requireIngestionExecutionRequest(value: unknown, jobId: string): asserts value is KnowledgeIngestionExecutionRequest {
  const fail = () => { throw new ApiError("执行意图契约无法确认，请只查询原任务，勿重发。", 409,
    { code: "CLIENT_CONTRACT_UNSUPPORTED" }); };
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  const item = value as Record<string, unknown>;
  const keys = ["schema_version", "id", "job_id", "expected_job_revision", "status", "revision", "created_at",
    "expires_at", "claimed_at", "finished_at", "error_code", "automatic_retry_enabled", "automatic_publish_enabled"];
  const timestamp = (input: unknown): input is string => typeof input === "string"
    && /(?:Z|[+-]\d{2}:\d{2})$/.test(input) && Number.isFinite(Date.parse(input));
  const revisions: Record<string, number> = { queued: 1, claimed: 2, completed: 3, rejected: 2, unconfirmed: 3 };
  if (Object.keys(item).length !== keys.length || keys.some((key) => !Object.hasOwn(item, key))
    || item.schema_version !== "knowledge_ingestion_execution_request_v1" || item.job_id !== jobId
    || typeof item.id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.id)
    || typeof item.status !== "string" || !Object.hasOwn(revisions, item.status) || item.revision !== revisions[item.status]
    || typeof item.expected_job_revision !== "number" || !Number.isSafeInteger(item.expected_job_revision) || item.expected_job_revision < 1
    || !timestamp(item.created_at) || !timestamp(item.expires_at) || item.automatic_retry_enabled !== false
    || item.automatic_publish_enabled !== false) return fail();
  const claimed = ["claimed", "completed", "unconfirmed"].includes(item.status as string);
  const terminal = ["completed", "rejected", "unconfirmed"].includes(item.status as string);
  if ((claimed ? !timestamp(item.claimed_at) : item.claimed_at !== null)
    || (terminal ? !timestamp(item.finished_at) : item.finished_at !== null)
    || (["rejected", "unconfirmed"].includes(item.status as string)
      ? typeof item.error_code !== "string" || !["REQUEST_EXPIRED", "AUTHORIZATION_OR_SCOPE_CHANGED", "JOB_CHANGED", "EXECUTION_UNCONFIRMED"].includes(item.error_code)
      : item.error_code !== null)) return fail();
  const window = Date.parse(item.expires_at as string) - Date.parse(item.created_at as string);
  if (window <= 0 || window > 60000) fail();
}

export async function getKnowledgeIngestionExecutionRequest(token: string, jobId: string): Promise<KnowledgeIngestionExecutionRequest | null> {
  const value = await apiRequest<unknown>(`/v1/admin/knowledge/ingestion-jobs/${encodeURIComponent(jobId)}/execution-request`,
    token, { cache: "no-store" });
  if (value === null) return null;
  requireIngestionExecutionRequest(value, jobId);
  return value;
}

export async function submitKnowledgeIngestionExecutionRequest(token: string, jobId: string,
  expectedRevision: number): Promise<KnowledgeIngestionExecutionRequest> {
  const value = await apiRequest<unknown>(`/v1/admin/knowledge/ingestion-jobs/${encodeURIComponent(jobId)}/execution-request`,
    token, { method: "POST", cache: "no-store", body: JSON.stringify({ expected_revision: expectedRevision }) });
  requireIngestionExecutionRequest(value, jobId);
  if (value.expected_job_revision !== expectedRevision) throw new ApiError("执行意图修订不匹配，请查询原任务，勿重发。", 409,
    { code: "CLIENT_CONTRACT_UNSUPPORTED" });
  return value;
}

export function listKnowledgeIngestionDrafts(token: string, jobId: string, page: number, pageSize: number): Promise<PaginatedListResponse<KnowledgeIngestionDraft>> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/drafts?${query}`, token);
}

export function getKnowledgeIngestionComparison(token: string, jobId: string, materializationId: string): Promise<KnowledgeIngestionComparison> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/drafts/${materializationId}/comparison`, token);
}

export function getKnowledgeIngestionProvenance(token: string, jobId: string, materializationId: string): Promise<KnowledgeIngestionProvenance> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/drafts/${materializationId}/provenance`, token,
    { cache: "no-store" });
}

export function getKnowledgeIngestionGovernance(token: string, jobId: string, materializationId: string): Promise<KnowledgeIngestionGovernance> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/drafts/${materializationId}/governance`, token);
}

export function updateKnowledgeIngestionGovernance(token: string, jobId: string, materializationId: string,
  payload: KnowledgeIngestionGovernanceInput): Promise<KnowledgeIngestionGovernance> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/drafts/${materializationId}/governance`, token,
    { method: "PATCH", body: JSON.stringify(payload) });
}

export function listKnowledgeIngestionOwners(token: string, jobId: string, materializationId: string,
  keywords: string, pageSize: number): Promise<PaginatedListResponse<KnowledgeIngestionOwnerOption>> {
  const query = new URLSearchParams({ page: "1", page_size: String(pageSize), keywords });
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/drafts/${materializationId}/governance/owners?${query}`, token);
}

export function withdrawKnowledgeIngestionCandidate(token: string, jobId: string, materializationId: string,
  payload: KnowledgeIngestionWithdrawalInput): Promise<KnowledgeIngestionGovernance> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/drafts/${materializationId}/withdraw`, token,
    { method: "POST", body: JSON.stringify(payload) });
}

export function expireKnowledgeIngestionJob(token: string, jobId: string, expectedRevision: number): Promise<KnowledgeIngestionJob> {
  return apiRequest(`/v1/admin/knowledge/ingestion-jobs/${jobId}/expire`, token,
    { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) });
}

export function getKnowledgeIndex(token: string, documentId: string): Promise<KnowledgeIndexState> {
  return apiRequest<KnowledgeIndexState>(`/v1/admin/knowledge/documents/${documentId}/index`, token);
}

export function rebuildKnowledgeIndex(token: string, documentId: string): Promise<KnowledgeIndexState> {
  return apiRequest<KnowledgeIndexState>(`/v1/admin/knowledge/documents/${documentId}/index/rebuild`, token, { method: "POST" });
}

export function getKnowledgeIndexJobs(token: string, documentId: string, page: number, pageSize: number): Promise<PaginatedListResponse<KnowledgeIndexJob>> {
  return apiRequest<PaginatedListResponse<KnowledgeIndexJob>>(`/v1/admin/knowledge/index-jobs?document_id=${documentId}&page=${page}&page_size=${pageSize}`, token);
}

export function retryKnowledgeIndexJob(token: string, jobId: string, expectedAttempts: number): Promise<KnowledgeIndexJob> {
  return apiRequest<KnowledgeIndexJob>(`/v1/admin/knowledge/index-jobs/${jobId}/retry`, token, {
    method: "POST", body: JSON.stringify({ expected_attempts: expectedAttempts }),
  });
}

export class ApiError extends Error {
  /**
   * 表示服务端返回的非成功响应，并保留 HTTP 状态码和结构化错误详情。
   */
  constructor(
    message: string,
    readonly status: number,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiRequest<T>(path: string, token?: string, init?: RequestInit): Promise<T> {
  // 统一处理认证头、JSON 编解码和服务端标准错误结构，避免各页面行为分叉。
  const headers = new Headers(init?.headers);
  if (token !== undefined) headers.set("Authorization", `Bearer ${token}`);
  if (init?.body !== undefined) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as
      | { error?: Record<string, unknown> & { message?: string } }
      | null;
    throw new ApiError(
      body?.error?.message ?? `请求失败（${response.status}）`,
      response.status,
      body?.error ?? {},
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** 普通列表接口共用的分页查询参数。 */
export interface PaginationQuery {
  /** 页码，从 1 开始。 */
  page?: number;
  /** 每页数据数量，最大 100。 */
  pageSize?: number;
}

/** 将前端驼峰分页参数转换为后端统一查询参数。 */
function appendDashboardScope(query: URLSearchParams, scope: DashboardQuery, statusParameter = "status"): void {
  query.set("days", String(scope.days ?? 7));
  const fields = { start: scope.start, end: scope.end, agent_id: scope.agentId, user_id: scope.userId,
    organization_unit_id: scope.organizationUnitId, agent_version_id: scope.agentVersionId,
    model_endpoint_version_id: scope.modelEndpointVersionId, lifecycle: scope.lifecycle, health: scope.health,
    [statusParameter]: scope.status };
  Object.entries(fields).forEach(([key, value]) => { if (value) query.set(key, value); });
}

function appendPagination(query: URLSearchParams, pagination: PaginationQuery): void {
  if (pagination.page !== undefined) query.set("page", String(pagination.page));
  if (pagination.pageSize !== undefined) query.set("page_size", String(pagination.pageSize));
}

/** 使用系统用户邮箱与密码登录。 */
export function login(email: string, password: string): Promise<LoginResponse> {
  return apiRequest<LoginResponse>("/v1/auth/login", undefined, {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function getIdentity(token: string): Promise<Identity> {
  return apiRequest<Identity>("/v1/auth/me", token);
}

export function logoutSession(token: string): Promise<void> {
  return apiRequest("/v1/auth/logout", token, { method: "POST" });
}

export function listAuthSessions(token: string, query: PaginationQuery): Promise<PaginatedListResponse<AuthSession>> {
  const params = new URLSearchParams(); appendPagination(params, query);
  return apiRequest(`/v1/auth/sessions?${params}`, token);
}

export function listErasureRequests(token: string, pagination: PaginationQuery): Promise<PaginatedListResponse<ErasureRequestSummary>> {
  const params = new URLSearchParams(); appendPagination(params, pagination);
  return apiRequest(`/v1/account/erasure-requests?${params}`, token);
}

export function listRetentionPolicies(token: string, pagination: PaginationQuery): Promise<PaginatedListResponse<RetentionPolicySummary>> {
  const params = new URLSearchParams(); appendPagination(params, pagination);
  return apiRequest(`/v1/admin/data-governance/policies?${params}`, token);
}

export function createRetentionPolicy(token: string, payload: RetentionPolicyInput): Promise<RetentionPolicyDetail> {
  return apiRequest("/v1/admin/data-governance/policies", token, { method: "POST", body: JSON.stringify(payload) });
}

export function getRetentionPolicy(token: string, id: string): Promise<RetentionPolicyDetail> {
  return apiRequest(`/v1/admin/data-governance/policies/${id}`, token);
}

export function getCompensationReadiness(token: string, actionId: string, runId: string): Promise<CompensationReadiness> {
  return apiRequest(`/v1/actions/${actionId}/compensation-readiness?context_run_id=${encodeURIComponent(runId)}`, token);
}

export function createCompensationProposal(token: string, actionId: string, runId: string, scopeDigest: string): Promise<CompensationDetail> {
  return apiRequest(`/v1/actions/${actionId}/compensation-proposals`, token, { method: "POST",
    body: JSON.stringify({ context_run_id: runId, expected_scope_digest: scopeDigest }) });
}

export function listCompensationProposals(token: string, actionId: string, runId: string, pagination: PaginationQuery): Promise<PaginatedListResponse<CompensationSummary>> {
  const params = new URLSearchParams({ context_run_id: runId }); appendPagination(params, pagination);
  return apiRequest(`/v1/actions/${actionId}/compensation-proposals?${params}`, token);
}

export function getCompensationProposal(token: string, actionId: string, runId: string, id: string): Promise<CompensationDetail> {
  return apiRequest(`/v1/actions/${actionId}/compensation-proposals/${id}?context_run_id=${encodeURIComponent(runId)}`, token);
}

export function createErasureRequest(token: string, payload: ErasureRequestInput, key: string): Promise<ErasureRequestDetail> {
  return apiRequest("/v1/account/erasure-requests", token, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(payload) });
}

export function getErasureRequest(token: string, id: string): Promise<ErasureRequestDetail> {
  return apiRequest(`/v1/account/erasure-requests/${id}`, token);
}

export function commandErasureRequest(token: string, id: string, command: "preview" | "withdraw", revision: number): Promise<ErasureRequestDetail> {
  return apiRequest(`/v1/account/erasure-requests/${id}/${command}`, token, { method: "POST", body: JSON.stringify({ expected_revision: revision }) });
}

export function revokeAuthSession(token: string, sessionId: string): Promise<AuthSession> {
  return apiRequest(`/v1/auth/sessions/${sessionId}/revoke`, token, { method: "POST" });
}

export function revokeAllAuthSessions(token: string): Promise<void> {
  return apiRequest("/v1/auth/sessions/revoke-all", token, { method: "POST" });
}

/** 管理员分页查看当前租户的执行尝试。 */
export function listRunAttempts(token: string, runId: string, query: PaginationQuery = {}): Promise<PaginatedListResponse<RunAttempt>> {
  const params = new URLSearchParams();
  appendPagination(params, query);
  const suffix = params.size ? `?${params}` : "";
  return apiRequest(`/v1/admin/runs/${runId}/attempts${suffix}`, token);
}

/** 诊断后有限恢复；旧无 owner Run 必须明确确认旧执行者已停止。 */
export function recoverRun(token: string, runId: string, payload: { expected_generation: number; confirm_legacy_executor_stopped?: boolean; cancel?: boolean }): Promise<RunDetail> {
  return apiRequest(`/v1/admin/runs/${runId}/recover`, token, { method: "POST", body: JSON.stringify(payload) });
}

export function listKnowledgeDocuments(
  token: string,
  query: PaginationQuery & { status?: string; sourceId?: string; ownerUserId?: string; ownerName?: string; keywords?: string } = {},
): Promise<PaginatedListResponse<KnowledgeDocument>> {
  const params = new URLSearchParams();
  appendPagination(params, query);
  if (query.status) params.set("status", query.status);
  if (query.sourceId) params.set("source_id", query.sourceId);
  if (query.ownerUserId?.trim()) params.set("owner_user_id", query.ownerUserId.trim());
  if (query.ownerName?.trim()) params.set("owner_name", query.ownerName.trim());
  if (query.keywords?.trim()) params.set("keywords", query.keywords.trim());
  const suffix = params.size ? `?${params}` : "";
  return apiRequest<PaginatedListResponse<KnowledgeDocument>>(`/v1/admin/knowledge/documents${suffix}`, token);
}

export function listKnowledgeSources(
  token: string, query: PaginationQuery & { keywords?: string } = {},
): Promise<PaginatedListResponse<KnowledgeSource>> {
  const params = new URLSearchParams();
  appendPagination(params, query);
  if (query.keywords?.trim()) params.set("keywords", query.keywords.trim());
  const suffix = params.size ? `?${params}` : "";
  return apiRequest<PaginatedListResponse<KnowledgeSource>>(`/v1/admin/knowledge/sources${suffix}`, token);
}

export function getKnowledgeDocument(token: string, documentId: string): Promise<KnowledgeDocument> {
  return apiRequest<KnowledgeDocument>(`/v1/admin/knowledge/documents/${documentId}`, token);
}

export function getKnowledgeVersions(
  token: string, documentId: string, query: PaginationQuery = {},
): Promise<PaginatedListResponse<KnowledgeVersion>> {
  const params = new URLSearchParams();
  appendPagination(params, query);
  const suffix = params.size ? `?${params}` : "";
  return apiRequest<PaginatedListResponse<KnowledgeVersion>>(`/v1/admin/knowledge/documents/${documentId}/versions${suffix}`, token);
}

export function createKnowledgeVersion(
  token: string,
  documentId: string,
  payload: { title: string; content_markdown: string; expected_revision: number; change_summary: string },
): Promise<KnowledgeVersion> {
  return apiRequest<KnowledgeVersion>(`/v1/admin/knowledge/documents/${documentId}/versions`, token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function submitKnowledgeVersion(token: string, versionId: string, expectedReviewRevision?: number): Promise<KnowledgeVersion> {
  return apiRequest<KnowledgeVersion>(`/v1/admin/knowledge/versions/${versionId}/submit-review`, token, {
    method: "POST", body: expectedReviewRevision === undefined ? undefined : JSON.stringify({ expected_review_revision: expectedReviewRevision }),
  });
}

export function publishKnowledgeVersion(token: string, versionId: string, expectedReviewRevision?: number): Promise<KnowledgeVersion> {
  return apiRequest<KnowledgeVersion>(`/v1/admin/knowledge/versions/${versionId}/publish`, token, {
    method: "POST", body: expectedReviewRevision === undefined ? undefined : JSON.stringify({ expected_review_revision: expectedReviewRevision }),
  });
}

export function decideKnowledgeReview(token: string, versionId: string, payload: {
  decision: "approved" | "rejected"; expected_review_revision: number; expected_content_digest: string; comment: string;
}): Promise<KnowledgeVersion> {
  return apiRequest<KnowledgeVersion>(`/v1/admin/knowledge/versions/${versionId}/review`, token, { method: "POST", body: JSON.stringify(payload) });
}

export function retireKnowledgeVersion(token: string, versionId: string): Promise<KnowledgeVersion> {
  return apiRequest<KnowledgeVersion>(`/v1/admin/knowledge/versions/${versionId}/retire`, token, { method: "POST" });
}

export function rollbackKnowledgeVersion(token: string, documentId: string, versionId: string, expectedRevision: number): Promise<KnowledgeVersion> {
  return apiRequest<KnowledgeVersion>(`/v1/admin/knowledge/documents/${documentId}/rollback/${versionId}?expected_revision=${expectedRevision}`, token, { method: "POST" });
}

export function getCompany(token: string): Promise<Company> {
  return apiRequest<Company>("/v1/admin/company", token);
}

export function updateCompany(
  token: string,
  payload: CompanyUpdateInput,
): Promise<Company> {
  return apiRequest<Company>("/v1/admin/company", token, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function listOrganizationUnits(token: string): Promise<{ items: OrganizationUnit[] }> {
  return apiRequest<{ items: OrganizationUnit[] }>("/v1/admin/organization-units", token);
}

export function createOrganizationUnit(
  token: string,
  payload: OrganizationUnitCreateInput,
): Promise<OrganizationUnit> {
  return apiRequest<OrganizationUnit>("/v1/admin/organization-units", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateOrganizationUnit(
  token: string,
  id: string,
  payload: OrganizationUnitUpdateInput,
): Promise<OrganizationUnit> {
  return apiRequest<OrganizationUnit>(`/v1/admin/organization-units/${id}`, token, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteOrganizationUnit(token: string, id: string): Promise<void> {
  return apiRequest<void>(`/v1/admin/organization-units/${id}`, token, { method: "DELETE" });
}

export function listUsers(
  token: string,
  pagination: PaginationQuery & { keywords?: string; organizationUnitId?: string } = {},
): Promise<PaginatedListResponse<AdminUser>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  if (pagination.keywords?.trim()) query.set("keywords", pagination.keywords.trim());
  if (pagination.organizationUnitId) query.set("organization_unit_id", pagination.organizationUnitId);
  const suffix = query.size > 0 ? `?${query}` : "";
  return apiRequest<PaginatedListResponse<AdminUser>>(`/v1/admin/users${suffix}`, token);
}

export function getAdminUser(token: string, userId: string): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/v1/admin/users/${userId}`, token);
}

export function createUser(token: string, payload: AdminUserCreateInput): Promise<AdminUser> {
  return apiRequest<AdminUser>("/v1/admin/users", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateUser(
  token: string,
  id: string,
  payload: AdminUserUpdateInput,
): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/v1/admin/users/${id}`, token, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function resetUserPassword(token: string, id: string, password: string): Promise<void> {
  return apiRequest<void>(`/v1/admin/users/${id}/password`, token, {
    method: "PUT",
    body: JSON.stringify({ password }),
  });
}

export function listAgents(
  token: string,
  pagination: PaginationQuery = {},
): Promise<PaginatedListResponse<Agent>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  const suffix = query.size > 0 ? `?${query}` : "";
  return apiRequest<PaginatedListResponse<Agent>>(`/v1/agents${suffix}`, token);
}

export function getAgent(id: string, token: string): Promise<Agent> {
  return apiRequest<Agent>(`/v1/agents/${id}`, token);
}

export function listConversations(
  token: string,
  agentId: string,
  pagination: PaginationQuery = { page: 1, pageSize: 100 },
): Promise<ConversationListResponse> {
  const query = new URLSearchParams({ agent_id: agentId });
  appendPagination(query, pagination);
  return apiRequest<ConversationListResponse>(`/v1/conversations?${query}`, token);
}

export function createConversation(
  token: string,
  agentId: string,
  title: string,
): Promise<Conversation> {
  return apiRequest<Conversation>("/v1/conversations", token, {
    method: "POST",
    body: JSON.stringify({ agent_id: agentId, title }),
  });
}

export function listAdminAgents(
  token: string,
  options: PaginationQuery & { status?: AdminAgent["status"]; keywords?: string } = {},
): Promise<PaginatedListResponse<AdminAgent>> {
  const query = new URLSearchParams();
  appendPagination(query, options);
  if (options.status !== undefined) query.set("status", options.status);
  if (options.keywords?.trim()) query.set("keywords", options.keywords.trim());
  const suffix = query.size > 0 ? `?${query}` : "";
  return apiRequest<PaginatedListResponse<AdminAgent>>(
    `/v1/admin/agents${suffix}`,
    token,
  );
}

export function getDashboardSummary(
  token: string,
  options: DashboardQuery & { system?: boolean } = {},
): Promise<DashboardSummary> {
  const query = new URLSearchParams();
  appendDashboardScope(query, options);
  const prefix = options.system ? "/v1/admin/system/dashboard" : "/v1/admin/dashboard";
  return apiRequest<DashboardSummary>(`${prefix}/summary?${query}`, token);
}

export function getDashboardTimeseries(
  token: string,
  options: DashboardQuery & { system?: boolean } = {},
): Promise<{ interval: string; items: DashboardTimeseriesPoint[] }> {
  const query = new URLSearchParams();
  appendDashboardScope(query, options);
  const prefix = options.system ? "/v1/admin/system/dashboard" : "/v1/admin/dashboard";
  return apiRequest<{ interval: string; items: DashboardTimeseriesPoint[] }>(`${prefix}/timeseries?${query}`, token);
}

export function listDashboardRuns(
  token: string,
  options: PaginationQuery & DashboardQuery = {},
): Promise<PaginatedListResponse<DashboardRun>> {
  const query = new URLSearchParams({ days: String(options.days ?? 7) });
  appendPagination(query, options);
  appendDashboardScope(query, options);
  return apiRequest<PaginatedListResponse<DashboardRun>>(`/v1/admin/dashboard/runs?${query}`, token);
}

export function listDashboardErrors(
  token: string,
  options: DashboardQuery = {},
): Promise<{ items: DashboardError[] }> {
  const query = new URLSearchParams({ days: String(options.days ?? 7) });
  appendDashboardScope(query, options);
  return apiRequest<{ items: DashboardError[] }>(`/v1/admin/dashboard/errors?${query}`, token);
}

export function listAnalyticsErrorEvents(
  token: string,
  options: PaginationQuery & Omit<DashboardQuery, "status"> & { severity?: string; errorCode?: string; status?: string; runStatus?: DashboardQuery["status"] } = {},
): Promise<PaginatedListResponse<DashboardErrorEvent>> {
  const query = new URLSearchParams({ days: String(options.days ?? 7) });
  appendPagination(query, options);
  if (options.severity) query.set("severity", options.severity);
  if (options.errorCode) query.set("error_code", options.errorCode);
  appendDashboardScope(query, { ...options, status: options.runStatus }, "run_status");
  if (options.status) query.set("status", options.status);
  return apiRequest<PaginatedListResponse<DashboardErrorEvent>>(`/v1/admin/analytics/errors?${query}`, token);
}

export function getAnalyticsErrorEvent(token: string, eventId: string): Promise<DashboardErrorEventDetail> {
  return apiRequest<DashboardErrorEventDetail>(`/v1/admin/analytics/errors/${eventId}`, token);
}

export function getDashboardTrace(token: string, runId: string): Promise<DashboardTrace> {
  return apiRequest<DashboardTrace>(`/v1/admin/dashboard/runs/${runId}/trace`, token);
}

export function listSystemAgentStatus(
  token: string,
  pagination: PaginationQuery & DashboardQuery = { page: 1, pageSize: 20 },
): Promise<PaginatedListResponse<SystemAgentStatus>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  appendDashboardScope(query, pagination);
  return apiRequest<PaginatedListResponse<SystemAgentStatus>>(`/v1/admin/system/dashboard/agents/status?${query}`, token);
}

export function getSystemRankings(token: string, options: DashboardQuery | number = 7): Promise<DashboardRankings> {
  const query = new URLSearchParams();
  appendDashboardScope(query, typeof options === "number" ? { days: options } : options);
  return apiRequest<DashboardRankings>(`/v1/admin/system/dashboard/rankings?${query}`, token);
}

export function listToolCatalog(
  token: string, query: PaginationQuery = {},
): Promise<PaginatedListResponse<ToolCatalogEntry>> {
  const params = new URLSearchParams();
  appendPagination(params, query);
  const suffix = params.size ? `?${params}` : "";
  return apiRequest<PaginatedListResponse<ToolCatalogEntry>>(`/v1/admin/tools${suffix}`, token);
}

export function updateToolCatalogEntry(
  token: string,
  toolId: string,
  payload: Pick<ToolCatalogEntry, "name" | "description" | "required_roles" | "risk_level" | "is_enabled">,
): Promise<ToolCatalogEntry> {
  return apiRequest<ToolCatalogEntry>(`/v1/admin/tools/${toolId}`, token, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

/** 显式撤销或解除撤销，不随普通目录启用隐式改变。 */
export function setToolRevocation(token: string, toolId: string, revoked: boolean, expectedVersion: number): Promise<ToolCatalogEntry> {
  return apiRequest(`/v1/admin/tools/${toolId}/revocation`, token, { method: "POST", body: JSON.stringify({ revoked, expected_version: expectedVersion }) });
}

export function listModelEndpoints(
  token: string,
  pagination: PaginationQuery = { page: 1, pageSize: 100 },
): Promise<PaginatedListResponse<ModelEndpoint>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  return apiRequest<PaginatedListResponse<ModelEndpoint>>(
    `/v1/admin/model-endpoints?${query}`,
    token,
  );
}

/** 按 ID 读取单个模型端点，供详情页独立于列表分页加载。 */
export function getModelEndpoint(token: string, endpointId: string): Promise<ModelEndpoint> {
  return apiRequest<ModelEndpoint>(`/v1/admin/model-endpoints/${endpointId}`, token, { cache: "no-store" });
}

export function listModelEndpointVersions(
  token: string,
  endpointId: string,
  pagination: PaginationQuery = { page: 1, pageSize: 100 },
): Promise<PaginatedListResponse<ModelEndpointVersion>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  return apiRequest<PaginatedListResponse<ModelEndpointVersion>>(
    `/v1/admin/model-endpoints/${endpointId}/versions?${query}`,
    token,
  );
}

export function createModelEndpoint(
  token: string,
  payload: Record<string, unknown>,
): Promise<ModelEndpoint> {
  return apiRequest<ModelEndpoint>("/v1/admin/model-endpoints", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** 读取服务端受控供应商预设；自定义选项由页面额外展示。 */
export function listModelProviderPresets(token: string): Promise<ModelProviderPreset[]> {
  return apiRequest<ModelProviderPreset[]>("/v1/admin/model-endpoints/presets", token);
}

/** 显式访问供应商模型目录；输入框回车添加模型不会调用该接口。 */
export function discoverModels(
  token: string,
  payload: { base_url: string; api_key: string },
): Promise<{ items: string[] }> {
  return apiRequest<{ items: string[] }>("/v1/admin/model-endpoints/discover", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** 整体保存模型配置及其当前模型集合。 */
export function saveModelEndpointConfiguration(
  token: string,
  endpointId: string,
  payload: Record<string, unknown>,
): Promise<ModelEndpoint> {
  return apiRequest<ModelEndpoint>(
    `/v1/admin/model-endpoints/${endpointId}/configuration`,
    token,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

/** 创建单模型真实流式连通性测试。 */
export function startModelTest(
  token: string,
  endpointId: string,
  endpointModelId: string,
): Promise<ModelTestRun> {
  return apiRequest<ModelTestRun>(
    `/v1/admin/model-endpoints/${endpointId}/models/${endpointModelId}/tests`,
    token,
    { method: "POST" },
  );
}

/** 轮询单模型测试的真实后台阶段。 */
export function getModelTestRun(token: string, testRunId: string): Promise<ModelTestRun> {
  return apiRequest<ModelTestRun>(
    `/v1/admin/model-endpoints/test-runs/${testRunId}`,
    token,
  );
}

export function cancelModelTestRun(token: string, testRunId: string): Promise<ModelTestRun> {
  return apiRequest<ModelTestRun>(`/v1/admin/model-endpoints/test-runs/${testRunId}/cancel`, token, { method: "POST" });
}

/** 在所有当前模型测试有效且通过时启用模型。 */
export function enableModelEndpoint(token: string, endpointId: string): Promise<ModelEndpoint> {
  return apiRequest<ModelEndpoint>(`/v1/admin/model-endpoints/${endpointId}/enable`, token, {
    method: "POST",
  });
}

/** 删除已停用且未被 Agent 固定引用的模型。 */
export function deleteModelEndpoint(token: string, endpointId: string): Promise<void> {
  return apiRequest<void>(`/v1/admin/model-endpoints/${endpointId}`, token, {
    method: "DELETE",
  });
}

export function updateModelEndpointProfile(
  token: string,
  endpointId: string,
  payload: { name: string; logo_url: string | null },
): Promise<ModelEndpoint> {
  return apiRequest<ModelEndpoint>(`/v1/admin/model-endpoints/${endpointId}`, token, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function createModelEndpointVersion(
  token: string,
  endpointId: string,
  version: Record<string, unknown>,
): Promise<ModelEndpointVersion> {
  return apiRequest<ModelEndpointVersion>(
    `/v1/admin/model-endpoints/${endpointId}/versions`,
    token,
    { method: "POST", body: JSON.stringify({ version }) },
  );
}

export function disableModelEndpoint(token: string, endpointId: string): Promise<ModelEndpoint> {
  return apiRequest<ModelEndpoint>(`/v1/admin/model-endpoints/${endpointId}/disable`, token, {
    method: "POST",
  });
}

export function testModelEndpoint(token: string, endpointId: string) {
  return apiRequest<{ status: string; checks: Record<string, string> }>(
    `/v1/admin/model-endpoints/${endpointId}/test`,
    token,
    { method: "POST" },
  );
}

export function createAdminAgent(
  token: string,
  payload: Record<string, unknown>,
): Promise<AdminAgent> {
  return apiRequest<AdminAgent>("/v1/admin/agents", token, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getAdminAgent(token: string, agentId: string): Promise<AdminAgent> {
  return apiRequest<AdminAgent>(`/v1/admin/agents/${agentId}`, token);
}

export function getAgentDraft(token: string, agentId: string) {
  return apiRequest<AgentDraft>(`/v1/admin/agents/${agentId}/draft`, token);
}

export function updateAgentDraft(
  token: string,
  agentId: string,
  expectedRevision: number,
  config: AgentConfig,
) {
  return apiRequest<AgentDraft>(
    `/v1/admin/agents/${agentId}/draft`,
    token,
    {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: expectedRevision, config }),
    },
  );
}

export function updateAgentProfile(
  token: string,
  agentId: string,
  profile: Pick<
    AdminAgent,
    "name" | "logo_url" | "description" | "welcome_message" | "suggested_prompts"
  >,
): Promise<AdminAgent> {
  return apiRequest<AdminAgent>(`/v1/admin/agents/${agentId}/profile`, token, {
    method: "PATCH",
    body: JSON.stringify(profile),
  });
}

export function validateAgentDraft(token: string, agentId: string, config: AgentConfig) {
  return apiRequest<{
    valid: true;
    model_endpoint_version_id: string;
    credential_revision: number;
    tool_ids: string[];
  }>(`/v1/admin/agents/${agentId}/draft/validate`, token, {
    method: "POST",
    body: JSON.stringify({ config }),
  });
}

export function listAgentVersions(
  token: string,
  agentId: string,
  pagination: PaginationQuery = { page: 1, pageSize: 100 },
): Promise<PaginatedListResponse<AgentVersion>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  return apiRequest<PaginatedListResponse<AgentVersion>>(
    `/v1/admin/agents/${agentId}/versions?${query}`,
    token,
  );
}

export function compareAgentReleaseVersions(token: string, agentId: string, versionId: string): Promise<AgentReleaseComparison> {
  return apiRequest(`/v1/admin/agents/${agentId}/versions/${versionId}/comparison`, token);
}

export function activateAgentVersion(
  token: string,
  agentId: string,
  versionId: string,
): Promise<AdminAgent> {
  return apiRequest<AdminAgent>(
    `/v1/admin/agents/${agentId}/versions/${versionId}/activate`,
    token,
    { method: "POST" },
  );
}

export function getAgentReleaseReadiness(token: string, agentId: string, versionId: string): Promise<ReleaseReadiness> {
  return apiRequest(`/v1/admin/agents/${agentId}/versions/${versionId}/release-readiness`, token);
}

export function listAgentReleaseEvaluations(token: string, agentId: string, versionId: string,
  pagination: PaginationQuery = {},
): Promise<PaginatedListResponse<ReleaseEvaluationSummary>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  return apiRequest(`/v1/admin/agents/${agentId}/versions/${versionId}/evaluations?${query}`, token);
}

export function disableAgent(token: string, agentId: string): Promise<AdminAgent> {
  return apiRequest<AdminAgent>(`/v1/admin/agents/${agentId}/disable`, token, {
    method: "POST",
  });
}

export function listAgentReleaseRollouts(token: string, agentId: string, pagination: PaginationQuery = {}): Promise<PaginatedListResponse<ReleaseRolloutSummary>> {
  const query = new URLSearchParams(); appendPagination(query, pagination);
  return apiRequest(`/v1/admin/agents/${agentId}/rollouts?${query}`, token);
}

export function getAgentReleaseRollout(token: string, agentId: string, rolloutId: string): Promise<ReleaseRolloutDetail> {
  return apiRequest(`/v1/admin/agents/${agentId}/rollouts/${rolloutId}`, token);
}

export function createAgentReleaseRollout(token: string, agentId: string, payload: ReleaseRolloutCreate): Promise<ReleaseRolloutSummary> {
  return apiRequest(`/v1/admin/agents/${agentId}/rollouts`, token, { method: "POST", body: JSON.stringify(payload) });
}

export function commandAgentReleaseRollout(token: string, agentId: string, rolloutId: string, payload: ReleaseRolloutCommand): Promise<ReleaseRolloutSummary> {
  return apiRequest(`/v1/admin/agents/${agentId}/rollouts/${rolloutId}/commands`, token, { method: "POST", body: JSON.stringify(payload) });
}

export function listAgentReleaseRolloutEvents(token: string, agentId: string, rolloutId: string, pagination: PaginationQuery = {}): Promise<PaginatedListResponse<ReleaseRolloutEvent>> {
  const query = new URLSearchParams(); appendPagination(query, pagination);
  return apiRequest(`/v1/admin/agents/${agentId}/rollouts/${rolloutId}/events?${query}`, token);
}

export function listAgentGrants(
  token: string,
  agentId: string,
  pagination: PaginationQuery = { page: 1, pageSize: 100 },
): Promise<PaginatedListResponse<AgentGrant>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  return apiRequest<PaginatedListResponse<AgentGrant>>(
    `/v1/admin/agents/${agentId}/grants?${query}`,
    token,
  );
}

export function listAgentAuditEvents(
  token: string,
  agentId: string,
  pagination: PaginationQuery = {},
): Promise<PaginatedListResponse<AgentAuditEvent>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  const suffix = query.size > 0 ? `?${query}` : "";
  return apiRequest<PaginatedListResponse<AgentAuditEvent>>(
    `/v1/admin/agents/${agentId}/audit-events${suffix}`,
    token,
  );
}

export function publishAgent(
  token: string,
  agentId: string,
  expectedRevision: number,
  activate = false,
  releaseNotes = "",
) {
  return apiRequest<AgentVersion>(
    `/v1/admin/agents/${agentId}/versions`,
    token,
    {
      method: "POST",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        activate,
        release_notes: releaseNotes,
      }),
    },
  );
}

export function replaceAgentGrants(
  token: string,
  agentId: string,
  role: string,
) {
  return apiRequest<{ items: AgentGrant[] }>(`/v1/admin/agents/${agentId}/grants`, token, {
    method: "PUT",
    body: JSON.stringify({ grants: [{ subject_type: "role", subject_id: role }] }),
  });
}

export function replaceAgentAccessGrants(
  token: string,
  agentId: string,
  grants: Array<{ subject_type: "user" | "role"; subject_id: string }>,
) {
  return apiRequest<{ items: AgentGrant[] }>(`/v1/admin/agents/${agentId}/grants`, token, {
    method: "PUT",
    body: JSON.stringify({ grants }),
  });
}

export function rotateModelCredential(token: string, endpointId: string, apiKey: string) {
  return apiRequest<ModelEndpoint>(
    `/v1/admin/model-endpoints/${endpointId}/credential`,
    token,
    { method: "PUT", body: JSON.stringify({ api_key: apiKey }) },
  );
}

function assertRunStatus(value: unknown): void {
  if (!isRunStatus(value)) throw new ApiError("运行状态与当前页面版本不兼容，请刷新应用或联系管理员；请勿重复提交。", 409,
    { code: "CLIENT_CONTRACT_UNSUPPORTED" });
}

export async function getConversation(id: string, token: string): Promise<Conversation> {
  const value = await apiRequest<Conversation>(`/v1/conversations/${id}`, token);
  if (value.latest_run) assertRunStatus(value.latest_run.status);
  return value;
}

export function updateConversation(
  id: string,
  token: string,
  update: ConversationUpdate,
): Promise<ConversationSummary> {
  return apiRequest<ConversationSummary>(`/v1/conversations/${id}`, token, {
    method: "PATCH",
    body: JSON.stringify(update),
  });
}

export function deleteConversation(id: string, token: string): Promise<void> {
  return apiRequest<void>(`/v1/conversations/${id}`, token, { method: "DELETE" });
}

export async function sendMessage(
  conversationId: string,
  content: string,
  token: string,
  idempotencyKey: string,
): Promise<MessageAccepted> {
  const value = await apiRequest<MessageAccepted>(`/v1/conversations/${conversationId}/messages`, token, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ content }),
  });
  assertRunStatus(value.status);
  return value;
}

export async function cancelRun(runId: string, token: string): Promise<RunDetail> {
  const value = await apiRequest<RunDetail>(`/v1/runs/${runId}/cancel`, token, { method: "POST" });
  assertRunStatus(value.status);
  return value;
}

function parseEvent(block: string): RunEvent | null {
  // SSE 允许一个事件包含多行 data，本解析器会按规范用换行符重新拼接。
  let id: number | null = null;
  let type = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const value = separator < 0 ? "" : line.slice(separator + 1).trimStart();
    if (field === "id") id = Number(value);
    if (field === "event") type = value;
    if (field === "data") data.push(value);
  }
  if (id === null || !Number.isSafeInteger(id) || id <= 0 || data.length === 0) return null;
  let payload: unknown;
  try { payload = JSON.parse(data.join("\n")); }
  catch { throw new ApiError("事件格式无效，请刷新应用或联系管理员；请勿重复提交。", 409, { code: "CLIENT_CONTRACT_UNSUPPORTED" }); }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ApiError("事件格式与当前页面版本不兼容，请刷新应用；请勿重复提交。", 409, { code: "CLIENT_CONTRACT_UNSUPPORTED" });
  }
  const eventData = payload as Record<string, unknown>;
  if (type.startsWith("run.") && "status" in eventData) assertRunStatus(eventData.status);
  return { id, type, data: eventData };
}

/** 持续接收服务端 SSE 增量，按事件顺序通知页面，并返回最后收到的事件游标。 */
export function getDiagnosticReport(token: string, runId: string): Promise<DiagnosticReport> {
  return apiRequest<DiagnosticReport>(`/v1/runs/${runId}/diagnostic`, token);
}

export function listApprovals(token: string, options: { page: number; pageSize: number; state?: string; actionId?: string }): Promise<PaginatedListResponse<ApprovalSummary>> {
  const query = new URLSearchParams({ page: String(options.page), page_size: String(options.pageSize) });
  if (options.state) query.set("state", options.state);
  if (options.actionId) query.set("action_id", options.actionId);
  return apiRequest(`/v1/approvals?${query}`, token);
}

export function getApproval(token: string, id: string): Promise<ApprovalDetail> {
  return apiRequest(`/v1/approvals/${id}`, token);
}

export function decideApproval(token: string, approval: ApprovalDetail, decision: "approved" | "rejected", reason: string): Promise<ApprovalDetail> {
  return apiRequest(`/v1/approvals/${approval.id}/decision`, token, { method: "POST", body: JSON.stringify({ expected_revision: approval.revision, decision, reason }) });
}

export function cancelApproval(token: string, approval: ApprovalDetail): Promise<ApprovalDetail> {
  return apiRequest(`/v1/approvals/${approval.id}/cancel`, token, { method: "POST", body: JSON.stringify({ expected_revision: approval.revision }) });
}

export function createActionApproval(token: string, action: BusinessActionDetail, contextRunId: string, approverId: string): Promise<ApprovalDetail> {
  return apiRequest(`/v1/actions/${action.id}/approval`, token, { method: "POST", body: JSON.stringify({
    expected_generation: action.generation, expected_parameters_digest: action.parameters_digest,
    expected_catalog_version: action.effective_catalog_version ?? action.catalog_version, context_run_id: contextRunId, approver_id: approverId,
  }) });
}

export function amendBusinessAction(token: string, action: BusinessActionDetail, contextRunId: string, approverId: string, title: string, summary: string): Promise<BusinessActionDetail> {
  return apiRequest(`/v1/actions/${action.id}/amend`, token, { method: "POST", body: JSON.stringify({
    expected_generation: action.generation, expected_parameters_digest: action.parameters_digest,
    expected_catalog_version: action.effective_catalog_version ?? action.catalog_version, context_run_id: contextRunId,
    approver_id: approverId, title, summary,
  }) });
}

export function listActionApprovers(token: string, actionId: string, contextRunId: string, keywords: string, pageSize: number): Promise<PaginatedListResponse<ApprovalApprover>> {
  const query = new URLSearchParams({ page: "1", page_size: String(pageSize), context_run_id: contextRunId, keywords });
  return apiRequest(`/v1/actions/${actionId}/approvers?${query}`, token);
}

export function listMockResourceGrants(token: string, page: number, pageSize: number): Promise<PaginatedListResponse<MockResourceGrant>> {
  return apiRequest(`/v1/admin/mock-resource-grants?page=${page}&page_size=${pageSize}`, token);
}

export function setMockResourceGrant(token: string, payload: { owner_user_id: string; user_id: string; permission: MockResourceGrant["permission"]; is_enabled: boolean; expected_revision: number }): Promise<MockResourceGrant> {
  return apiRequest("/v1/admin/mock-resource-grants", token, { method: "POST", body: JSON.stringify(payload) });
}

export function proposeMockTicket(token: string, runId: string): Promise<BusinessActionDetail> {
  return apiRequest<BusinessActionDetail>("/v1/actions/mock-ticket-proposals", token, { method: "POST", body: JSON.stringify({ run_id: runId }) });
}

export function getBusinessAction(token: string, actionId: string, contextRunId?: string): Promise<BusinessActionDetail> {
  const query = contextRunId ? `?context_run_id=${encodeURIComponent(contextRunId)}` : "";
  return apiRequest<BusinessActionDetail>(`/v1/actions/${actionId}${query}`, token);
}

export function listActionEscalations(token: string, actionId: string, runId: string, page: number, pageSize: number): Promise<PaginatedListResponse<EscalationPackageSummary>> {
  const query = new URLSearchParams({ context_run_id: runId, page: String(page), page_size: String(pageSize) });
  return apiRequest(`/v1/actions/${actionId}/escalations?${query}`, token);
}

export function getActionEscalation(token: string, actionId: string, packageId: string): Promise<EscalationPackageDetail> {
  return apiRequest(`/v1/actions/${actionId}/escalations/${packageId}`, token, { cache: "no-store" });
}

export function reportClientFirstText(token: string, runId: string, eventSequence: number, elapsedMs: number): Promise<{ recorded: boolean }> {
  return apiRequest(`/v1/runs/${runId}/client-first-text`, token, {
    method: "POST", body: JSON.stringify({ event_sequence: eventSequence, elapsed_ms: elapsedMs }),
  });
}

export function getRunSecurityEvents(token: string, runId: string, page: number, pageSize: number): Promise<PaginatedListResponse<RunSecurityEvent>> {
  return apiRequest(`/v1/admin/runs/${runId}/security-events?page=${page}&page_size=${pageSize}`, token);
}

export async function getRunDetail(token: string, runId: string): Promise<RunDetail> {
  const value = await apiRequest<RunDetail>(`/v1/runs/${runId}`, token);
  assertRunStatus(value.status);
  return value;
}

function requireHttpMcpDispatch(value: unknown, runId: string): asserts value is HttpMcpDispatchSummary {
  const fail = () => { throw new ApiError("派发记录契约无法确认，请刷新应用后重新查询。", 409,
    { code: "CLIENT_CONTRACT_UNSUPPORTED" }); };
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
  const item = value as Record<string, unknown>;
  const keys = ["id", "run_id", "attempt_id", "invocation_id", "source_record_id", "generation", "revision", "status",
    "claimed_at", "revoked_at", "created_at", "expires_at", "updated_at", "observed_at", "is_expired", "business_status_confirmed", "execution_permit"];
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const timestamp = (input: unknown): input is string => typeof input === "string"
    && /(?:Z|[+-]\d{2}:\d{2})$/.test(input) && Number.isFinite(Date.parse(input));
  if (Object.keys(item).length !== keys.length || Object.keys(item).some((key) => !keys.includes(key))
    || ["id", "run_id", "attempt_id", "invocation_id", "source_record_id"].some((key) => typeof item[key] !== "string" || !uuid.test(item[key] as string))
    || item.run_id !== runId || typeof item.status !== "string" || !["authorized", "claimed", "bound", "blocked", "revoked"].includes(item.status)
    || ["generation", "revision"].some((key) => typeof item[key] !== "number" || !Number.isSafeInteger(item[key]) || (item[key] as number) < 1)
    || ["created_at", "expires_at", "updated_at", "observed_at"].some((key) => !timestamp(item[key]))
    || (item.claimed_at !== null && !timestamp(item.claimed_at)) || (item.revoked_at !== null && !timestamp(item.revoked_at))
    || (item.status === "authorized") !== (item.claimed_at === null) || (item.status === "revoked") !== (item.revoked_at !== null)
    || typeof item.is_expired !== "boolean" || item.business_status_confirmed !== false || item.execution_permit !== false) return fail();
  const expiresAt = Date.parse(item.expires_at as string), observedAt = Date.parse(item.observed_at as string);
  // JS只保留毫秒；同一毫秒内以服务器的微秒比较结果为准。
  if (expiresAt <= Date.parse(item.created_at as string)
    || (expiresAt !== observedAt && item.is_expired !== (expiresAt < observedAt))) fail();
}

export async function listRunHttpMcpDispatches(token: string, runId: string, page: number, pageSize: number): Promise<PaginatedListResponse<HttpMcpDispatchSummary>> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  const value = await apiRequest<PaginatedListResponse<HttpMcpDispatchSummary>>(
    `/v1/runs/${encodeURIComponent(runId)}/http-mcp-dispatches?${query}`, token, { cache: "no-store" });
  if (!value || !Array.isArray(value.items) || value.page !== page || value.page_size !== pageSize
    || !Number.isSafeInteger(value.total) || value.total < 0 || value.pages !== Math.ceil(value.total / pageSize)
    || value.items.length > pageSize || value.items.length > Math.max(0, value.total - (page - 1) * pageSize)
    || new Set(value.items.map((item) => item?.id)).size !== value.items.length) {
    throw new ApiError("派发分页契约无法确认，请刷新应用后重新查询。", 409, { code: "CLIENT_CONTRACT_UNSUPPORTED" });
  }
  value.items.forEach((item) => requireHttpMcpDispatch(item, runId));
  return value;
}

export async function getRunHttpMcpDispatch(token: string, runId: string, dispatchId: string): Promise<HttpMcpDispatchSummary> {
  const value = await apiRequest<unknown>(`/v1/runs/${encodeURIComponent(runId)}/http-mcp-dispatches/${encodeURIComponent(dispatchId)}`,
    token, { cache: "no-store" });
  requireHttpMcpDispatch(value, runId);
  if (value.id !== dispatchId) throw new ApiError("原派发目标无法确认，请重新查询。", 409, { code: "CLIENT_CONTRACT_UNSUPPORTED" });
  return value;
}

export function listRuntimeAlerts(token: string, options: { page: number; pageSize: number; state: "unacknowledged" | "acknowledged" | "resolved" | "all"; severity?: RuntimeAlert["severity"]; ruleCode?: string }): Promise<RuntimeAlertList> {
  const query = new URLSearchParams({ page: String(options.page), page_size: String(options.pageSize), state: options.state });
  if (options.severity) query.set("severity", options.severity);
  if (options.ruleCode) query.set("rule_code", options.ruleCode);
  return apiRequest(`/v1/admin/runtime-alerts?${query}`, token);
}

export function acknowledgeRuntimeAlert(token: string, alert: RuntimeAlert): Promise<RuntimeAlert> {
  return apiRequest(`/v1/admin/runtime-alerts/${alert.id}/acknowledge`, token,
    { method: "POST", body: JSON.stringify({ expected_revision: alert.revision }) });
}

export function listToolDiscoverySources(token: string, page: number, pageSize: number): Promise<PaginatedListResponse<ToolDiscoveryView>> {
  return apiRequest(`/v1/admin/tool-discovery/sources?page=${page}&page_size=${pageSize}`, token, { cache: "no-store" });
}

export function getToolConnection(token: string, sourceKey: string): Promise<ToolConnectionView> {
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}/connection`, token, { cache: "no-store" });
}

export function controlToolConnection(token: string, sourceKey: string, revision: number, sourceRecordId: string, operation: "open"): Promise<ToolConnectionView>;
export function controlToolConnection(token: string, sourceKey: string, revision: number, sourceRecordId: string, operation: "probes"): Promise<ToolConnectionProbe>;
export function controlToolConnection(token: string, sourceKey: string, revision: number, sourceRecordId: string, operation: "open" | "probes"): Promise<ToolConnectionView | ToolConnectionProbe> {
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}/connection/${operation}`, token,
    { method: "POST", cache: "no-store", body: JSON.stringify({ expected_revision: revision, expected_source_record_id: sourceRecordId, confirm: true }) });
}

export function getToolConnectionProbe(token: string, sourceKey: string, jobId: string): Promise<ToolConnectionProbe> {
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}/connection/probes/${encodeURIComponent(jobId)}`, token, { cache: "no-store" });
}

export function cancelToolConnectionProbe(token: string, sourceKey: string, jobId: string, revision: number): Promise<ToolConnectionProbe> {
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}/connection/probes/${encodeURIComponent(jobId)}/cancel`, token,
    { method: "POST", cache: "no-store", body: JSON.stringify({ expected_revision: revision }) });
}

export function getToolDiscoverySource(token: string, sourceKey: string): Promise<ToolDiscoveryView> {
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}`, token, { cache: "no-store" });
}

export function configureToolDiscoverySource(token: string, sourceKey: string, expectedRevision: number, policy: ToolDiscoveryPolicy): Promise<ToolDiscoveryView> {
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}`, token,
    { method: "PUT", cache: "no-store", body: JSON.stringify({ expected_revision: expectedRevision, policy }) });
}

export function invalidateToolDiscoverySource(token: string, sourceKey: string,
  payload: { expected_policy_digest: string; source_fingerprint: string; reason: "list_changed" | "manual_refresh" }): Promise<ToolDiscoveryView> {
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}/invalidate`, token,
    { method: "POST", cache: "no-store", body: JSON.stringify(payload) });
}

export function recordToolDiscoveryCatalog(token: string, sourceKey: string,
  payload: { expected_revision: number; expected_policy_digest: string; source_fingerprint: string; catalog_json: string }): Promise<ToolDiscoveryView> {
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}/catalogs`, token,
    { method: "POST", cache: "no-store", body: JSON.stringify(payload) });
}

export function getFixedDiscoveredTool(token: string, sourceKey: string, binding: { record_id: string; catalog_digest: string; tool_id: string }): Promise<DiscoveredToolDescriptor> {
  const query = new URLSearchParams({ record_id: binding.record_id, catalog_digest: binding.catalog_digest });
  return apiRequest(`/v1/admin/tool-discovery/sources/${encodeURIComponent(sourceKey)}/tools/${encodeURIComponent(binding.tool_id)}?${query}`, token, { cache: "no-store" });
}

export function listModelCredentialRevisions(token: string, endpointId: string, page: number, pageSize: number): Promise<PaginatedListResponse<ModelCredentialRevision>> {
  return apiRequest(`/v1/admin/model-endpoints/${endpointId}/credential-revisions?page=${page}&page_size=${pageSize}`, token, { cache: "no-store" });
}

export function listModelCredentialAudits(token: string, endpointId: string, page: number, pageSize: number): Promise<PaginatedListResponse<ModelCredentialAudit>> {
  return apiRequest(`/v1/admin/model-endpoints/${endpointId}/credential-audits?page=${page}&page_size=${pageSize}`, token, { cache: "no-store" });
}

export function revokeModelCredentialRevision(token: string, endpointId: string, revision: number, expectedCurrentRevision: number, reason: string): Promise<ModelCredentialRevision> {
  return apiRequest(`/v1/admin/model-endpoints/${endpointId}/credential-revisions/${revision}/revoke`, token,
    { method: "POST", cache: "no-store", body: JSON.stringify({ expected_current_revision: expectedCurrentRevision, reason }) });
}

export function listMockTicketEscalations(token: string, externalId: string, page: number, pageSize: number): Promise<PaginatedListResponse<EscalationPackageSummary>> {
  return apiRequest(`/v1/mock-tickets/${encodeURIComponent(externalId)}/escalations?page=${page}&page_size=${pageSize}`, token);
}

export function getMockTicketEscalation(token: string, externalId: string, packageId: string): Promise<EscalationPackageDetail> {
  return apiRequest(`/v1/mock-tickets/${encodeURIComponent(externalId)}/escalations/${packageId}`, token, { cache: "no-store" });
}

export function listMockTickets(token: string, options: { page: number; pageSize: number; state?: string; keywords?: string }): Promise<PaginatedListResponse<MockTicket>> {
  const query = new URLSearchParams({ page: String(options.page), page_size: String(options.pageSize) });
  if (options.state) query.set("state", options.state);
  if (options.keywords?.trim()) query.set("keywords", options.keywords.trim());
  return apiRequest(`/v1/mock-tickets?${query}`, token);
}

export function getMockTicket(token: string, externalId: string): Promise<MockTicket> {
  return apiRequest(`/v1/mock-tickets/${encodeURIComponent(externalId)}`, token);
}

export function getConversationTakeover(token: string, conversationId: string, signal?: AbortSignal): Promise<NonNullable<Conversation["support_takeover"]>> {
  return apiRequest(`/v1/conversations/${conversationId}/support-takeover`, token, { signal });
}

export function listMockTicketCommands(token: string, externalId: string, page: number, pageSize: number): Promise<PaginatedListResponse<MockTicketCommand>> {
  return apiRequest(`/v1/mock-tickets/${encodeURIComponent(externalId)}/commands?page=${page}&page_size=${pageSize}`, token);
}

export function submitMockTicketCommand(token: string, externalId: string, payload: { kind: MockTicketCommandKind; body: string; expected_version: number; idempotency_key: string }): Promise<MockTicketCommand> {
  return apiRequest(`/v1/mock-tickets/${encodeURIComponent(externalId)}/commands`, token, { method: "POST", body: JSON.stringify(payload) });
}

export function getActionReceipts(token: string, actionId: string, contextRunId: string,
  pagination: PaginationQuery = {},
): Promise<PaginatedListResponse<ActionReceipt>> {
  const query = new URLSearchParams({ context_run_id: contextRunId });
  appendPagination(query, pagination);
  return apiRequest<PaginatedListResponse<ActionReceipt>>(`/v1/actions/${actionId}/receipts?${query}`, token);
}

export function reconcileBusinessAction(token: string, action: BusinessActionDetail, contextRunId: string): Promise<BusinessActionDetail> {
  return apiRequest<BusinessActionDetail>(`/v1/actions/${action.id}/reconcile`, token, { method: "POST", body: JSON.stringify({
    expected_generation: action.generation, expected_reconciliation_count: action.reconciliation_count, context_run_id: contextRunId,
  }) });
}

export function getRunActionAudit(token: string, runId: string, pagination: PaginationQuery = {}): Promise<PaginatedListResponse<ActionAuditSummary>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  return apiRequest<PaginatedListResponse<ActionAuditSummary>>(`/v1/admin/dashboard/runs/${runId}/actions?${query}`, token);
}

export function executeBusinessAction(token: string, action: BusinessActionDetail, contextRunId: string): Promise<BusinessActionDetail> {
  return apiRequest<BusinessActionDetail>(`/v1/actions/${action.id}/execute`, token, { method: "POST", body: JSON.stringify({
    expected_generation: action.generation, expected_parameters_digest: action.parameters_digest,
    expected_catalog_version: action.effective_catalog_version, context_run_id: contextRunId,
  }) });
}

export async function streamRun(
  runId: string,
  token: string,
  after: number,
  signal: AbortSignal,
  onEvent: (event: RunEvent) => void,
): Promise<number> {
  // cursor 与 Last-Event-ID 同时携带最后确认位置，使中断重连不会重复渲染事件。
  const response = await fetch(`${API_BASE_URL}/v1/runs/${runId}/events?cursor=${after}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream",
      "Last-Event-ID": String(after),
    },
    signal,
  });
  if (!response.ok || response.body === null) {
    throw new ApiError(`事件流连接失败（${response.status}）`, response.status);
  }
  // 直接读取网络响应流，不用 response.text()/json() 等待整个响应结束。
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  // 仅保留尚未拼完整的 SSE 事件，不用于缓存完整答案或模拟逐字播放。
  let buffer = "";
  let lastEventId = after;
  try {
    while (true) {
      // 有新的网络分片就返回；没有数据时等待，不主动生成任何字符。
      const { value, done } = await reader.read();
      // stream 模式保留跨分片的 UTF-8 字节，避免中文或 emoji 被截断。
      const chunk = decoder.decode(value, { stream: !done });
      // 流分片可能含用户正文；不将原始内容复制到浏览器日志。
      // CR 与 LF 可能落在不同网络分片，先拼接再规范化换行。
      buffer = (buffer + chunk).replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      // 一个分片可能包含多个事件；空行表示事件结束，依次解析并立即处理。
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        if (frame.split("\n").some((line) => line.trim() === "event: auth.expired")) {
          throw new ApiError("登录会话已失效，请重新登录", 401);
        }
        const event = parseEvent(frame);
        buffer = buffer.slice(boundary + 2);
        if (event !== null && event.id > lastEventId) {
          // 防御服务端重放或代理缓存导致的重复事件。
          lastEventId = event.id;
          // 完整事件到达就交给页面，不等模型结束，也不设置显示速度。
          onEvent(event);
        }
        boundary = buffer.indexOf("\n\n");
      }
      // 断流或正常结束都返回接收位置；调用方结合终态判断是否需要续传。
      if (done) return lastEventId;
    }
  } finally {
    // 正常结束、取消请求或抛错时都释放读取器。
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
