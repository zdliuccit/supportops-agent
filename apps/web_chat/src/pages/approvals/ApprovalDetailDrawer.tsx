import { useEffect, useRef, useState } from "react";
import { cancelApproval, decideApproval, getApproval } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { GlobalLoading } from "@/components/GlobalLoading";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { ApprovalDetail, ApprovalStatus } from "@/types";

export const approvalLabels: Record<ApprovalStatus, string> = { pending: "等待指定人员审批", approved: "已批准，尚未消费",
  rejected: "已拒绝，未执行", expired: "已过期", cancelled: "已取消", invalidated: "授权范围变化，批准失效", consumed: "批准已一次性消费" };

/** 每次打开重新读取当前资源权限，批准与业务执行状态分别呈现。 */
export function ApprovalDetailDrawer({ approvalId, onClose, onChanged }: { approvalId: string | null; onClose: () => void; onChanged: () => void }) {
  const [detail, setDetail] = useState<ApprovalDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const sequence = useRef(0);
  async function read() {
    if (!approvalId) return;
    const current = ++sequence.current;
    setLoading(true); setDetail(null); setError(null); setReason("");
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => getApproval(token, approvalId)));
      if (sequence.current === current) setDetail(response.value);
    } catch (cause) { if (sequence.current === current) setError(cause instanceof Error ? cause.message : "读取审批失败"); }
    finally { if (sequence.current === current) setLoading(false); }
  }
  useEffect(() => { void read(); return () => { sequence.current += 1; }; }, [approvalId]);
  async function decide(decision: "approved" | "rejected" | "cancel") {
    if (!detail) return;
    const current = sequence.current;
    setBusy(true); setError(null);
    try {
      const response = await withRefreshedToken((token) => decision === "cancel" ? cancelApproval(token, detail) : decideApproval(token, detail, decision, reason.trim()));
      if (current === sequence.current) setDetail(response.value);
      notify.success(decision === "approved" ? "批准已保存；执行结果以独立复核为准。" : decision === "rejected" ? "拒绝已保存，动作未执行。" : "等待审批已取消。");
      onChanged();
    } catch (cause) { if (current === sequence.current) { setDetail(null); setError(cause instanceof Error ? cause.message : "提交审批失败"); } }
    finally { setBusy(false); }
  }
  return <AppDrawer open={approvalId !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }} loading={busy}
    title="独立审批详情" description="隔离 Mock；指定非本人审批不扩大申请者权限。" showConfirm={false} cancelText="关闭">
    {loading ? <GlobalLoading fullScreen={false} size="sm" label="正在读取当前审批…" /> : <div className="space-y-4 text-sm">
      {error && <p role="alert" className="text-red-600">{error}</p>}
      {detail && <><p role="status" className="font-medium">{approvalLabels[detail.status]}</p>
        <p>业务动作状态：{detail.action_status} · {detail.risk_level}</p>
        <h3 className="font-medium">{detail.parameters.title}</h3><p className="whitespace-pre-wrap break-words">{detail.parameters.summary}</p>
        <p className="break-all">业务意图：{detail.parameters.issue_key}</p>
        <p>有效至：{new Date(detail.expires_at).toLocaleString()}</p>
        <p className="break-all text-xs text-muted-foreground">参数 SHA-256：{detail.parameters_digest}</p>
        {detail.decision_reason && <p className="whitespace-pre-wrap break-words">决定理由：{detail.decision_reason}</p>}
        {detail.can_decide && <div className="space-y-3"><Label htmlFor={`approval-reason-${detail.id}`}>审批理由（必填）</Label>
          <Textarea id={`approval-reason-${detail.id}`} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={1000} disabled={busy} />
          <div className="flex flex-wrap gap-2"><Button disabled={busy || !reason.trim()} onClick={() => void decide("approved")}>批准冻结动作</Button>
            <Button variant="destructive" disabled={busy || !reason.trim()} onClick={() => void decide("rejected")}>拒绝</Button></div></div>}
        {detail.can_cancel && <Button variant="outline" disabled={busy} onClick={() => void decide("cancel")}>取消未消费申请</Button>}
      </>}
      <Button variant="outline" disabled={busy || loading} onClick={() => void read()}>重新读取当前状态</Button>
    </div>}
  </AppDrawer>;
}
