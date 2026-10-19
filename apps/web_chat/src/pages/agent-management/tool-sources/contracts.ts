import type { PaginatedListResponse, ToolDiscoveryView } from "@/types";

const digest = /^[0-9a-f]{64}$/;
const sourceKey = /^[a-z][a-z0-9_-]{0,63}$/;
const toolName = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isDigest(value: unknown): value is string {
  return typeof value === "string" && digest.test(value);
}

export const discoveryLabels: Record<ToolDiscoveryView["status"], string> = {
  pending: "待验证", ready: "目录已验证", disabled: "已停用", rejected: "目录被拒绝", expired: "批准已到期",
};

/** 未知契约不得成为编辑策略或继续写入的依据。 */
export function requireDiscoveryView(value: unknown): ToolDiscoveryView {
  if (!object(value) || value.schema_version !== "tool_discovery_view_v1"
    || typeof value.record_id !== "string" || !uuid.test(value.record_id)
    || typeof value.source_key !== "string" || !sourceKey.test(value.source_key)
    || !Number.isSafeInteger(value.revision) || Number(value.revision) < 1
    || typeof value.status !== "string" || !Object.hasOwn(discoveryLabels, value.status)
    || !isDigest(value.policy_digest) || !isDigest(value.source_fingerprint)
    || typeof value.valid_until !== "string" || !Number.isFinite(Date.parse(value.valid_until))
    || typeof value.created_at !== "string" || !Number.isFinite(Date.parse(value.created_at))
    || value.execution_enabled !== false || value.connection_status !== "not_connected"
    || !Number.isSafeInteger(value.observed_count) || Number(value.observed_count) < 0 || Number(value.observed_count) > 100
    || (value.failure_code !== null && value.failure_code !== "TOOL_DISCOVERY_CATALOG_REJECTED")
    || !object(value.policy) || value.policy.schema_version !== "tool_source_policy_v1"
    || typeof value.policy.enabled !== "boolean" || value.policy.source_fingerprint !== value.source_fingerprint
    || value.policy.valid_until !== value.valid_until || !Array.isArray(value.policy.allowed_tools)
    || value.policy.allowed_tools.length > 50 || !Array.isArray(value.tools) || value.tools.length > 50) {
    throw new Error("工具来源响应契约未知，请刷新页面；当前禁止写入。");
  }
  const allowed = new Map<string, string>();
  for (const tool of value.policy.allowed_tools) {
    if (!object(tool) || typeof tool.name !== "string" || !toolName.test(tool.name)
      || !isDigest(tool.descriptor_digest) || allowed.has(tool.name)) throw new Error("工具批准集合无法确认，请刷新页面。");
    allowed.set(tool.name, tool.descriptor_digest);
  }
  const names = new Set<string>();
  const ids = new Set<string>();
  for (const tool of value.tools) {
    if (!object(tool) || typeof tool.tool_id !== "string" || !/^mcp_[0-9a-f]{64}$/.test(tool.tool_id)
      || typeof tool.remote_name !== "string" || names.has(tool.remote_name) || ids.has(tool.tool_id)
      || !isDigest(tool.descriptor_digest) || allowed.get(tool.remote_name) !== tool.descriptor_digest
      || !isDigest(tool.input_schema_digest) || !isDigest(tool.output_schema_digest)) {
      throw new Error("固定工具绑定无法确认，请刷新页面。");
    }
    names.add(tool.remote_name); ids.add(tool.tool_id);
  }
  if ((value.status === "ready" && (!isDigest(value.catalog_digest) || names.size !== allowed.size))
    || (value.status !== "ready" && (value.catalog_digest !== null || names.size > 0))
    || (value.status === "rejected" && value.failure_code === null)
    || (value.failure_code !== null && value.status !== "rejected" && value.status !== "expired")
    || (value.policy.enabled && allowed.size === 0)
    || (value.status === "disabled") !== !value.policy.enabled) {
    throw new Error("工具来源状态无法确认，请刷新页面。");
  }
  return value as unknown as ToolDiscoveryView;
}

export function requireSourcePage(value: PaginatedListResponse<ToolDiscoveryView>, page: number, pageSize: number) {
  if (!Array.isArray(value.items) || value.items.length > pageSize || value.page !== page || value.page_size !== pageSize
    || !Number.isSafeInteger(value.total) || value.total < 0 || value.pages !== Math.ceil(value.total / pageSize)) {
    throw new Error("工具来源分页契约未知，请刷新页面。");
  }
  const items = value.items.map(requireDiscoveryView);
  if (new Set(items.map((item) => item.source_key)).size !== items.length) throw new Error("工具来源重复，请刷新页面。");
  return { ...value, items };
}
