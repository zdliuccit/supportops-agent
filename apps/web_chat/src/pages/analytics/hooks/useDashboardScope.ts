import { useCallback, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { DashboardQuery, RunStatus } from "@/types";

export const runStatusLabels: Record<RunStatus, string> = { queued: "排队中", running: "运行中", cancelling: "取消中",
  waiting_approval: "等待审批", waiting_action: "等待动作", completed: "已完成", failed: "失败", cancelled: "已取消" };
const keys = { days: "days", start: "start", end: "end", agentId: "agent_id", lifecycle: "lifecycle", health: "health",
  agentVersionId: "agent_version_id", modelEndpointVersionId: "model_endpoint_version_id", userId: "user_id",
  organizationUnitId: "organization_unit_id", status: "status" } satisfies Record<keyof DashboardQuery, string>;
export const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const pageValue = (value: string | null, fallback: number, max = Number.MAX_SAFE_INTEGER) => {
  const number = Number(value);
  return value && Number.isSafeInteger(number) && number > 0 && number <= max ? number : fallback;
};

/** 范围保存在 URL，刷新/后退/下钻复用同一带时区区间；非法条件不扩大查询。 */
export function useDashboardScope(fixedAgentId?: string) {
  const [params, setParams] = useSearchParams();
  const serialized = params.toString();
  const { scope, error } = useMemo(() => {
    const values = new URLSearchParams(serialized);
    const days = pageValue(values.get("days"), 7, 90);
    const agentId = fixedAgentId ?? values.get("agent_id") ?? undefined;
    const lifecycle = values.get("lifecycle") || undefined;
    const health = values.get("health") || undefined;
    const status = values.get("status") || undefined;
    const start = values.get("start") || undefined, end = values.get("end") || undefined;
    const ids = { agentId, userId: values.get("user_id") || undefined, organizationUnitId: values.get("organization_unit_id") || undefined,
      agentVersionId: values.get("agent_version_id") || undefined, modelEndpointVersionId: values.get("model_endpoint_version_id") || undefined };
    let error: string | null = null;
    if (values.has("days") && String(days) !== values.get("days")) error = "时间范围须为 1–90 的整数天数";
    if (Object.values(ids).some((value) => value && !uuidPattern.test(value))) error = "Agent、用户和版本标识须为有效 UUID";
    if (lifecycle && !["draft", "active", "disabled"].includes(lifecycle)) error = "生命周期筛选无效";
    if (health && !["healthy", "degraded", "unknown", "no_recent_activity"].includes(health)) error = "健康筛选无效";
    if (status && !Object.hasOwn(runStatusLabels, status)) error = "运行状态筛选无效";
    if (start || end) {
      const validTime = (value: string | undefined) => value && /(Z|[+-]\d{2}:\d{2})$/i.test(value) && Number.isFinite(Date.parse(value));
      if (!validTime(start) || !validTime(end) || Date.parse(start!) >= Date.parse(end!) || Date.parse(end!) - Date.parse(start!) > 90 * 86400000)
        error = "起止时间须带时区、递增且不超过 90 天";
    }
    return { scope: { days, ...ids, start, end, lifecycle: lifecycle as DashboardQuery["lifecycle"],
      health: health as DashboardQuery["health"], status: status as DashboardQuery["status"] } satisfies DashboardQuery, error };
  }, [fixedAgentId, serialized]);

  useEffect(() => {
    if (fixedAgentId && params.get("agent_id") !== fixedAgentId) {
      setParams((old) => { const next = new URLSearchParams(old); next.set("agent_id", fixedAgentId); return next; }, { replace: true });
      return;
    }
    if (params.has("start") || params.has("end")) return;
    const end = new Date();
    setParams((old) => { const next = new URLSearchParams(old); next.set("start", new Date(end.getTime() - (scope.days ?? 7) * 86400000).toISOString());
      next.set("end", end.toISOString()); return next; }, { replace: true });
  }, [fixedAgentId, serialized, scope.days, setParams]);

  const change = useCallback((key: keyof DashboardQuery, value: string) => {
    setParams((old) => {
      const next = new URLSearchParams(old);
      if (value) next.set(keys[key], value); else next.delete(keys[key]);
      if (key === "days") { const end = new Date(); next.set("start", new Date(end.getTime() - Number(value) * 86400000).toISOString()); next.set("end", end.toISOString()); }
      next.set("page", "1"); next.set("status_page", "1"); return next;
    }, { replace: true });
  }, [setParams]);
  const href = useCallback((path: string, overrides: Partial<DashboardQuery> = {}) => {
    const next = new URLSearchParams();
    Object.entries({ ...scope, ...overrides }).forEach(([key, value]) => { if (value !== undefined) next.set(keys[key as keyof DashboardQuery], String(value)); });
    next.set("page", "1"); return `${path}?${next}`;
  }, [scope]);
  const pagination = useCallback((page: number, pageSize: number, prefix = "") => {
    setParams((old) => { const next = new URLSearchParams(old); next.set(`${prefix}page`, String(page)); next.set(`${prefix}page_size`, String(pageSize)); return next; }, { replace: true });
  }, [setParams]);
  return { scope, error, change, href, pagination, page: pageValue(params.get("page"), 1), pageSize: pageValue(params.get("page_size"), 20, 100),
    statusPage: pageValue(params.get("status_page"), 1), statusPageSize: pageValue(params.get("status_page_size"), 20, 100) };
}
