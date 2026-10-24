import { AppDrawer } from "@/components/AppDrawer";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { BusinessPhaseTimeline } from "./BusinessPhaseTimeline";
import { FirstTextTimingSummary } from "./FirstTextTimingSummary";
import { RunBudgetSummary } from "./RunBudgetSummary";
import { RunSecurityAudit } from "./RunSecurityAudit";
import { RunActionAudit } from "./RunActionAudit";
import { RunRoutingOutcome } from "./RunRoutingOutcome";
import type { DashboardTrace } from "@/types";

export function DashboardTraceDrawer({ open, trace, loading, error, onClose, onRetry }: {
  open: boolean; trace: DashboardTrace | null; loading: boolean; error: string | null; onClose: () => void; onRetry: () => void;
}) {
  return <AppDrawer open={open} onOpenChange={(value) => { if (!value) onClose(); }} title="运行链路"
    description={trace ? `${trace.run.agent_name} · ${trace.run.correlation_id}` : "按当前管理员权限读取运行元数据"}
    showConfirm={false} cancelText="关闭" className="w-full sm:max-w-3xl">
    {loading && <p role="status">正在读取链路…</p>}
    {error && <div role="alert" className="space-y-2 text-sm text-red-600"><p>{error}</p><Button variant="outline" onClick={onRetry}>重试</Button></div>}
    {trace && <div className="space-y-4"><RunBudgetSummary budget={trace.budget} /><FirstTextTimingSummary observations={trace.observations} />
      <BusinessPhaseTimeline trace={trace.business_trace} /><RunSecurityAudit key={trace.run.id} runId={trace.run.id} />
      <RunActionAudit key={trace.run.id} runId={trace.run.id} />
      {open && <RunRoutingOutcome key={trace.run.id} runId={trace.run.id} />}
      {trace.observations.map((item) => <section key={item.id} className="space-y-2 rounded-2xl bg-white p-4 text-sm">
        <div className="flex flex-wrap items-center gap-2"><StatusBadge value={item.status} label={item.kind} /><span>{item.name}</span>
          <span>{item.duration_ms === null ? "耗时未知" : `${item.duration_ms} ms`}</span></div>
        {item.error_code && <p className="text-red-600">{item.error_message ?? item.error_code}</p>}
      </section>)}
    </div>}
  </AppDrawer>;
}
