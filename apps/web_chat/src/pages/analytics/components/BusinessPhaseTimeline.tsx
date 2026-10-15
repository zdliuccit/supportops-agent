import { StatusBadge } from "@/components/StatusBadge";
import type { DashboardTrace } from "@/types";

const kindLabels = { execution: "Agent 执行尝试", diagnostic: "诊断步骤", approval_wait: "审批等待", action_wait: "动作等待",
  recovery_wait: "恢复等待", action: "动作执行", verification: "独立复核", reconciliation: "业务对账", receipt: "回执事实" };

/** 等待与执行独立展示；阶段来自持久化事实，父子耗时不能相加。 */
export function BusinessPhaseTimeline({ trace }: { trace: DashboardTrace["business_trace"] }) {
  if (!trace) return <p className="text-sm text-muted-foreground">暂无业务阶段观测。</p>;
  return <section aria-label="业务阶段时间线" className="space-y-3 rounded-2xl bg-white p-4 text-sm">
    <h3 className="font-medium">业务阶段</h3>
    <p className="text-xs text-muted-foreground">等待与执行分别统计，父子阶段不能累加；未结束或缺失时间显示未知。本次读取：{new Date(trace.observed_at).toLocaleString()}</p>
    {trace.truncated && <p role="status" className="text-amber-700">每类来源仅展示前 {trace.source_limit} 条，当前时间线已截断。</p>}
    {trace.phases.length === 0 ? <p className="text-muted-foreground">暂无持久化阶段事实。</p> : <ol className="space-y-3">
      {trace.phases.map((phase) => <li key={phase.span_id} className="space-y-1">
        <div className="flex flex-wrap items-center gap-2"><StatusBadge value={phase.status} label={kindLabels[phase.kind]} />
          <span>{phase.name}</span><span className="text-muted-foreground">{phase.duration_ms === null ? "尚未结束或耗时未知" : `${phase.duration_ms} ms`}</span></div>
        <p className="text-xs text-muted-foreground">{new Date(phase.started_at).toLocaleString()} → {phase.finished_at ? new Date(phase.finished_at).toLocaleString() : "未确认结束"}</p>
        <p className="break-all text-xs text-muted-foreground">父阶段：{phase.parent_span_id}{phase.receipt_id ? ` · 回执：${phase.receipt_id}` : ""}</p>
      </li>)}
    </ol>}
  </section>;
}
