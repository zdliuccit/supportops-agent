import { useCallback, useEffect, useRef, useState } from "react";
import { createCompensationProposal, getCompensationProposal, getCompensationReadiness, listCompensationProposals } from "@/api";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { CompensationDetail, CompensationReadiness, CompensationSummary, PaginatedListResponse } from "@/types";

const blockerLabels: Record<string, string> = {
  COMPENSATION_EXECUTOR_NOT_IMPLEMENTED: "补偿执行器尚未接入，当前仅保存意向",
  NEW_INDEPENDENT_APPROVAL_REQUIRED: "执行前需要新的独立非本人审批，原批准不能复用",
  FRESH_AUTHORIZATION_AND_TARGET_CHECK_REQUIRED: "执行前须重新校验当前权限、固定版本和目标状态",
  COMPLETED_CONTEXT_REQUIRED: "需要已完成且与原动作关联的有效上下文",
  ACTION_RECONCILIATION_REQUIRED: "原动作尚未独立确认，先完成对账或人工核查",
  COMPENSATION_CONNECTOR_UNSUPPORTED: "当前动作没有获准的补偿提案契约",
  ACTION_SCOPE_INTEGRITY_FAILED: "原动作冻结范围完整性失败",
  INDEPENDENT_RECEIPT_REQUIRED: "缺少当前尝试的独立查询回执",
  SOURCE_RECEIPT_INTEGRITY_FAILED: "原查询回执完整性或范围不一致",
  CURRENT_TARGET_NOT_PROVEN: "尚未确认可访问的当前目标工单",
  TARGET_ALREADY_CLOSED_OR_UNSUPPORTED: "工单已关闭或当前状态不适用",
  CURRENT_TARGET_INTEGRITY_FAILED: "当前目标工单字段不符合受限契约",
  COMPENSATION_SCOPE_INTEGRITY_FAILED: "补偿提案完整性失败，已隐藏范围",
  FROZEN_SCOPE_CHANGED: "原动作、目标或授权范围已变化，旧提案不能用于当前执行",
  ACTION_RELEASE_WITHDRAWN: "来源版本已撤回，不能生成新补偿提案",
};
function blockerLabel(code: string) { return blockerLabels[code] ?? "存在未识别阻塞原因，请联系支持人员"; }

/** 本人显式保存补偿意向；本组件不包含批准、排队或关闭工单操作。 */
export function ActionCompensationPanel({ actionId, runId }: { actionId: string; runId: string }) {
  const [enabled, setEnabled] = useState(false), [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [readiness, setReadiness] = useState<CompensationReadiness | null>(null);
  const [result, setResult] = useState<PaginatedListResponse<CompensationSummary> | null>(null);
  const [detail, setDetail] = useState<CompensationDetail | null>(null), [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState({ current: 1, pageSize: 20 }), [confirmDigest, setConfirmDigest] = useState<string | null>(null);
  const sequence = useRef(0), mutation = useRef(false), mounted = useRef(false), detailSequence = useRef(0);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    detailSequence.current += 1;
    setLoading(true); setDetailLoading(false); setError(null); setReadiness(null); setResult(null); setDetail(null);
    try {
      const response = await delayRequest(() => withAuthenticatedToken(async (token) => {
        const [state, history] = await Promise.all([getCompensationReadiness(token, actionId, runId),
          listCompensationProposals(token, actionId, runId, { page: page.current, pageSize: page.pageSize })]);
        return { state, history };
      }));
      if (current === sequence.current) { setReadiness(response.value.state); setResult(response.value.history); }
    } catch (cause) { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "补偿提案暂时无法读取"); }
    finally { if (current === sequence.current) setLoading(false); }
  }, [actionId, runId, page.current, page.pageSize]);
  const latestLoad = useRef(load); latestLoad.current = load;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; sequence.current += 1; detailSequence.current += 1; }; }, []);
  useEffect(() => { if (enabled) void load(); return () => { sequence.current += 1; }; }, [enabled, load]);
  async function save() {
    if (!confirmDigest || mutation.current) return;
    mutation.current = true; setBusy(true);
    try {
      await withAuthenticatedToken((token) => createCompensationProposal(token, actionId, runId, confirmDigest));
      notify.success("补偿提案已保存；尚未批准或执行，工单状态不变。");
      if (mounted.current) setConfirmDigest(null);
    } catch (cause) { notify.error(cause, "补偿提案未确认，请重新读取当前范围后重试"); if (mounted.current) setConfirmDigest(null); }
    finally { mutation.current = false; if (mounted.current) { setBusy(false); await latestLoad.current(); } }
  }
  async function readDetail(id: string) {
    const current = ++detailSequence.current;
    setDetailLoading(true); setDetail(null); setError(null);
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => getCompensationProposal(token, actionId, runId, id)));
      if (mounted.current && current === detailSequence.current) setDetail(response.value);
    } catch (cause) { if (mounted.current && current === detailSequence.current) setError(cause instanceof Error ? cause.message : "补偿提案详情读取失败"); }
    finally { if (mounted.current && current === detailSequence.current) setDetailLoading(false); }
  }
  const columns: AppTableColumn<CompensationSummary>[] = [
    { title: "状态", key: "status", render: () => <StatusBadge value="pending" label="仅补偿提案" /> },
    { title: "最低风险", dataIndex: "minimum_risk" },
    { title: "保存时间", key: "created", render: (_, row) => new Date(row.created_at).toLocaleString() },
    { title: "操作", key: "actions", align: "right", render: (_, row) => <Button size="sm" variant="ghost" disabled={loading || busy || detailLoading} onClick={() => void readDetail(row.id)}>核对范围</Button> },
  ];
  if (!enabled) return <Button variant="outline" onClick={() => { setLoading(true); setEnabled(true); }}>查看受控补偿提案</Button>;
  return <section aria-label="受控补偿提案" className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-medium">受控补偿提案</h3>
      <Button variant="outline" disabled={loading || busy || detailLoading || !readiness?.proposal_supported || !readiness.scope_digest}
        onClick={() => setConfirmDigest(readiness?.scope_digest ?? null)}>保存关闭Mock工单意向</Button></div>
    <p className="text-sm text-muted-foreground">这里只保存关闭意向。最低R3且必须重新独立审批，当前没有补偿执行器；不会自动撤销原动作或关闭工单。</p>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {readiness?.scope && <p className="text-sm">当前目标：{readiness.scope.target_external_id} · 工单版本 {readiness.scope.target_version} · 最低风险 {readiness.scope.minimum_risk}</p>}
    {readiness && <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{readiness.blockers.map((code) => <li key={code}>{blockerLabel(code)}</li>)}</ul>}
    <div className="rounded-2xl bg-white"><ListToolbar title="补偿提案列表" loading={loading || busy || detailLoading} onRefresh={() => void load()} />
      <div className="relative min-h-40"><AppTable columns={columns} dataSource={result?.items ?? []} rowKey="id" ariaLabel="本人补偿提案"
        emptyText={error ? "补偿提案暂时无法读取" : "尚无补偿提案"} pagination={{ ...page, total: result?.total ?? 0,
          onChange: (current, pageSize) => { if (!busy) setPage({ current, pageSize }); } }} />{(loading || busy || detailLoading) && <ListLoadingOverlay label="正在读取补偿范围…" />}</div>
    </div>
    {detail && <div role="status" className="space-y-2 text-sm">
      <p>历史完整性：{detail.integrity_status === "recorded" ? "已记录" : "失败，内容已隐藏"}；当前适用性：{({ current: "范围一致，仍无执行许可", changed: "范围已变化", unavailable: "当前来源不可用", integrity_failed: "完整性失败" })[detail.applicability]}</p>
      <p className="break-all font-mono text-xs">范围摘要：{detail.proposal.scope_digest}</p>
      {detail.scope && <p>冻结目标 {detail.scope.target_external_id} · 版本 {detail.scope.target_version} · 来源回执 {detail.scope.source_receipt_status === "verified" ? "独立已验证" : "独立查询冲突"}</p>}
      <ul className="list-disc pl-5">{detail.blockers.map((code) => <li key={code}>{blockerLabel(code)}</li>)}</ul>
    </div>}
    <Dialog open={confirmDigest !== null} onOpenChange={(open) => { if (!open && !busy) setConfirmDigest(null); }}><DialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>保存受控补偿提案</DialogTitle></DialogHeader><DialogBody><DialogDescription>确认提出关闭当前Mock工单的意向。服务器将重新读取原动作、独立回执与当前工单版本，任何范围变化都会拒绝。原动作/回执保留，原批准不复用，当前不执行补偿。</DialogDescription>
        <p className="mt-3 break-all font-mono text-xs">确认范围：{confirmDigest}</p></DialogBody>
      <DialogFooter><Button variant="outline" disabled={busy} onClick={() => setConfirmDigest(null)}>取消</Button><Button disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "确认保存提案"}</Button></DialogFooter>
    </DialogContent></Dialog>
  </section>;
}
