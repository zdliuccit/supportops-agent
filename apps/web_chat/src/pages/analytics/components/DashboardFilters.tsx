import { useCallback, useId } from "react";
import { getAdminAgent, getAdminUser, listAdminAgents, listUsers } from "@/api";
import { DepartmentTreeSelect } from "@/components/DepartmentTreeSelect";
import { SearchableSelect, type SearchableSelectOption } from "@/components/SearchableSelect";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import type { DashboardQuery } from "@/types";
import { runStatusLabels, uuidPattern } from "../hooks/useDashboardScope";

/** 各分析视图共用筛选控件；实际数据请求由页面持有。 */
export function DashboardFilters({ scope, onChange, fixedAgent = false }: { scope: DashboardQuery;
  onChange: (key: keyof DashboardQuery, value: string) => void; fixedAgent?: boolean }) {
  const agentErrorId = useId(), modelErrorId = useId();
  const invalidAgent = !!scope.agentVersionId && !uuidPattern.test(scope.agentVersionId);
  const invalidModel = !!scope.modelEndpointVersionId && !uuidPattern.test(scope.modelEndpointVersionId);
  const agents = useCallback(async (keywords: string, pageSize: number): Promise<SearchableSelectOption[]> => {
    const response = await withRefreshedToken((token) => listAdminAgents(token, { keywords, pageSize, page: 1 }));
    return response.value.items.map((item) => ({ value: item.id, label: item.name, description: item.slug }));
  }, []);
  const users = useCallback(async (keywords: string, pageSize: number): Promise<SearchableSelectOption[]> => {
    const response = await withRefreshedToken((token) => listUsers(token, { keywords, pageSize, page: 1 }));
    return response.value.items.map((item) => ({ value: item.id, label: item.display_name }));
  }, []);
  const resolveAgent = useCallback(async (id: string) => { const response = await withRefreshedToken((token) => getAdminAgent(token, id)); return { value: id, label: response.value.name }; }, []);
  const resolveUser = useCallback(async (id: string) => { const response = await withRefreshedToken((token) => getAdminUser(token, id)); return { value: id, label: response.value.display_name }; }, []);
  const selections = [
    { key: "lifecycle", label: "生命周期", choices: { draft: "草稿", active: "已启用", disabled: "已停用" } },
    { key: "health", label: "健康状态", choices: { healthy: "健康", degraded: "需关注", unknown: "未知/过期", no_recent_activity: "无近期活动" } },
    { key: "status", label: "运行状态", choices: runStatusLabels },
  ] as const;
  return <div className="flex w-full min-w-0 flex-wrap items-center gap-3">
    <Select value={String(scope.days ?? 7)} onValueChange={(value) => onChange("days", value)}><SelectTrigger className="w-36 max-w-full" aria-label="时间范围"><SelectValue /></SelectTrigger>
      <SelectContent>{Array.from(new Set([1, 7, 30, 90, scope.days ?? 7])).sort((a, b) => a-b).map((days) => <SelectItem value={String(days)} key={days}>最近 {days} 天</SelectItem>)}</SelectContent></Select>
    {!fixedAgent && <SearchableSelect value={scope.agentId ?? ""} onValueChange={(value) => onChange("agentId", value)} loadOptions={agents} resolveOption={resolveAgent}
      placeholder="全部 Agent" clearLabel="全部 Agent" searchPlaceholder="搜索 Agent 名称" className="w-60 max-w-full" />}
    {selections.map(({ key, label, choices }) => <Select key={key} value={scope[key] ?? "all"} onValueChange={(value) => onChange(key, value === "all" ? "" : value)}>
      <SelectTrigger className="w-40 max-w-full" aria-label={label}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部{label}</SelectItem>
        {Object.entries(choices).map(([value, title]) => <SelectItem key={value} value={value}>{title}</SelectItem>)}
      </SelectContent></Select>)}
    <SearchableSelect value={scope.userId ?? ""} onValueChange={(value) => onChange("userId", value)} loadOptions={users} resolveOption={resolveUser}
      placeholder="全部用户" clearLabel="全部用户" searchPlaceholder="搜索用户姓名" className="w-60 max-w-full" />
    <DepartmentTreeSelect value={scope.organizationUnitId ?? null} onChange={(value) => onChange("organizationUnitId", value ?? "")}
      showLabel={false} emptyLabel="全部部门" className="w-60 max-w-full" />
    <div className="w-64 max-w-full"><Input value={scope.agentVersionId ?? ""} onChange={(event) => onChange("agentVersionId", event.target.value.trim())}
      aria-label="历史调用 Agent 版本 UUID" aria-invalid={invalidAgent} aria-describedby={invalidAgent ? agentErrorId : undefined} placeholder="请输入 Agent 版本 UUID" />
      {invalidAgent && <p id={agentErrorId} className="mt-1 text-xs text-red-600">请输入有效 UUID</p>}</div>
    <div className="w-64 max-w-full"><Input value={scope.modelEndpointVersionId ?? ""} onChange={(event) => onChange("modelEndpointVersionId", event.target.value.trim())}
      aria-label="历史调用模型版本 UUID" aria-invalid={invalidModel} aria-describedby={invalidModel ? modelErrorId : undefined} placeholder="请输入模型版本 UUID" />
      {invalidModel && <p id={modelErrorId} className="mt-1 text-xs text-red-600">请输入有效 UUID</p>}</div>
    <p className="w-full text-xs text-muted-foreground">{scope.start && scope.end ? `实际区间：${new Date(scope.start).toLocaleString()} 至 ${new Date(scope.end).toLocaleString()}（不含终点）。` : "正在固定时间区间。"}
      生命周期/健康使用当前状态；版本/模型/用户/运行状态约束历史调用，部门取用户当前末级部门。Agent 库存仅在调用维度筛选时限制为区间内匹配的 Agent。</p>
  </div>;
}
