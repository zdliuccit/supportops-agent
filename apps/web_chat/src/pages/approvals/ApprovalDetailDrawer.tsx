import { type ComponentProps, useEffect, useLayoutEffect, useRef, useState } from "react";
import { cancelApproval, decideApproval, getApproval } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { GlobalLoading } from "@/components/GlobalLoading";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/FormField";
import { StatusBadge } from "@/components/StatusBadge";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import { businessActionLabels } from "@/lib/businessActionLabels";
import type { ApprovalDetail, ApprovalStatus } from "@/types";

export const approvalLabels: Record<ApprovalStatus, string> = { pending: "等待指定人员审批", approved: "已批准，尚未消费",
  rejected: "已拒绝，未执行", expired: "已过期", cancelled: "已取消", invalidated: "授权范围变化，批准失效", consumed: "批准已一次性消费" };

/** 标签颜色只表达审批事实，业务执行结果另行展示。 */
export function ApprovalStatusBadge({ status }: { status: ApprovalStatus }) {
  const tone = { pending: "pending", approved: "completed", rejected: "failed", expired: "expired", cancelled: "cancelled", invalidated: "failed", consumed: "completed" }[status];
  return <StatusBadge value={tone} label={approvalLabels[status]} />;
}

/** 每次打开重新读取当前资源权限，批准与业务执行状态分别呈现。 */
export function ApprovalDetailDrawer({ approvalId, onClose, onChanged, onCloseAutoFocus }: { approvalId: string | null; onClose: () => void; onChanged: () => void; onCloseAutoFocus?: ComponentProps<typeof AppDrawer>["onCloseAutoFocus"] }) {
  const [detail, setDetail] = useState<ApprovalDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [reasonEdited, setReasonEdited] = useState(false);
  const sequence = useRef(0);
  const busyRef = useRef(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const reasonError = reasonEdited && !reason.trim() ? "请输入审批理由" : undefined;
  useLayoutEffect(() => { if (busy) contentRef.current?.focus(); }, [busy]);
  async function read() {
    if (!approvalId || busyRef.current) return;
    const current = ++sequence.current;
    setLoading(true); setDetail(null); setError(null); setReason(""); setReasonEdited(false);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => getApproval(token, approvalId)));
      if (sequence.current === current) setDetail(response.value);
    } catch (cause) { if (sequence.current === current) setError(cause instanceof Error ? cause.message : "读取审批失败"); }
    finally { if (sequence.current === current) setLoading(false); }
  }
  useEffect(() => { if (approvalId) notify.dismissSuccess(); void read(); return () => { sequence.current += 1; }; }, [approvalId]);
  async function decide(decision: "approved" | "rejected" | "cancel") {
    if (!detail || busyRef.current || loading) return;
    if (decision !== "cancel" && !reason.trim()) { setReasonEdited(true); return; }
    const current = sequence.current;
    busyRef.current = true;
    setBusy(true); setError(null);
    try {
      const response = await withRefreshedToken((token) => decision === "cancel" ? cancelApproval(token, detail) : decideApproval(token, detail, decision, reason.trim()));
      if (current !== sequence.current) return;
      setDetail(response.value);
      notify.success(decision === "approved" ? "批准已保存；执行结果以独立复核为准。" : decision === "rejected" ? "拒绝已保存，动作未执行。" : "等待审批已取消。");
      onChanged();
    } catch (cause) { if (current === sequence.current) { setDetail(null); setError(cause instanceof Error ? cause.message : "提交审批失败"); notify.error(cause, "提交审批失败"); } }
    finally { busyRef.current = false; if (current === sequence.current) setBusy(false); }
  }
  return <AppDrawer open={approvalId !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }} loading={busy}
    onCloseAutoFocus={onCloseAutoFocus} className="w-full sm:max-w-lg" contentClassName="min-w-0 [overflow-wrap:anywhere]"
    title="独立审批详情" description="隔离 Mock；指定非本人审批不扩大申请者权限。" showConfirm={false} cancelText="关闭">
    {loading ? <GlobalLoading fullScreen={false} size="sm" label="正在读取当前审批…" /> : <div ref={contentRef} tabIndex={-1} aria-busy={busy} className="space-y-4 text-sm outline-none">
      {error && <p role="alert" className="text-red-600">{error}</p>}
      {detail && <><p role="status" className="font-medium">{approvalLabels[detail.status]}</p>
        <p>业务动作状态：{businessActionLabels[detail.action_status]} · {detail.risk_level}</p>
        <h3 className="font-medium">{detail.parameters.title}</h3><p className="whitespace-pre-wrap break-words">{detail.parameters.summary}</p>
        <p className="break-all">业务意图：{detail.parameters.issue_key}</p>
        <p>有效至：{new Date(detail.expires_at).toLocaleString()}</p>
        <p className="break-all text-xs text-muted-foreground">参数 SHA-256：{detail.parameters_digest}</p>
        {detail.decision_reason && <p className="whitespace-pre-wrap break-words">决定理由：{detail.decision_reason}</p>}
        {detail.can_decide && <div className="space-y-3"><FormField label="审批理由（必填）" htmlFor={`approval-reason-${detail.id}`} required error={reasonError}>
          <Textarea id={`approval-reason-${detail.id}`} value={reason} onChange={(event) => { setReason(event.target.value); setReasonEdited(true); }} maxLength={1000} disabled={busy}
            placeholder="请输入审批理由" aria-invalid={Boolean(reasonError)} aria-describedby={reasonError ? `approval-reason-${detail.id}-error` : undefined} /></FormField>
          <div className="flex flex-wrap gap-2"><Button disabled={busy || !reason.trim()} onClick={() => void decide("approved")}>批准冻结动作</Button>
            <Button variant="destructive" disabled={busy || !reason.trim()} onClick={() => void decide("rejected")}>拒绝</Button></div></div>}
        {detail.can_cancel && <Button variant="outline" disabled={busy} onClick={() => void decide("cancel")}>取消未消费申请</Button>}
      </>}
      {busy && <p role="status">正在保存审批决定…</p>}
      <Button variant="outline" disabled={busy || loading} onClick={() => void read()}>重新读取当前状态</Button>
    </div>}
  </AppDrawer>;
}
