import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { StatusBadge } from "@/components/StatusBadge";
import type { RoutingCandidateFilterReport, RoutingConfigResolutionView } from "@/types";
import { formatCost, reasonLabels } from "../contracts";

export function RoutingPreviewResult({ value }: { value: RoutingConfigResolutionView }) {
  const result = value.resolution;
  const columns: AppTableColumn<RoutingCandidateFilterReport["candidates"][number]>[] = [
    { title: "优先级", dataIndex: "priority" },
    { title: "固定模型版本", key: "model", render: (_, row) => <span className="break-all font-mono text-xs">{row.model_version_id}</span> },
    { title: "元数据兼容性", key: "status", render: (_, row) => <StatusBadge value={row.status === "eligible" ? "pending" : "failed"}
      label={row.status === "eligible" ? "元数据满足" : "有阻断项"} /> },
    { title: "单次预留估价（USD）", key: "price", render: (_, row) => formatCost(row.estimated_cost_microusd) },
    { title: "阻断原因", key: "reasons", render: (_, row) => row.reasons.length ? row.reasons.map((reason) => reasonLabels[reason]).join("；") : "本次未发现元数据阻断项" },
  ];
  return <section className="space-y-3 text-sm" aria-label="本次路由预览结果">
    <div className="flex flex-wrap items-center gap-3"><h4 className="font-medium">本次预览</h4>
      <StatusBadge value={result.status === "resolved" ? "pending" : "failed"} label={result.status === "resolved" ? "引用可解析，未批准" : "完整候选集合未满足要求"} /></div>
    <p>观察时间：{new Date(result.observed_at).toLocaleString()}。估价按本次完整输入及输出上限计算，未调用模型；不代表实际费用、质量评测或发布批准。</p>
    {result.issues.length > 0 && <ul className="list-inside list-disc space-y-1">{result.issues.map((issue, index) => <li key={`${issue.code}:${issue.candidate_id}:${index}`}>
      {reasonLabels[issue.code]}{issue.candidate_id && <span className="break-all font-mono text-xs">（候选 {issue.candidate_id}）</span>}
    </li>)}</ul>}
    {result.candidate_filter ? <AppTable columns={columns} dataSource={result.candidate_filter.candidates} rowKey="candidate_id"
      emptyText="完整候选过滤结果缺失" ariaLabel="完整候选兼容性与费用预览" /> : <p>本次尚未进入逐候选过滤，原完整候选集合保留在上方。</p>}
    <p className="break-all text-xs text-muted-foreground">本次观察摘要：{value.resolution_digest}</p>
  </section>;
}
