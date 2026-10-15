import type { RunStatus } from "@/types";

/** 明确区分 Run 状态与独立动作的 unknown/reconciling，不猜测未来状态。 */
const RUN_STATUSES: readonly RunStatus[] = ["queued", "running", "cancelling", "waiting_approval", "waiting_action", "completed", "failed", "cancelled"];

export function isRunStatus(value: unknown): value is RunStatus {
  return typeof value === "string" && RUN_STATUSES.some((status) => status === value);
}
