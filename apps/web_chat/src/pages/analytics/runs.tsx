import { useCallback, useEffect, useMemo, useState } from "react";

import { getAdminAgent, getAdminUser, getDashboardTrace, listAdminAgents, listDashboardRuns, listUsers } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { SearchableSelect, type SearchableSelectOption } from "@/components/SearchableSelect";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { useAppSelector } from "@/store/hooks";
import { getDepartmentNamePath, selectOrganizationUnits } from "@/store/organizationUnitsSlice";
import type { DashboardRun, DashboardTrace } from "@/types";

export function AnalyticsRunsPage() {
  const [days, setDays] = useState("7");
  const [status, setStatus] = useState("all");
  const [agentId, setAgentId] = useState("");
  const [userId, setUserId] = useState("");
  const [agentVersionId, setAgentVersionId] = useState("");
  const [modelEndpointVersionId, setModelEndpointVersionId] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [items, setItems] = useState<DashboardRun[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trace, setTrace] = useState<DashboardTrace | null>(null);
  const organizationUnits = useAppSelector(selectOrganizationUnits);

  const load = useCallback(async () => {
    setLoading(true); setRefreshing(true); setError(null);
    try {
      await delayRequest(() => withRefreshedToken(async (token) => {
        const result = await listDashboardRuns(token, { days: Number(days), agentId: agentId || undefined, userId: userId || undefined, agentVersionId: agentVersionId || undefined, modelEndpointVersionId: modelEndpointVersionId || undefined, status: status === "all" ? undefined : status, page, pageSize });
        setItems(result.items); setTotal(result.total);
      }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "调用记录加载失败"); }
    finally { setLoading(false); setRefreshing(false); }
  }, [agentId, agentVersionId, days, modelEndpointVersionId, page, pageSize, status, userId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setPage(1); }, [agentId, agentVersionId, days, modelEndpointVersionId, status, userId]);

  const loadAgents = useCallback(async (keywords: string, pageSize: number): Promise<SearchableSelectOption[]> => {
    const result = await withRefreshedToken((token) => listAdminAgents(token, { page: 1, pageSize, keywords }));
    return result.value.items.map((agent) => ({ value: agent.id, label: agent.name, description: agent.slug }));
  }, []);

  const loadUsers = useCallback(async (keywords: string, pageSize: number): Promise<SearchableSelectOption[]> => {
    const result = await withRefreshedToken((token) => listUsers(token, { page: 1, pageSize, keywords }));
    return result.value.items.map((user) => ({ value: user.id, label: user.display_name, description: user.email }));
  }, []);

  const resolveAgent = useCallback(async (id: string): Promise<SearchableSelectOption | null> => {
    const result = await withRefreshedToken((token) => getAdminAgent(token, id));
    return { value: result.value.id, label: result.value.name, description: result.value.slug };
  }, []);

  const resolveUser = useCallback(async (id: string): Promise<SearchableSelectOption | null> => {
    const result = await withRefreshedToken((token) => getAdminUser(token, id));
    return { value: result.value.id, label: result.value.display_name, description: result.value.email };
  }, []);

  const columns = useMemo<AppTableColumn<DashboardRun>[]>(() => [
    { title: "时间", key: "time", render: (_, row) => <span className="whitespace-nowrap text-[#637381]">{new Date(row.created_at).toLocaleString("zh-CN")}</span> },
    { title: "Agent", key: "agent", render: (_, row) => <span className="font-medium text-[#1c252e]">{row.agent_name}</span> },
    { title: "使用者", key: "user", render: (_, row) => { const departmentPath = getDepartmentNamePath(organizationUnits, row.organization_unit_id).join(" / "); return <div className="min-w-28"><p className="font-medium text-[#1c252e]">{row.user_name ?? "未识别用户"}</p><p className="mt-0.5 text-xs text-[#919eab]">{departmentPath || row.department_name || "未分配部门"}</p></div>; } },
    { title: "状态", key: "status", render: (_, row) => <StatusBadge value={row.status} label={row.status === "completed" ? "已完成" : row.status === "failed" ? "失败" : row.status === "cancelled" ? "已取消" : row.status === "running" ? "运行中" : row.status} /> },
    { title: "错误", key: "error", render: (_, row) => <span className="text-[#637381]">{row.error_code ?? "—"}</span> },
    { title: "耗时", key: "latency", align: "right", render: (_, row) => row.end_to_end_latency_ms ? `${row.end_to_end_latency_ms} ms` : "—" },
    { title: "操作", key: "action", fixed: "right", align: "right", minWidth: 112, render: (_, row) => <Button variant="ghost" size="sm" className="text-[#00a76f]" onClick={() => { void withRefreshedToken(async (token) => setTrace(await getDashboardTrace(token, row.id))); }}>查看链路</Button> },
  ], [organizationUnits]);

  return <div className="min-h-[calc(100svh-72px)] bg-white pb-12"><div className="mx-auto max-w-[1600px] space-y-6"><PageHeader title="调用记录" description="按时间查看所有 Agent 调用、使用者和运行结果。" />{error && <div className="flex items-center justify-between rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700"><span>{error}</span><Button variant="ghost" size="sm" onClick={() => void load()}>重试</Button></div>}<Card className="rounded-2xl border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><ListToolbar onRefresh={() => void load()} loading={refreshing} filters={<div className="flex flex-wrap items-center gap-3"><Select value={days} onValueChange={setDays}><SelectTrigger className="h-9 w-40"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">最近 24 小时</SelectItem><SelectItem value="7">最近 7 天</SelectItem><SelectItem value="30">最近 30 天</SelectItem></SelectContent></Select><SearchableSelect value={agentId} onValueChange={setAgentId} loadOptions={loadAgents} resolveOption={resolveAgent} placeholder="全部 Agent" searchPlaceholder="搜索 Agent 名称或 slug" clearLabel="全部 Agent" className="w-70" /><SearchableSelect value={userId} onValueChange={setUserId} loadOptions={loadUsers} resolveOption={resolveUser} placeholder="全部使用者" searchPlaceholder="搜索姓名或邮箱" clearLabel="全部使用者" className="w-70" /><Input className="h-9 w-52" value={agentVersionId} onChange={(event) => setAgentVersionId(event.target.value)} placeholder="Agent 版本 ID" /><Input className="h-9 w-52" value={modelEndpointVersionId} onChange={(event) => setModelEndpointVersionId(event.target.value)} placeholder="模型版本 ID" /><Select value={status} onValueChange={setStatus}><SelectTrigger className="h-9 w-28"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem><SelectItem value="completed">已完成</SelectItem><SelectItem value="running">运行中</SelectItem><SelectItem value="failed">失败</SelectItem><SelectItem value="cancelled">已取消</SelectItem></SelectContent></Select></div>} /><CardContent className="overflow-x-auto p-0"><div className="relative min-h-[360px] min-w-[1120px]"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="调用记录列表" pagination={{ current: page, pageSize, total, onChange: (nextPage, nextPageSize) => { setPage(nextPage); setPageSize(nextPageSize); } }} emptyText="当前时间范围暂无调用记录" />{loading && <ListLoadingOverlay label="正在加载调用记录…" />}</div></CardContent></Card><AppDrawer
      open={trace !== null}
      onOpenChange={(open) => { if (!open) setTrace(null); }}
      title="运行链路"
      description={trace ? `${trace.run.agent_name} · ${trace.run.correlation_id}` : undefined}
      showConfirm={false}
      cancelText="关闭"
      className="w-full sm:max-w-xl"
      contentClassName="bg-[#f7f8fa]"
    >
      {trace && <div className="space-y-3">{trace.observations.map((item) => <div key={item.id} className="rounded-2xl bg-white p-4"><div className="flex items-center justify-between"><StatusBadge value={item.status} label={item.kind} /><span className="font-medium">{item.name}</span><span className="text-xs text-[#919eab]">{item.duration_ms ?? "—"} ms</span></div></div>)}</div>}
    </AppDrawer></div></div>;
}
