import { useEffect, useRef, useState } from "react";
import { getDashboardTrace } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import { BusinessPhaseTimeline } from "../../components/BusinessPhaseTimeline";
import { FirstTextTimingSummary } from "../../components/FirstTextTimingSummary";
import { RunSecurityAudit } from "../../components/RunSecurityAudit";
import type { DashboardTrace, RuntimeAlert } from "@/types";

/** 详情只展示脱敏元数据；关联 Run 的读取独立复检权限。 */
export function RuntimeAlertDetail({ alert, onClose }: { alert: RuntimeAlert | null; onClose: () => void }) {
  const [trace, setTrace] = useState<DashboardTrace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const sequence = useRef(0);
  useEffect(() => {
    const current = ++sequence.current;
    setTrace(null); setError(null); setLoading(false);
    if (!alert?.run_id) return;
    setLoading(true);
    void withRefreshedToken((token) => getDashboardTrace(token, alert.run_id!)).then((response) => {
      if (sequence.current === current) setTrace(response.value);
    }).catch((cause: unknown) => {
      if (sequence.current === current) setError(cause instanceof Error ? cause.message : "关联运行读取失败");
    }).finally(() => { if (sequence.current === current) setLoading(false); });
    return () => { sequence.current += 1; };
  }, [alert?.id, alert?.run_id, retry]);

  return <AppDrawer open={alert !== null} onOpenChange={(open) => { if (!open) onClose(); }} title="运行告警详情"
    description={alert?.description} showConfirm={false} cancelText="关闭" className="w-full sm:max-w-3xl">
    {alert && <div className="space-y-5 text-sm">
      <p role="status">{alert.is_stale ? "观测已过期，当前条件未知。" : "以下为最近成功扫描时的事实。"} 已阅仅表示看到告警，不代表修复或业务成功。</p>
      <dl className="grid gap-3 sm:grid-cols-2">
        <div><dt className="text-muted-foreground">规则</dt><dd>{alert.title} · {alert.rule_code}</dd></div>
        <div><dt className="text-muted-foreground">来源</dt><dd className="break-all">{alert.scope_kind} · {alert.scope_id}</dd></div>
        <div><dt className="text-muted-foreground">首次 / 最近观测</dt><dd>{new Date(alert.first_observed_at).toLocaleString()} / {new Date(alert.last_observed_at).toLocaleString()}</dd></div>
        <div><dt className="text-muted-foreground">来源代次 / 告警版本</dt><dd>{alert.source_generation} / {alert.revision}</dd></div>
        <div><dt className="text-muted-foreground">确认人员</dt><dd className="break-all">{alert.acknowledged_by ?? "未确认"}</dd></div>
        <div><dt className="text-muted-foreground">确认时间</dt><dd>{alert.acknowledged_at ? new Date(alert.acknowledged_at).toLocaleString() : "未确认"}</dd></div>
      </dl>
      <section aria-label="来源诊断元数据"><h3 className="font-medium">来源诊断元数据</h3>
        <dl className="mt-2 space-y-2">{Object.entries(alert.metadata).map(([key, value]) => <div key={key} className="flex flex-wrap gap-2">
          <dt className="text-muted-foreground">{key}</dt><dd className="break-all">{value === null ? "未知" : Array.isArray(value) ? value.join("、") || "无缺失字段" : String(value)}</dd>
        </div>)}</dl>
      </section>
      {loading && <p role="status">正在读取关联运行…</p>}
      {error && <div role="alert" className="flex flex-wrap items-center gap-3 text-red-600"><span>{error}</span><Button variant="outline" size="sm" onClick={() => setRetry((value) => value + 1)}>重试</Button></div>}
      {trace && <><FirstTextTimingSummary observations={trace.observations} /><BusinessPhaseTimeline trace={trace.business_trace} /></>}
      {alert.run_id && <RunSecurityAudit key={alert.run_id} runId={alert.run_id} />}
    </div>}
  </AppDrawer>;
}
