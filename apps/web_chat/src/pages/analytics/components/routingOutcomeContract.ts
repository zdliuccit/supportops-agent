import type { RoutingExecutionOutcomeView, RunAttempt } from "@/types";

export type RoutingAttemptSummary = Pick<RunAttempt, "id" | "run_id" | "generation" | "status">;

const attemptStates = ["running", "completed", "failed", "cancelled", "expired", "quarantined"] as const;
const outcomeStates = ["completed", "failed", "cancelled", "quarantined", "unconfirmed"] as const;
const outcomeCodes = new Set(["ROUTING_EXECUTION_CANCELLED", "ROUTING_EXECUTION_LEASE_LOST", "ROUTING_EXECUTION_FAILED",
  "ROUTING_CHECKPOINT_CAPTURE_UNCONFIRMED", "ROUTING_PRODUCT_COMMIT_UNCONFIRMED", "ROUTING_WORKER_UNAVAILABLE",
  "ROUTING_WORKER_BINDING_INVALID", "ROUTING_RUN_REQUEST_REJECTED", "ROUTING_EXECUTION_RECONCILIATION_REQUIRED",
  "ROUTING_EXECUTION_OUTCOME_UNCONFIRMED"]);
const outcomeKeys = ["schema_version", "run_id", "attempt_id", "generation", "status", "code", "root_call_id",
  "observed_at", "execution_permitted", "recovery_permitted"];

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function uuid(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}
function attemptStatus(value: unknown): value is RunAttempt["status"] {
  return attemptStates.some((state) => state === value);
}
function outcomeStatus(value: unknown): value is RoutingExecutionOutcomeView["status"] {
  return outcomeStates.some((state) => state === value);
}
function invalid(): never {
  throw new Error("查询响应与当前页面契约不一致，请刷新页面后重试。");
}

/** 只消费需要显示的元数据，不把未校验的owner或上下文复制进页面状态。 */
export function requireRoutingAttempts(value: unknown, runId: string, page: number, pageSize: number) {
  if (!object(value) || !Array.isArray(value.items) || value.items.length > pageSize
    || value.page !== page || value.page_size !== pageSize || typeof value.total !== "number"
    || !Number.isSafeInteger(value.total) || value.total < value.items.length
    || value.pages !== Math.ceil(value.total / pageSize)) return invalid();
  const ids = new Set<string>(), generations = new Set<number>();
  const items: RoutingAttemptSummary[] = value.items.map((item: unknown) => {
    if (!object(item) || !uuid(item.id) || item.run_id !== runId || typeof item.generation !== "number"
      || !Number.isSafeInteger(item.generation) || item.generation < 1 || !attemptStatus(item.status)
      || ids.has(item.id) || generations.has(item.generation)) return invalid();
    ids.add(item.id); generations.add(item.generation);
    return { id: item.id, run_id: runId, generation: item.generation, status: item.status };
  });
  return { items, total: value.total };
}

/** 新增许可或未知状态不能被旧界面解读为完成。 */
export function requireRoutingOutcome(value: unknown, attempt: RoutingAttemptSummary): RoutingExecutionOutcomeView {
  if (!object(value) || Object.keys(value).length !== outcomeKeys.length || outcomeKeys.some((key) => !Object.hasOwn(value, key))
    || value.schema_version !== "routing_execution_outcome_v1" || value.run_id !== attempt.run_id
    || value.attempt_id !== attempt.id || value.generation !== attempt.generation || !outcomeStatus(value.status)
    || value.execution_permitted !== false || value.recovery_permitted !== false
    || value.root_call_id !== null && !uuid(value.root_call_id)
    || value.code !== null && (typeof value.code !== "string" || !outcomeCodes.has(value.code))
    || typeof value.observed_at !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(value.observed_at)
    || !Number.isFinite(Date.parse(value.observed_at))) return invalid();
  if (value.status === "completed" ? value.code !== null || value.root_call_id === null : value.code === null) return invalid();
  if (value.status === "unconfirmed" && value.code !== "ROUTING_EXECUTION_OUTCOME_UNCONFIRMED") return invalid();
  if (value.status === "quarantined" && (value.root_call_id === null || ![
    "ROUTING_CHECKPOINT_CAPTURE_UNCONFIRMED", "ROUTING_PRODUCT_COMMIT_UNCONFIRMED",
    "ROUTING_EXECUTION_RECONCILIATION_REQUIRED"].includes(String(value.code)))) return invalid();
  if (value.status === "failed" && (!["ROUTING_EXECUTION_LEASE_LOST", "ROUTING_EXECUTION_FAILED",
    "ROUTING_WORKER_UNAVAILABLE", "ROUTING_WORKER_BINDING_INVALID", "ROUTING_RUN_REQUEST_REJECTED"].includes(String(value.code))
    || value.root_call_id === null && !["ROUTING_WORKER_UNAVAILABLE", "ROUTING_WORKER_BINDING_INVALID",
      "ROUTING_RUN_REQUEST_REJECTED"].includes(String(value.code))
    || value.root_call_id !== null && value.code === "ROUTING_RUN_REQUEST_REJECTED")) return invalid();
  if (value.status === "cancelled" && (["ROUTING_RUN_REQUEST_REJECTED", "ROUTING_EXECUTION_RECONCILIATION_REQUIRED",
    "ROUTING_EXECUTION_OUTCOME_UNCONFIRMED"].includes(String(value.code))
    || value.root_call_id === null && value.code !== "ROUTING_EXECUTION_CANCELLED")) return invalid();
  return { schema_version: "routing_execution_outcome_v1", run_id: attempt.run_id, attempt_id: attempt.id,
    generation: attempt.generation, status: value.status, code: value.code, root_call_id: value.root_call_id,
    observed_at: value.observed_at, execution_permitted: false, recovery_permitted: false };
}
