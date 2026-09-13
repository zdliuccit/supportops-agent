import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { getSystemRankings, listSystemAgentStatus } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { SystemAgentStatus } from "@/types";

export function AnalyticsRankingsPage() {
  const [days, setDays] = useState(7);
  const [items, setItems] = useState<Array<{ agent_id: string; agent_name: string; value: number | null }>>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = async () => { setLoading(true); setRefreshing(true); setError(null); try { await delayRequest(() => withRefreshedToken(async (token) => setItems((await getSystemRankings(token, days)).rankings.agent_runs ?? []))); } catch (cause) { setError(cause instanceof Error ? cause.message : "排行数据加载失败"); } finally { setLoading(false); setRefreshing(false); } };
  useEffect(() => { void load(); }, [days]);
  return <AnalyticsPageShell title="Agent 调用排行" description="按时间范围比较调用规模，识别使用最活跃的 Agent。" error={error} onRetry={() => void load()} refreshing={refreshing}>
    <ListToolbar onRefresh={() => void load()} loading={refreshing} filters={<div className="flex items-center gap-1 rounded-xl bg-[#f7f9fb] p-1"><button className={`rounded-lg px-3 py-1.5 text-sm ${days === 1 ? "bg-white font-semibold shadow-sm" : "text-[#637381]"}`} onClick={() => setDays(1)}>24 小时</button><button className={`rounded-lg px-3 py-1.5 text-sm ${days === 7 ? "bg-white font-semibold shadow-sm" : "text-[#637381]"}`} onClick={() => setDays(7)}>7 天</button><button className={`rounded-lg px-3 py-1.5 text-sm ${days === 30 ? "bg-white font-semibold shadow-sm" : "text-[#637381]"}`} onClick={() => setDays(30)}>30 天</button></div>} />
    <div className="relative min-h-[180px]"><div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">{items.length === 0 ? <div className="col-span-full rounded-xl bg-[#f7f9fb] p-10 text-center text-sm text-[#919eab]">当前时间范围暂无排行数据</div> : items.map((item, index) => <Card key={item.agent_id} className="rounded-2xl border-[#eef1f4] shadow-[0_8px_24px_rgba(28,37,46,.05)]"><CardContent className="p-5"><div className="flex items-center justify-between text-xs font-semibold text-[#919eab]"><span>TOP {index + 1}</span><span className="rounded-full bg-[#e8f7f3] px-2 py-1 text-[#00a76f]">{item.value ?? 0} 次</span></div><p className="mt-5 truncate font-semibold text-[#1c252e]" title={item.agent_name}>{item.agent_name}</p><div className="mt-3 h-2 overflow-hidden rounded-full bg-[#eef1f4]"><div className="h-full rounded-full bg-[#00a76f]" style={{ width: `${Math.min(100, Math.max(4, (item.value ?? 0) / Math.max(items[0]?.value ?? 1, 1) * 100))}%` }} /></div></CardContent></Card>)}</div>{loading && <ListLoadingOverlay label="正在加载调用排行…" />}</div>
  </AnalyticsPageShell>;
}

export function AnalyticsAgentStatusPage() {
  const [items, setItems] = useState<SystemAgentStatus[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const load = async () => { setLoading(true); setRefreshing(true); try { await delayRequest(() => withRefreshedToken(async (token) => { const result = await listSystemAgentStatus(token, { page: 1, pageSize: 100 }); setItems(result.items); setTotal(result.total); })); } finally { setLoading(false); setRefreshing(false); } };
  useEffect(() => { void load(); }, []);
  const columns: AppTableColumn<SystemAgentStatus>[] = [
    { title: "Agent", key: "agent", render: (_, row) => <div><p className="font-medium text-[#1c252e]">{row.name}</p><p className="mt-1 text-xs text-[#919eab]">{row.health_reason}</p></div> },
    { title: "生命周期", key: "lifecycle", render: (_, row) => <StatusBadge value={row.lifecycle_status} /> },
    { title: "执行状态", key: "execution", render: (_, row) => <StatusBadge value={row.execution_status} /> },
    { title: "健康", key: "health", render: (_, row) => <StatusBadge value={row.health_status} /> },
    { title: "错误率", key: "error_rate", align: "right", render: (_, row) => row.error_rate === null ? "—" : `${row.error_rate}%` },
    { title: "运行数据", key: "action", align: "right", render: (_, row) => <Link className="text-sm font-medium text-[#00a76f]" to={`/analytics/agents/${row.id}`}>查看分析</Link> },
  ];
  return <AnalyticsPageShell title="Agent 状态" description={`当前租户共 ${total} 个 Agent，查看生命周期、执行态与健康度。`} error={null} onRetry={() => void load()} refreshing={refreshing}><Card className="overflow-hidden rounded-2xl border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><ListToolbar title="运行状态列表" onRefresh={() => void load()} loading={refreshing} /><CardContent className="overflow-x-auto p-0"><div className="relative min-h-[320px]"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="Agent 状态列表" emptyText="暂无 Agent 状态数据" />{loading && <ListLoadingOverlay label="正在加载 Agent 状态…" />}</div></CardContent></Card></AnalyticsPageShell>;
}

function AnalyticsPageShell({ title, description, error, onRetry, refreshing: _refreshing, children }: { title: string; description: string; error: string | null; onRetry: () => void; refreshing: boolean; children: ReactNode }) {
  return <div className="min-h-[calc(100svh-72px)] bg-white pb-12"><div className="mx-auto max-w-[1600px] space-y-6"><PageHeader title={title} description={description} />{error && <div className="flex items-center justify-between rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700"><span>{error}</span><Button variant="ghost" size="sm" onClick={onRetry}>重试</Button></div>}{children}</div></div>;
}
