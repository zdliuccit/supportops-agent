import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, executeBusinessAction, getActionReceipts, getBusinessAction, proposeMockTicket, reconcileBusinessAction } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { GlobalLoading } from "@/components/GlobalLoading";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { delayRequest } from "@/lib/delayRequest";
import type { ActionReceipt, BusinessActionDetail, BusinessActionStatus, SupportResult } from "@/types";
import { ActionApprovalPanel } from "./ActionApprovalPanel";
import { EscalationPackagePanel } from "./EscalationPackagePanel";
import { ActionAmendDialog } from "./ActionAmendDialog";
import { ActionCompensationPanel } from "./ActionCompensationPanel";

const labels: Record<BusinessActionStatus, string> = { proposed: "提案已保存，尚未执行", waiting_approval: "等待独立审批",
  queued: "执行意图已排队，尚未确认结果", running: "动作处理中，尚未确认结果", unknown: "结果未知，需要对账",
  reconciling: "正在独立对账", succeeded: "业务状态已独立复核成功", failed: "动作未完成", needs_attention: "需要人工处理未知结果", cancelled: "已取消" };

/** 冻结提案与执行回执单独展示；读取/执行都使用当前权限。 */
type ActionContextProps = { result: SupportResult } | { failureContext: { run_id: string } };
export function MessageBusinessActions(props: ActionContextProps) {
  const result = "result" in props ? props.result : null;
  const sourceRunId = "result" in props ? props.result.run_id : props.failureContext.run_id;
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState<BusinessActionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [receipts, setReceipts] = useState<ActionReceipt[]>([]);
  const [receiptTotal, setReceiptTotal] = useState(0);
  const [receiptPage, setReceiptPage] = useState({ current: 1, pageSize: 20 });
  const generation = useRef(0);
  useEffect(() => () => { generation.current += 1; }, []);

  async function read(actionId: string, pagination = { current: 1, pageSize: 20 }) {
    const current = ++generation.current;
    setSelectedId(actionId); setOpen(true); setAction(null); setError(null); setLoading(true);
    setReceipts([]); setReceiptTotal(0); setReceiptPage(pagination);
    try {
      const response = await delayRequest(() => withRefreshedToken(async (token) => {
        const [detail, receiptList] = await Promise.all([getBusinessAction(token, actionId, sourceRunId),
          getActionReceipts(token, actionId, sourceRunId, { page: pagination.current, pageSize: pagination.pageSize })]);
        return { detail, receiptList };
      }));
      if (generation.current === current) {
        setAction(response.value.detail); setReceipts(response.value.receiptList.items); setReceiptTotal(response.value.receiptList.total);
      }
    } catch (cause) { if (generation.current === current) setError(cause instanceof Error ? cause.message : "读取动作失败"); }
    finally { if (generation.current === current) setLoading(false); }
  }

  async function propose() {
    setSubmitting(true);
    try {
      const response = await withRefreshedToken((token) => proposeMockTicket(token, sourceRunId));
      notify.success("建单提案已保存；尚未执行，不是创建成功回执。");
      await read(response.value.id);
    } catch (cause) { notify.error(cause, "当前无法提出建单动作"); } finally { setSubmitting(false); }
  }

  async function execute() {
    if (!action || !result) return;
    const current = generation.current;
    setSubmitting(true); setError(null);
    try {
      const response = await withRefreshedToken((token) => executeBusinessAction(token, action, sourceRunId));
      if (generation.current === current) setAction(response.value);
      notify.success("执行意图已提交；实际结果以独立复核状态为准。");
    } catch (cause) { if (generation.current === current) {
      setError(cause instanceof Error ? cause.message : "提交执行意图失败");
      if (cause instanceof ApiError && (cause.status === 403 || cause.status === 404)) setAction(null);
    } }
    finally { setSubmitting(false); }
  }

  async function reconcile() {
    if (!action || !result) return;
    setSubmitting(true); setError(null);
    try {
      const response = await withRefreshedToken((token) => reconcileBusinessAction(token, action, sourceRunId));
      setAction(response.value);
      notify.success("已追加一次有限查询许可；累计次数保留，不会直接重复执行。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "恢复独立对账失败"); }
    finally { setSubmitting(false); }
  }

  const receiptLabels: Record<ActionReceipt["status"], string> = { submitted: "目标已接收，尚未独立复核", verified: "独立查询已验证",
    conflict: "目标状态不一致", not_executed: "目标明确证明未执行", unknown: "仍未取得确定证明" };
  const receiptColumns: AppTableColumn<ActionReceipt>[] = [
    { title: "证明类型", key: "status", render: (_, receipt) => receiptLabels[receipt.status] },
    { title: "观察时间", key: "observed_at", render: (_, receipt) => new Date(receipt.observed_at).toLocaleString() },
    { title: "模拟目标编号", dataIndex: "external_id", key: "external_id", render: (_, receipt) => receipt.external_id ?? "尚未确认" },
  ];

  const references = result?.business_actions ?? [];
  const canPropose = result === null || (result.diagnostic?.escalation !== null && result.diagnostic?.escalation !== undefined);
  if (references.length === 0 && !canPropose && !selectedId) return null;
  return <section aria-label="独立业务动作" className="space-y-2">
    <p className="font-medium">模拟业务动作</p>
    <div className="flex flex-wrap gap-2">{references.map((reference) => reference.availability === "available"
      ? <Button key={reference.action_id} variant="outline" size="sm" onClick={() => void read(reference.action_id)}>{reference.status ? labels[reference.status] : "查看当前动作"}</Button>
      : <span key={reference.action_id} className="text-muted-foreground">动作已失效或无权读取</span>)}
      {canPropose && <Button variant="outline" size="sm" disabled={submitting} onClick={() => void propose()}>{result ? "提出模拟建单动作" : "保存/查看模拟升级提案"}</Button>}
      {selectedId && !references.some((reference) => reference.action_id === selectedId) && <Button variant="outline" size="sm" onClick={() => void read(selectedId)}>查看已保存提案</Button>}
    </div>
    <AppDrawer open={open} onOpenChange={(value) => { if (!value && !submitting) { generation.current += 1; setOpen(false); setAction(null); setError(null); setLoading(false); } }}
      title="冻结动作与执行状态" description="隔离 Mock 数据，未连接真实工单；建议、提案、执行意图和已复核结果分别记录。" showConfirm={false} cancelText="关闭">
      {loading ? <GlobalLoading fullScreen={false} size="sm" label="正在读取当前动作…" /> : <div className="space-y-4 text-sm">
        {error && <p role="alert" className="text-red-600">{error}</p>}
        {action && <><p role="status" className="font-medium">{labels[action.status]} · {action.risk_level}</p>
          <h3 className="font-medium">{action.parameters.title}</h3><p className="whitespace-pre-wrap break-words">{action.parameters.summary}</p>
          <p className="break-all text-xs text-muted-foreground">业务意图：{action.parameters.issue_key}</p>
          <p className="break-all font-mono text-xs text-muted-foreground">冻结参数 SHA-256：{action.parameters_digest}</p>
          {action.approval_required && <p className="text-amber-700">需要独立审批，当前不会自动执行。</p>}
          {action.error_code && <p className="text-muted-foreground">状态原因：{action.error_code}</p>}
          <p className="text-muted-foreground">累计对账 {action.reconciliation_count} 次 · 当前许可 {action.reconciliation_limit} 次</p>
          {action.status === "succeeded" && action.external_id && <Link className="break-all text-emerald-700 underline" to={`/mock-tickets/${encodeURIComponent(action.external_id)}`}>查看已复核 Mock 工单：{action.external_id}</Link>}
          {result && action.status === "proposed" && !action.approval_required && action.risk_level === "R2" && <Button disabled={submitting || action.effective_catalog_version === null} onClick={() => void execute()}>{submitting ? "正在提交…" : "提交模拟执行意图"}</Button>}
          {result && action.status === "needs_attention" && <Button variant="outline" disabled={submitting} onClick={() => void reconcile()}>申请一次独立对账</Button>}
          {result && <ActionAmendDialog action={action} runId={sourceRunId} onReplaced={(replacement) => void read(replacement.id)} />}
          {result && <ActionApprovalPanel action={action} runId={sourceRunId} onChanged={() => { if (selectedId) void read(selectedId, receiptPage); }} />}
          <EscalationPackagePanel actionId={action.id} runId={sourceRunId} />
          {result && <ActionCompensationPanel key={`${action.id}:${sourceRunId}`} actionId={action.id} runId={sourceRunId} />}
          <section aria-label="独立动作回执" className="space-y-2"><h3 className="font-medium">回执与复核证明</h3>
            <AppTable columns={receiptColumns} dataSource={receipts} rowKey="id" ariaLabel="业务动作回执" emptyText="尚无回执，不能据此认定成功"
              pagination={{ ...receiptPage, total: receiptTotal, onChange: (current, pageSize) => { if (selectedId) void read(selectedId, { current, pageSize }); } }} />
          </section>
        </>}
        {selectedId && <Button variant="outline" disabled={submitting} onClick={() => void read(selectedId)}>重新读取当前状态</Button>}
      </div>}
    </AppDrawer>
  </section>;
}
