import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  Clock3,
  Database,
  Gauge,
  LayoutDashboard,
  RefreshCw,
  Server,
  Sparkles,
  Users,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  getAdminAgent,
  getDashboardSummary,
  getDashboardTimeseries,
  getDashboardTrace,
  getSystemRankings,
  listDashboardErrors,
  listDashboardRuns,
  listSystemAgentStatus,
} from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { useAppSelector } from "@/store/hooks";
import { getDepartmentNamePath, selectOrganizationUnits } from "@/store/organizationUnitsSlice";
import type {
  AdminAgent,
  DashboardError,
  DashboardRun,
  DashboardSummary,
  DashboardTimeseriesPoint,
  DashboardTrace,
  SystemAgentStatus,
} from "@/types";

type DashboardView = "system" | "agent" | "rankings" | "statuses";

const colors = {
  green: { bg: "#d4f8e4", strong: "#00a76f", text: "#087f5b" },
  purple: { bg: "#ead8ff", strong: "#8e33ff", text: "#5b21b6" },
  blue: { bg: "#d9ecff", strong: "#1877f2", text: "#155eef" },
  amber: { bg: "#fff0bd", strong: "#f59e0b", text: "#8a5200" },
  coral: { bg: "#ff5630", strong: "#ffffff", text: "#ffffff" },
} as const;

const metricLabels: Record<string, string> = {
  total_users: "用户总数",
  active_users: "活跃用户",
  active_users_5m: "近 5 分钟活跃",
  conversations: "新增会话",
  agent_runs: "Agent 调用",
  success_rate: "成功率",
  error_count: "错误数",
  input_tokens: "输入 Token",
  output_tokens: "输出 Token",
  total_cost_microusd: "估算成本",
  p95_latency_ms: "P95 延迟",
};

function formatMetric(key: string, metric: DashboardSummary["metrics"][string] | undefined): string {
  if (!metric?.available || metric.value === null || metric.value === undefined) return "—";
  if (key === "success_rate") return `${metric.value}%`;
  if (key === "total_cost_microusd") return `$${(metric.value / 1_000_000).toFixed(2)}`;
  if (key === "p95_latency_ms") return `${metric.value} ms`;
  return Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(metric.value);
}

function TrendChart({ points, valueKey, color }: { points: DashboardTimeseriesPoint[]; valueKey: string; color: string }) {
  const data = points.map((point) => ({
    label: new Date(point.bucket_start).toLocaleDateString("zh-CN", { month: "short", day: "numeric" }),
    value: Number(point.values[valueKey] ?? 0),
  }));
  if (data.length === 0) return <div className="mt-5 grid h-44 place-items-center rounded-2xl bg-[#fbfcfd] text-sm text-[#919eab]">当前时间范围暂无趋势数据</div>;
  const gradientId = `fill-${valueKey.replace(/[^a-z0-9]/gi, "-")}`;
  return (
    <div className="mt-5 h-52 rounded-2xl bg-[#fbfcfd] px-2 py-3">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}>
          <defs><linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity={0.28} /><stop offset="100%" stopColor={color} stopOpacity={0.02} /></linearGradient></defs>
          <CartesianGrid stroke="#e7ebef" strokeDasharray="4 4" vertical={false} />
          <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: "#919eab", fontSize: 11 }} tickMargin={10} minTickGap={28} />
          <YAxis axisLine={false} tickLine={false} tick={{ fill: "#919eab", fontSize: 11 }} tickFormatter={(value: number) => Intl.NumberFormat("zh-CN", { notation: "compact" }).format(value)} width={42} />
          <Tooltip cursor={{ stroke: color, strokeOpacity: 0.22 }} contentStyle={{ border: "1px solid #e6e8eb", borderRadius: 12, boxShadow: "0 8px 24px rgba(28,37,46,.12)", fontSize: 12 }} labelStyle={{ color: "#637381", marginBottom: 4 }} formatter={(value) => [Intl.NumberFormat("zh-CN").format(Number(value ?? 0)), "数值"]} />
          <Area type="monotone" dataKey="value" stroke={color} strokeWidth={2.5} fill={`url(#${gradientId})`} dot={{ r: 2.5, fill: "#fff", stroke: color, strokeWidth: 1.8 }} activeDot={{ r: 5, fill: color, stroke: "#fff", strokeWidth: 2 }} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function KpiCard({ icon: Icon, label, metric, metricKey, tone }: { icon: LucideIcon; label: string; metric: DashboardSummary["metrics"][string] | undefined; metricKey: string; tone: keyof typeof colors }) {
  const palette = colors[tone];
  const change = metric?.change_percent;
  return <Card className="overflow-hidden rounded-2xl border-0 shadow-[0_12px_26px_rgba(28,37,46,.07)]" style={{ borderRadius: "18px", backgroundColor: palette.bg, backgroundImage: `radial-gradient(rgba(255,255,255,.52) 1px, transparent 1px), linear-gradient(135deg, rgba(255,255,255,.2), rgba(255,255,255,0))`, backgroundSize: "14px 14px, 100% 100%" }}><CardContent className="relative p-5"><div className="absolute -bottom-10 -left-8 size-28 rounded-full bg-white/20 blur-sm" /><div className="relative flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-sm font-semibold" style={{ color: palette.text }}>{label}</p><p className="mt-3 text-[30px] font-bold leading-none tracking-[-1.2px]" style={{ color: palette.text }}>{formatMetric(metricKey, metric)}</p><div className="mt-4 flex items-center gap-1 text-xs font-medium" style={{ color: palette.text }}>{change !== null && change !== undefined ? <><span>{change >= 0 ? <ArrowUpRight className="inline size-3.5" /> : <ArrowDownRight className="inline size-3.5" />}{Math.abs(change)}%</span><span className="opacity-70">较上一周期</span></> : <span className="opacity-70">当前统计周期</span>}</div></div><span className="relative grid size-12 shrink-0 place-items-center rounded-2xl bg-white/60 shadow-sm" style={{ color: palette.strong }}><Icon className="size-5" /></span></div></CardContent></Card>;
}

function DonutCard({ summary, statuses }: { summary: DashboardSummary | null; statuses: SystemAgentStatus[] }) {
  const healthy = statuses.filter((item) => item.health_status === "healthy").length;
  const degraded = statuses.filter((item) => item.health_status === "degraded").length;
  const unknown = Math.max((summary?.status_counts.total ?? statuses.length) - healthy - degraded, 0);
  const total = Math.max(healthy + degraded + unknown, 1);
  const healthyEnd = (healthy / total) * 360;
  const degradedEnd = healthyEnd + (degraded / total) * 360;
  const gradient = `conic-gradient(#00a76f 0deg ${healthyEnd}deg, #ffab00 ${healthyEnd}deg ${degradedEnd}deg, #dce2e8 ${degradedEnd}deg 360deg)`;
  return <Card className="flex h-full flex-col border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><CardHeader className="flex-row items-start justify-between space-y-0 pb-2"><div><CardTitle className="text-base">Agent 健康分布</CardTitle><p className="mt-1 text-sm text-[#919eab]">按当前租户 Agent 状态汇总</p></div><Activity className="size-5 text-[#919eab]" /></CardHeader><CardContent className="flex min-h-[235px] flex-1 items-center justify-between gap-6 py-6"><div className="relative grid size-48 shrink-0 place-items-center rounded-full" style={{ background: gradient }}><div className="grid size-32 place-items-center rounded-full bg-white"><div className="text-center"><p className="text-3xl font-bold text-[#1c252e]">{summary?.status_counts.total ?? 0}</p><p className="text-xs text-[#919eab]">Agent</p></div></div></div><div className="min-w-0 flex-1 space-y-5 text-sm"><div className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 text-[#637381]"><i className="size-2.5 rounded-full bg-[#00a76f]" />健康</span><span className="font-semibold text-[#1c252e]">{healthy}</span></div><div className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 text-[#637381]"><i className="size-2.5 rounded-full bg-[#ffab00]" />需关注</span><span className="font-semibold text-[#1c252e]">{degraded}</span></div><div className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 text-[#637381]"><i className="size-2.5 rounded-full bg-[#dce2e8]" />未知/无活动</span><span className="font-semibold text-[#1c252e]">{unknown}</span></div></div></CardContent></Card>;
}

function DashboardPage({ view }: { view: DashboardView }) {
  const { agentId } = useParams();
  const organizationUnits = useAppSelector(selectOrganizationUnits);
  const isSystem = view !== "agent";
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [points, setPoints] = useState<DashboardTimeseriesPoint[]>([]);
  const [runs, setRuns] = useState<DashboardRun[]>([]);
  const [runTotal, setRunTotal] = useState(0);
  const [errors, setErrors] = useState<DashboardError[]>([]);
  const [agent, setAgent] = useState<AdminAgent | null>(null);
  const [statuses, setStatuses] = useState<SystemAgentStatus[]>([]);
  const [statusTotal, setStatusTotal] = useState(0);
  const [rankings, setRankings] = useState<Array<{ agent_id: string; agent_name: string; value: number | null }>>([]);
  const [days, setDays] = useState("7");
  const [statusFilter, setStatusFilter] = useState("all");
  const [runPage, setRunPage] = useState(1);
  const [runPageSize, setRunPageSize] = useState(10);
  const [statusPage, setStatusPage] = useState(1);
  const [statusPageSize, setStatusPageSize] = useState(10);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [trace, setTrace] = useState<DashboardTrace | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null); setRefreshing(true);
    try {
      await delayRequest(() => withRefreshedToken(async (token) => {
        const [nextSummary, nextPoints, nextRuns, nextErrors] = await Promise.all([
          getDashboardSummary(token, { days: Number(days), agentId, system: isSystem }),
          getDashboardTimeseries(token, { days: Number(days), agentId, system: isSystem }),
          listDashboardRuns(token, { days: Number(days), agentId, status: statusFilter === "all" ? undefined : statusFilter, page: runPage, pageSize: runPageSize }),
          listDashboardErrors(token, { days: Number(days), agentId }),
        ]);
        setSummary(nextSummary); setPoints(nextPoints.items); setRuns(nextRuns.items); setRunTotal(nextRuns.total); setErrors(nextErrors.items);
        if (agentId && !isSystem) setAgent(await getAdminAgent(token, agentId));
        if (isSystem) {
          const [nextStatuses, nextRankings] = await Promise.all([listSystemAgentStatus(token, { page: statusPage, pageSize: statusPageSize }), getSystemRankings(token, Number(days))]);
          setStatuses(nextStatuses.items); setStatusTotal(nextStatuses.total); setRankings(nextRankings.rankings.agent_runs ?? []);
        }
      }));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Dashboard 数据加载失败"); }
    finally { setLoading(false); setRefreshing(false); }
  }, [agentId, days, isSystem, runPage, runPageSize, statusFilter, statusPage, statusPageSize]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setRunPage(1); setStatusPage(1); }, [agentId, days, isSystem, statusFilter]);

  const runColumns = useMemo<AppTableColumn<DashboardRun>[]>(() => [
    { title: "时间", key: "time", render: (_, row) => <span className="whitespace-nowrap text-[#637381]">{new Date(row.created_at).toLocaleString("zh-CN")}</span> },
    ...(isSystem ? [{ title: "Agent", key: "agent", render: (_: unknown, row: DashboardRun) => <span className="font-medium text-[#1c252e]">{row.agent_name}</span> } satisfies AppTableColumn<DashboardRun>] : []),
    { title: "使用者", key: "user", render: (_, row) => { const departmentPath = getDepartmentNamePath(organizationUnits, row.organization_unit_id).join(" / "); return <div className="min-w-28"><p className="font-medium text-[#1c252e]">{row.user_name ?? "未识别用户"}</p><p className="mt-0.5 text-xs text-[#919eab]">{departmentPath || row.department_name || "未分配部门"}</p></div>; } },
    { title: "状态", key: "status", render: (_, row) => <StatusBadge value={row.status} /> },
    { title: "错误", key: "error", render: (_, row) => <span className="text-[#637381]">{row.error_code ?? "—"}</span> },
    { title: "耗时", key: "latency", align: "right", render: (_, row) => row.end_to_end_latency_ms ? `${row.end_to_end_latency_ms} ms` : "—" },
    { title: "操作", key: "action", fixed: "right", align: "right", minWidth: 112, render: (_, row) => <Button variant="ghost" size="sm" className="text-[#00a76f] hover:bg-[#e8f7ef] hover:text-[#008f63]" onClick={() => { void withRefreshedToken((token) => getDashboardTrace(token, row.id).then(setTrace)); }}>查看链路</Button> },
  ], [isSystem, organizationUnits]);

  const statusColumns = useMemo<AppTableColumn<SystemAgentStatus>[]>(() => [
    { title: "Agent", key: "agent", render: (_, row) => <div><p className="font-medium text-[#1c252e]">{row.name}</p><p className="mt-1 max-w-48 truncate text-xs text-[#919eab]" title={row.health_reason}>{row.health_reason}</p></div> },
    { title: "生命周期", key: "lifecycle", render: (_, row) => <StatusBadge value={row.lifecycle_status} /> },
    { title: "执行状态", key: "execution", render: (_, row) => <StatusBadge value={row.execution_status} /> },
    { title: "健康", key: "health", render: (_, row) => <StatusBadge value={row.health_status} /> },
    { title: "错误率", key: "error_rate", align: "right", render: (_, row) => row.error_rate === null ? "—" : `${row.error_rate}%` },
    { title: "P95", key: "p95", align: "right", render: (_, row) => row.p95_latency_ms ? `${row.p95_latency_ms} ms` : "—" },
    { title: "版本", key: "pending", render: (_, row) => row.pending_publish ? <StatusBadge value="pending_publish" label="待发布" /> : <StatusBadge value="completed" label="已同步" /> },
  ], []);

  if (loading) return <div className="space-y-5"><Skeleton className="h-28 w-full" /><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-36" />)}</div><Skeleton className="h-80 w-full" /></div>;

  const kpis: Array<[LucideIcon, string, keyof typeof colors]> = [[Users, "total_users", "green"], [Activity, "active_users", "purple"], [Bot, "agent_runs", "blue"], [Gauge, "success_rate", "amber"]];
  const overviewOnlyClass = view === "system"
    ? "[&>div>section:nth-of-type(4)]:hidden [&>div>section:nth-of-type(5)]:hidden [&>div>section:nth-of-type(6)]:hidden [&>div>section:nth-of-type(7)]:hidden"
    : "";
  return <div className={`-mx-5 -mb-12 min-h-[calc(100svh-72px)] bg-white px-5 pb-12 lg:-mx-6 lg:px-8 ${overviewOnlyClass}`}><div className="mx-auto max-w-[1600px] space-y-6">
    <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"><div><div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[.14em] text-[#00a76f]"><LayoutDashboard className="size-4" />运营分析</div><h1 className="text-[30px] font-bold tracking-[-.8px] text-[#1c252e]">{isSystem ? "运营总览" : `${agent?.name ?? "Agent"} 运行分析`}</h1><p className="mt-2 max-w-2xl text-sm text-[#637381]">{isSystem ? "统一掌握 Agent 库存、服务健康与企业级使用趋势。" : "查看当前 Agent 的调用质量、资源消耗和运行链路。"}</p></div><div className="flex items-center gap-2"><Select value={days} onValueChange={setDays}><SelectTrigger className="h-10 w-36 border-0 bg-white shadow-[0_4px_16px_rgba(28,37,46,.06)]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">最近 24 小时</SelectItem><SelectItem value="7">最近 7 天</SelectItem><SelectItem value="30">最近 30 天</SelectItem></SelectContent></Select><Button variant="outline" size="icon" className="h-10 w-10 border-0 bg-white shadow-[0_4px_16px_rgba(28,37,46,.06)]" onClick={() => void load()} disabled={refreshing} aria-label="刷新 Dashboard"><RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} /></Button></div></header>
    {error && <div className="flex items-center justify-between rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-700"><span>{error}</span><Button variant="ghost" size="sm" onClick={() => void load()}>重试</Button></div>}
    {summary?.service.status !== "healthy" && <div className="flex items-center gap-3 rounded-2xl border border-amber-100 bg-[#fff9e9] px-4 py-3 text-sm text-[#8b5e00]"><Server className="size-4" />执行服务当前{summary?.service.status === "unavailable" ? "不可用" : "状态待确认"}，部分运行指标可能暂时缺失。</div>}
    {!isSystem && agent?.active_version_config_digest && <div className="flex items-center gap-3 rounded-2xl border border-[#bdebdc] bg-[#effbf5] px-4 py-3 text-sm text-[#087f5b]"><Sparkles className="size-4" /><span>当前运行版本已固定，修改配置后需要重新发布并重启 Agent。</span><Link className="ml-auto font-semibold underline" to={`/agent-management/agents/${agent.id}`}>查看配置</Link></div>}
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">{kpis.map(([Icon, key, tone]) => <KpiCard key={key} icon={Icon} label={metricLabels[key]} metric={summary?.metrics[key]} metricKey={key} tone={tone} />)}</section>
    <section className="grid gap-5 xl:grid-cols-[1.55fr_1fr]"><Card className="border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><CardHeader className="flex-row items-start justify-between space-y-0 pb-2"><div><CardTitle className="text-base">调用趋势</CardTitle><p className="mt-1 text-sm text-[#919eab]">查看时间范围内的调用活跃度</p></div><Badge variant="outline" className="border-[#bdebdc] bg-[#effbf5] text-[#087f5b]">{points.length ? `${points.length} 个时间桶` : "暂无数据"}</Badge></CardHeader><CardContent><TrendChart points={points} valueKey="agent_runs" color={colors.green.strong} /><div className="mt-5 grid grid-cols-3 gap-4 border-t border-dashed border-[#e6e8eb] pt-4"><div><p className="text-xs text-[#919eab]">输入 Token</p><p className="mt-1 font-semibold">{formatMetric("input_tokens", summary?.metrics.input_tokens)}</p></div><div><p className="text-xs text-[#919eab]">输出 Token</p><p className="mt-1 font-semibold">{formatMetric("output_tokens", summary?.metrics.output_tokens)}</p></div><div><p className="text-xs text-[#919eab]">P95 延迟</p><p className="mt-1 font-semibold">{formatMetric("p95_latency_ms", summary?.metrics.p95_latency_ms)}</p></div></div></CardContent></Card>{isSystem ? <DonutCard summary={summary} statuses={statuses} /> : <Card className="border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><CardHeader className="flex-row items-start justify-between space-y-0 pb-2"><div><CardTitle className="text-base">运行质量</CardTitle><p className="mt-1 text-sm text-[#919eab]">成功、失败与服务响应概况</p></div><Gauge className="size-5 text-[#919eab]" /></CardHeader><CardContent className="space-y-5 pt-5"><div className="flex items-end justify-between"><div><p className="text-4xl font-bold text-[#00a76f]">{formatMetric("success_rate", summary?.metrics.success_rate)}</p><p className="mt-1 text-sm text-[#637381]">当前周期成功率</p></div><CheckCircle2 className="size-9 text-[#00a76f]" /></div><div className="h-3 overflow-hidden rounded-full bg-[#eef1f4]"><div className="h-full rounded-full bg-[#00a76f]" style={{ width: `${Math.min(Number(summary?.metrics.success_rate?.value ?? 0), 100)}%` }} /></div><div className="grid grid-cols-3 gap-3 text-sm"><div><p className="text-xs text-[#919eab]">已完成</p><p className="mt-1 font-semibold">{summary?.status_counts.completed ?? 0}</p></div><div><p className="text-xs text-[#919eab]">失败</p><p className="mt-1 font-semibold text-[#ff5630]">{summary?.status_counts.failed ?? 0}</p></div><div><p className="text-xs text-[#919eab]">取消</p><p className="mt-1 font-semibold">{summary?.status_counts.cancelled ?? 0}</p></div></div></CardContent></Card>}</section>
    <section className="grid gap-5 lg:grid-cols-2"><Card className="border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><CardHeader className="flex-row items-start justify-between space-y-0 pb-2"><div><CardTitle className="text-base">资源消耗</CardTitle><p className="mt-1 text-sm text-[#919eab]">Token 与成本走势</p></div><Database className="size-5 text-[#1877f2]" /></CardHeader><CardContent><TrendChart points={points} valueKey="output_tokens" color={colors.blue.strong} /></CardContent></Card><Card className="border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><CardHeader className="flex-row items-start justify-between space-y-0 pb-2"><div><CardTitle className="text-base">错误与成本</CardTitle><p className="mt-1 text-sm text-[#919eab]">异常数量和估算成本趋势</p></div><AlertTriangle className="size-5 text-[#ffab00]" /></CardHeader><CardContent><TrendChart points={points} valueKey="error_count" color="#ff5630" /><div className="mt-3 flex justify-between text-sm"><span className="text-[#637381]">估算成本</span><span className="font-semibold">{formatMetric("total_cost_microusd", summary?.metrics.total_cost_microusd)}</span></div></CardContent></Card></section>
    {isSystem && <section className="space-y-3"><div className="flex items-end justify-between"><div><h2 className="text-lg font-bold text-[#1c252e]">Agent 调用排行</h2><p className="mt-1 text-sm text-[#919eab]">按当前时间范围统计调用量</p></div><span className="text-xs text-[#919eab]">Top 5</span></div><div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">{rankings.slice(0, 5).map((item, index) => <Card key={item.agent_id} className="border-0 shadow-[0_8px_24px_rgba(28,37,46,.045)]"><CardContent className="p-4"><div className="flex items-center justify-between text-xs font-semibold text-[#919eab]"><span>TOP {index + 1}</span><Bot className="size-4 text-[#00a76f]" /></div><p className="mt-3 truncate font-semibold" title={item.agent_name}>{item.agent_name}</p><p className="mt-1 text-2xl font-bold text-[#1c252e]">{item.value ?? 0}</p><p className="text-xs text-[#637381]">次调用</p></CardContent></Card>)}</div></section>}
    {isSystem && <section className="space-y-3"><div className="flex items-end justify-between"><div><h2 className="text-lg font-bold text-[#1c252e]">Agent 状态</h2><p className="mt-1 text-sm text-[#919eab]">生命周期、执行态与健康态分开展示</p></div><span className="text-xs text-[#919eab]">共 {statusTotal} 个 Agent</span></div><Card className="overflow-hidden border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><CardContent className="p-0"><AppTable columns={statusColumns} dataSource={statuses} rowKey="id" ariaLabel="Agent 状态列表" pagination={{ current: statusPage, pageSize: statusPageSize, total: statusTotal, onChange: (page, pageSize) => { setStatusPage(page); setStatusPageSize(pageSize); } }} /></CardContent></Card></section>}
    <section className="space-y-3"><div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-lg font-bold text-[#1c252e]">最近调用</h2><p className="mt-1 text-sm text-[#919eab]">最近 {days === "1" ? "24 小时" : `${days} 天`}，共 {runTotal} 次调用</p></div><div className="flex items-center gap-2"><Select value={statusFilter} onValueChange={setStatusFilter}><SelectTrigger className="h-9 w-32 border-[#e6e8eb] bg-white"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem><SelectItem value="completed">已完成</SelectItem><SelectItem value="failed">失败</SelectItem><SelectItem value="cancelled">已取消</SelectItem><SelectItem value="running">运行中</SelectItem></SelectContent></Select><Clock3 className="hidden size-4 text-[#919eab] sm:block" /></div></div><Card className="overflow-hidden border-0 shadow-[0_10px_30px_rgba(28,37,46,.05)]"><CardContent className="p-0"><AppTable columns={runColumns} dataSource={runs} rowKey="id" ariaLabel="Agent 调用记录" pagination={{ current: runPage, pageSize: runPageSize, total: runTotal, onChange: (page, pageSize) => { setRunPage(page); setRunPageSize(pageSize); } }} emptyText="当前时间范围暂无调用记录" /></CardContent></Card></section>
    {errors.length > 0 && <section className="space-y-3"><div><h2 className="text-lg font-bold text-[#1c252e]">错误分析</h2><p className="mt-1 text-sm text-[#919eab]">按错误码聚合，帮助快速定位异常</p></div><div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">{errors.slice(0, 6).map((item) => <Card key={item.error_code} className="border-0 bg-[#ff5630] text-white shadow-[0_8px_24px_rgba(255,86,48,.18)]"><CardContent className="p-4"><div className="flex items-center justify-between gap-3"><span className="truncate font-mono text-sm text-white">{item.error_code}</span><Badge className="border-white/30 bg-white/20 text-white hover:bg-white/20" variant="outline">{item.count}</Badge></div><p className="mt-2 text-xs text-white/75">影响会话 {item.affected_users} · 涉及 Agent {item.agent_count}</p></CardContent></Card>)}</div></section>}
    <AppDrawer
      open={trace !== null}
      onOpenChange={(open) => { if (!open) setTrace(null); }}
      title="运行链路"
      description={trace ? `${trace.run.agent_name} · ${trace.run.correlation_id}` : undefined}
      showConfirm={false}
      cancelText="关闭"
      className="w-full sm:max-w-xl"
      contentClassName="bg-[#f7f8fa]"
    >
      {trace && <div className="space-y-3">{trace.observations.length === 0 ? <div className="rounded-2xl bg-white p-5 text-sm text-[#637381]">当前运行还没有细粒度观测步骤。</div> : trace.observations.map((item) => <div key={item.id} className="rounded-2xl border-0 bg-white p-4 shadow-[0_6px_20px_rgba(28,37,46,.04)]"><div className="flex items-center justify-between"><div className="flex items-center gap-2"><StatusBadge value={item.status} label={item.kind} /><span className="font-semibold">{item.name}</span></div><span className="text-xs text-[#919eab]">{item.duration_ms ? `${item.duration_ms} ms` : "—"}</span></div>{item.error_code && <p className="mt-2 flex items-center gap-1 text-sm text-[#ff5630]"><XCircle className="size-4" />{item.error_code}</p>}</div>)}</div>}
    </AppDrawer>
  </div></div>;
}

export function SystemDashboardPage() { return <DashboardPage view="system" />; }
export function SystemRankingsPage() { return <DashboardPage view="rankings" />; }
export function SystemAgentStatusPage() { return <DashboardPage view="statuses" />; }
export function AgentDashboardPage() { return <DashboardPage view="agent" />; }
