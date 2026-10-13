import { useEffect, useRef, useState } from "react";

import { getDiagnosticReport } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { GlobalLoading } from "@/components/GlobalLoading";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import type { DiagnosticReport, DiagnosticStage } from "@/types";

export const diagnosticStageLabels: Record<DiagnosticStage["name"], string> = {
  classify: "识别诊断场景", collect_information: "检查必要信息", query_logs: "查询请求日志",
  query_profile: "查询配额与账户", query_route: "查询模型路由", query_incidents: "查询故障事件",
  retrieve_rules: "检索获准知识规则", evaluate: "核对事实与规则", escalate: "整理人工接管包", complete: "诊断完成",
};
const stageStatus = { running: "进行中", completed: "已完成", skipped: "已跳过", failed: "未完成" };
const reportStatus = { running: "诊断中", needs_information: "需要补充信息", confirmed_mock: "已确认模拟原因",
  unconfirmed: "原因未确认", conflict: "证据存在冲突", unavailable: "依赖或来源不可用" };
const toolLabels: Record<string, string> = { get_request_logs: "请求日志", get_current_user_profile: "配额与账户",
  get_model_route_status: "模型路由", list_active_incidents: "故障事件" };
const fieldLabels: Record<string, string> = {
  rpm_count: "每分钟请求数", rpm_limit: "RPM 限额", input_tokens: "输入 Token", max_output_reserved_tokens: "最大输出预占",
  tpm_limit: "TPM 限额", concurrency: "项目并发数", concurrency_limit: "并发限额", http_status: "HTTP 状态",
  request_at: "请求时间", model_name: "模型", region: "区域", error_class: "错误分类", account_status: "账户状态",
  cash_balance_usd: "现金余额（模拟 USD）", gift_balance_usd: "赠送余额（模拟 USD）", frozen_balance_usd: "冻结余额（模拟 USD）",
  plan_remaining_tokens: "套餐剩余 Token", quota_updated_at: "配额更新时间", project_id: "模拟项目", subject_user_id: "当前主体",
  route_status: "路由状态", incident_id: "模拟事件", incident_count: "可见事件数", visibility: "可见范围", incident_status: "事件状态",
};

/** 展开时重读当前授权报告，不能把旧缓存事实当作仍获准数据。 */
export function DiagnosticReportPanel({ report }: { report: DiagnosticReport }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<DiagnosticReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);
  useEffect(() => () => { requestId.current += 1; }, []);

  async function load() {
    const current = ++requestId.current;
    setOpen(true); setDetail(null); setError(null); setLoading(true);
    try {
      const response = await withRefreshedToken((token) => getDiagnosticReport(token, report.run_id));
      if (current === requestId.current) setDetail(response.value);
    } catch (cause) {
      if (current === requestId.current) setError(cause instanceof Error ? cause.message : "读取诊断报告失败");
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }

  return <section aria-label="模拟诊断排查结果" className="space-y-2">
    <p className="font-medium">429 模拟诊断：{reportStatus[report.status]}</p>
    <Button variant="outline" size="sm" onClick={() => void load()}>查看排查步骤与人工接管包</Button>
    <AppDrawer open={open} onOpenChange={(value) => { if (!value) { requestId.current += 1; setDetail(null); setError(null); setLoading(false); } setOpen(value); }}
      title="模拟诊断报告" description="查询事实与根因分开；当前权限下读取，不代表真实生产数据。" showConfirm={false} cancelText="关闭">
      {loading ? <GlobalLoading fullScreen={false} size="sm" label="正在读取诊断报告…" /> : error ? <div role="alert" className="space-y-3">
        <p className="text-red-600">{error}</p><Button variant="outline" onClick={() => void load()}>重新读取</Button>
      </div> : detail ? <div className="space-y-5 text-sm">
        <p role="status" className="text-amber-700">隔离模拟环境 · {reportStatus[detail.status]}</p>
        <section aria-label="实际排查步骤"><h3 className="mb-2 font-medium">排查步骤</h3>
          <ol className="space-y-2">{detail.stages.map((stage) => <li key={stage.name} className="flex flex-wrap justify-between gap-2">
            <span>{diagnosticStageLabels[stage.name]}</span><span className="text-muted-foreground">{stageStatus[stage.status]}</span>
          </li>)}</ol>
        </section>
        {detail.candidates.length > 0 && <section aria-label="根因候选" className="space-y-2"><h3 className="font-medium">根因候选</h3>
          {detail.candidates.map((candidate) => <p key={candidate.code}>{candidate.description}（{candidate.confirmation === "confirmed_mock" ? "已确认模拟原因" : "尚未确认"}）</p>)}
        </section>}
        <section aria-label="来源提供的模拟事实" className="space-y-2"><h3 className="font-medium">来源提供的事实</h3>
          {detail.facts.length === 0 ? <p className="text-muted-foreground">没有当前获准的查询事实。</p> : detail.sources.map((source) => <div key={source.invocation_id} className="space-y-2">
            <p className="font-medium">{toolLabels[source.tool_id] ?? source.tool_id}{source.reused ? " · 复用已验证只读结果" : ""}</p>
            <p className="text-xs text-muted-foreground">查询时间：{new Date(source.observed_at).toLocaleString()}</p>
            <dl className="space-y-1">{detail.facts.filter((fact) => fact.invocation_id === source.invocation_id).map((fact) => <div key={fact.field} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3">
              <dt className="text-muted-foreground">{fieldLabels[fact.field] ?? fact.field}</dt><dd className="break-words">{fact.value === null ? "未知" : String(fact.value)}</dd>
            </div>)}</dl>
          </div>)}
        </section>
        {detail.unresolved_items.length > 0 && <section aria-label="未确认事项"><h3 className="mb-2 font-medium">未确认事项</h3>
          <ul className="list-inside list-disc space-y-1">{detail.unresolved_items.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </section>}
        {detail.escalation && <section aria-label="人工接管包" className="space-y-2"><h3 className="font-medium">人工接管包（尚未建单）</h3>
          <p>已整理 {detail.escalation.confirmed_fact_count} 项来源事实、{detail.escalation.completed_steps.length} 个完成步骤和 {detail.escalation.evidence_ids.length} 份知识引用。</p>
          <p className="text-muted-foreground">可接续以上未确认事项；没有执行配额、资金或权限变更。</p>
        </section>}
      </div> : <p className="text-muted-foreground">暂无可读取的诊断报告。</p>}
    </AppDrawer>
  </section>;
}
