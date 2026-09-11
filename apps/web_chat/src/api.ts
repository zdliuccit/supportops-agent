import type {
  Conversation,
  ConversationListResponse,
  ConversationSummary,
  ConversationUpdate,
  MessageAccepted,
  RunEvent,
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
} from "./types";

/** 浏览器端统一使用的 REST/SSE API 地址，可由 Vite 环境变量覆盖。 */
const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

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
  pagination: PaginationQuery = {},
): Promise<PaginatedListResponse<AdminUser>> {
  const query = new URLSearchParams();
  appendPagination(query, pagination);
  const suffix = query.size > 0 ? `?${query}` : "";
  return apiRequest<PaginatedListResponse<AdminUser>>(`/v1/admin/users${suffix}`, token);
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
  options: PaginationQuery & { status?: AdminAgent["status"] } = {},
): Promise<PaginatedListResponse<AdminAgent>> {
  const query = new URLSearchParams();
  appendPagination(query, options);
  if (options.status !== undefined) query.set("status", options.status);
  const suffix = query.size > 0 ? `?${query}` : "";
  return apiRequest<PaginatedListResponse<AdminAgent>>(
    `/v1/admin/agents${suffix}`,
    token,
  );
}

export function listToolCatalog(token: string): Promise<{ items: ToolCatalogEntry[] }> {
  return apiRequest<{ items: ToolCatalogEntry[] }>("/v1/admin/tools", token);
}

export function updateToolCatalogEntry(
  token: string,
  toolId: string,
  payload: Omit<ToolCatalogEntry, "tool_id" | "implementation_key" | "version">,
): Promise<ToolCatalogEntry> {
  return apiRequest<ToolCatalogEntry>(`/v1/admin/tools/${toolId}`, token, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
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
  return apiRequest<ModelEndpoint>(`/v1/admin/model-endpoints/${endpointId}`, token);
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

export function disableAgent(token: string, agentId: string): Promise<AdminAgent> {
  return apiRequest<AdminAgent>(`/v1/admin/agents/${agentId}/disable`, token, {
    method: "POST",
  });
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
  activate = true,
  releaseNotes = "",
) {
  return apiRequest<{ id: string; version_number: number }>(
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

export function getConversation(id: string, token: string): Promise<Conversation> {
  return apiRequest<Conversation>(`/v1/conversations/${id}`, token);
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

export function sendMessage(
  conversationId: string,
  content: string,
  token: string,
  idempotencyKey: string,
): Promise<MessageAccepted> {
  return apiRequest<MessageAccepted>(`/v1/conversations/${conversationId}/messages`, token, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ content }),
  });
}

export function cancelRun(runId: string, token: string): Promise<{ status: string }> {
  return apiRequest<{ status: string }>(`/v1/runs/${runId}/cancel`, token, { method: "POST" });
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
  if (id === null || !Number.isFinite(id) || data.length === 0) return null;
  return { id, type, data: JSON.parse(data.join("\n")) as Record<string, unknown> };
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
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastEventId = after;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const event = parseEvent(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (event !== null && event.id > lastEventId) {
        // 防御服务端重放或代理缓存导致的重复事件。
        lastEventId = event.id;
        onEvent(event);
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) return lastEventId;
  }
}
