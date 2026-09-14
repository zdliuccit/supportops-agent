import { useCallback, useEffect, useMemo, useState } from "react";
import { Search, XCircle } from "lucide-react";

import { getAdminAgent, getAdminUser, getAnalyticsErrorEvent, listAdminAgents, listAnalyticsErrorEvents, listUsers } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { SearchableSelect, type SearchableSelectOption } from "@/components/SearchableSelect";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { DashboardErrorEvent, DashboardErrorEventDetail } from "@/types";

const severityLabel: Record<string, string> = { critical: "严重", error: "错误", warning: "警告", info: "提示" };

export function ErrorAnalysisPage() {
  const [items, setItems] = useState<DashboardErrorEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [days, setDays] = useState("7");
  const [severity, setSeverity] = useState("all");
  const [errorCode, setErrorCode] = useState("");
  const [agentId, setAgentId] = useState("");
  const [userId, setUserId] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<DashboardErrorEventDetail | null>(null);

  const load = useCallback(async () => {
    setError(null); setLoading(true); setRefreshing(true);
    try {
      await delayRequest(() => withRefreshedToken(async (token) => {
        const response = await listAnalyticsErrorEvents(token, { days: Number(days), page, pageSize, severity: severity === "all" ? undefined : severity, errorCode: errorCode.trim() || undefined, agentId: agentId || undefined, userId: userId || undefined });
        setItems(response.items); setTotal(response.total);
      }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "错误数据加载失败"); }
    finally { setLoading(false); setRefreshing(false); }
  }, [agentId, days, errorCode, page, pageSize, severity, userId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setPage(1); }, [agentId, days, severity, errorCode, userId]);

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

  const openDetail = async (eventId: string) => {
    try { await withRefreshedToken(async (token) => setDetail(await getAnalyticsErrorEvent(token, eventId))); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "错误详情加载失败"); }
  };

  const columns = useMemo<AppTableColumn<DashboardErrorEvent>[]>(() => [
    { title: "发生时间", key: "time", render: (_, row) => <span className="whitespace-nowrap text-[#637381]">{new Date(row.occurred_at).toLocaleString("zh-CN")}</span> },
    { title: "严重级别", key: "severity", render: (_, row) => <StatusBadge value={row.severity} label={severityLabel[row.severity] ?? row.severity} /> },
    { title: "错误原因", key: "reason", render: (_, row) => <div className="max-w-[320px]"><p className="truncate font-medium text-[#1c252e]" title={row.reason}>{row.reason || row.error_code}</p><p className="mt-1 font-mono text-xs text-[#919eab]">{row.error_code}</p></div> },
    { title: "Agent", key: "agent", render: (_, row) => <span className="font-medium text-[#1c252e]">{row.agent_name ?? "已删除 Agent"}</span> },
    { title: "用户", key: "user", render: (_, row) => <span className="text-[#637381]">{row.user_name ?? "已删除用户"}</span> },
    { title: "阶段", key: "stage", render: (_, row) => <span className="text-[#637381]">{row.stage}</span> },
    { title: "状态", key: "status", render: (_, row) => <StatusBadge value={row.resolution_status} label={row.resolution_status === "resolved" ? "已处理" : "待处理"} /> },
    { title: "操作", key: "action", fixed: "right", align: "right", minWidth: 112, render: (_, row) => <Button variant="ghost" size="sm" className="text-[#00a76f]" onClick={() => void openDetail(row.id)}>查看详情</Button> },
  ], []);

  return <div className="min-h-[calc(100svh-72px)] bg-white pb-12"><div className="mx-auto max-w-[1600px] space-y-6">
    <PageHeader title="错误分析" description="按事件追踪每次失败的原因、影响对象和恢复进度。" />
    {error && <div className="flex items-center justify-between rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700"><span>{error}</span><Button variant="ghost" size="sm" onClick={() => void load()}>重试</Button></div>}
    <Card className="rounded-2xl border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><ListToolbar onRefresh={() => void load()} loading={refreshing} filters={<div className="flex flex-wrap items-center gap-3"><Select value={days} onValueChange={setDays}><SelectTrigger className="h-9 w-40"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">最近 24 小时</SelectItem><SelectItem value="7">最近 7 天</SelectItem><SelectItem value="30">最近 30 天</SelectItem></SelectContent></Select><SearchableSelect value={agentId} onValueChange={setAgentId} loadOptions={loadAgents} resolveOption={resolveAgent} placeholder="全部 Agent" searchPlaceholder="搜索 Agent 名称或 slug" clearLabel="全部 Agent" className="w-70" /><SearchableSelect value={userId} onValueChange={setUserId} loadOptions={loadUsers} resolveOption={resolveUser} placeholder="全部使用者" searchPlaceholder="搜索姓名或邮箱" clearLabel="全部使用者" className="w-70" /><Select value={severity} onValueChange={setSeverity}><SelectTrigger className="h-9 w-28"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部级别</SelectItem><SelectItem value="critical">严重</SelectItem><SelectItem value="error">错误</SelectItem><SelectItem value="warning">警告</SelectItem></SelectContent></Select><div className="relative w-48"><Search className="absolute left-3 top-2.5 size-4 text-[#919eab]" /><Input className="h-9 pl-9" placeholder="按错误码筛选" value={errorCode} onChange={(event) => setErrorCode(event.target.value)} /></div></div>} /><CardContent className="overflow-x-auto p-0"><div className="relative min-h-[360px] min-w-[1100px]"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="错误事件列表" pagination={{ current: page, pageSize, total, onChange: (nextPage, nextPageSize) => { setPage(nextPage); setPageSize(nextPageSize); } }} emptyText="当前时间范围暂无错误事件" />{loading && <ListLoadingOverlay label="正在加载错误事件…" />}</div></CardContent></Card>
    <AppDrawer
      open={detail !== null}
      onOpenChange={(open) => { if (!open) setDetail(null); }}
      title="错误详情"
      description={detail ? `${detail.error_code} · ${new Date(detail.occurred_at).toLocaleString("zh-CN")}` : undefined}
      showConfirm={false}
      cancelText="关闭"
      className="w-full sm:max-w-xl"
      contentClassName="bg-[#f7f8fa]"
    >
      {detail && <div className="space-y-3"><Card><CardContent className="grid gap-4 p-5 sm:grid-cols-2"><div><p className="text-xs text-[#919eab]">错误原因</p><p className="mt-1 font-medium text-[#1c252e]">{detail.reason || detail.error_code}</p></div><div><p className="text-xs text-[#919eab]">严重级别</p><p className="mt-1"><StatusBadge value={detail.severity} label={severityLabel[detail.severity] ?? detail.severity} /></p></div><div><p className="text-xs text-[#919eab]">Agent / 用户</p><p className="mt-1 text-sm text-[#1c252e]">{detail.agent_name ?? "已删除 Agent"} · {detail.user_name ?? "已删除用户"}</p></div><div><p className="text-xs text-[#919eab]">运行 / 阶段</p><p className="mt-1 break-all font-mono text-xs text-[#637381]">{detail.run_id} · {detail.stage}</p></div><div><p className="text-xs text-[#919eab]">模型版本</p><p className="mt-1 text-sm text-[#637381]">{detail.model_name ?? "—"} · Agent v{detail.agent_version_number ?? "—"}</p></div><div><p className="text-xs text-[#919eab]">延迟 / 重试</p><p className="mt-1 text-sm text-[#637381]">{detail.latency_ms ?? "—"} ms · {detail.retry_count ?? 0} 次</p></div></CardContent></Card><Card><CardHeader><CardTitle className="text-sm">运行时间线</CardTitle></CardHeader><CardContent className="space-y-3">{detail.observations.length === 0 ? <p className="text-sm text-[#919eab]">暂无细粒度追踪信息。</p> : detail.observations.map((observation) => <div key={observation.id} className="flex items-center justify-between rounded-lg bg-[#f7f9fb] px-3 py-2 text-sm"><span className="flex items-center gap-2"><XCircle className="size-4 text-[#ff5630]" />{observation.name}</span><span className="text-xs text-[#919eab]">{observation.duration_ms ?? "—"} ms</span></div>)}</CardContent></Card></div>}
    </AppDrawer>
  </div></div>;
}
