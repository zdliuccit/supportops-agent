import { useCallback, useEffect, useRef, useState } from "react";
import { createActionApproval, listActionApprovers, listApprovals } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { SearchableSelect } from "@/components/SearchableSelect";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import { ApprovalDetailDrawer, ApprovalStatusBadge } from "@/pages/approvals/ApprovalDetailDrawer";
import type { ApprovalSummary, BusinessActionDetail } from "@/types";

/** 申请者只能选择本项目显式获准的非本人审批者。 */
export function ActionApprovalPanel({ action, runId, onChanged }: { action: BusinessActionDetail; runId: string; onChanged: () => void }) {
  const [items, setItems] = useState<ApprovalSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approver, setApprover] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const sequence = useRef(0);
  const contentRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const submittingRef = useRef(false);
  const options = useCallback(async (keywords: string, pageSize: number) => {
    const response = await withRefreshedToken((token) => listActionApprovers(token, action.id, runId, keywords, pageSize));
    return response.value.items.map((user) => ({ value: user.user_id, label: user.display_name }));
  }, [action.id, runId]);
  async function load(pagination = page) {
    const current = ++sequence.current;
    setLoading(true); setError(null); setItems([]);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => listApprovals(token, { page: pagination.current, pageSize: pagination.pageSize, actionId: action.id })));
      if (sequence.current === current) { setItems(response.value.items); setTotal(response.value.total); }
    } catch (cause) { if (sequence.current === current) setError(cause instanceof Error ? cause.message : "读取审批失败"); }
    finally { if (sequence.current === current) setLoading(false); }
  }
  useEffect(() => { const first = { current: 1, pageSize: 20 }; setPage(first); setApprover(""); void load(first); return () => { sequence.current += 1; }; }, [action.id, action.status]);
  async function submit() {
    if (submittingRef.current || !approver) return;
    submittingRef.current = true;
    setBusy(true); setError(null);
    try {
      const response = await withRefreshedToken((token) => createActionApproval(token, action, runId, approver));
      notify.success("冻结审批申请已保存，批准前不会执行。"); setSelectedId(response.value.id); onChanged(); await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "创建审批失败"); notify.error(cause, "创建审批失败"); } finally { submittingRef.current = false; setBusy(false); }
  }
  const columns: AppTableColumn<ApprovalSummary>[] = [
    { title: "审批状态", key: "status", render: (_, item) => <ApprovalStatusBadge status={item.status} /> },
    { title: "有效至", key: "expires", render: (_, item) => new Date(item.expires_at).toLocaleString() },
    { title: "操作", key: "action", render: (_, item) => <Button size="sm" variant="outline" onClick={(event) => { triggerRef.current = event.currentTarget; setSelectedId(item.id); }}>查看</Button> },
  ];
  return <section ref={contentRef} tabIndex={-1} aria-label="动作独立审批" className="min-w-0 space-y-3 outline-none">
    {action.status === "proposed" && <div className="space-y-2"><Label htmlFor={`approval-approver-${action.id}`}>指定审批人</Label>
      <SearchableSelect id={`approval-approver-${action.id}`} value={approver} onValueChange={setApprover} loadOptions={options} placeholder="选择本项目获准审批人" emptyText="未配置显式审批授权，请联系管理员" disabled={busy} className="max-w-full" />
      <Button variant="outline" disabled={busy || !approver} onClick={(event) => { triggerRef.current = event.currentTarget; void submit(); }}>{busy ? "正在申请…" : "申请独立审批"}</Button></div>}
    <ListToolbar title="审批列表" loading={loading} onRefresh={() => void load()} />
    {error && <p role="alert" className="text-red-600">{error}</p>}
    <div className="relative min-h-24"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="动作审批记录"
      emptyText="尚无审批申请" pagination={{ ...page, total, onChange: (current, pageSize) => { const next = { current, pageSize }; setPage(next); void load(next); } }} />
      {loading && <ListLoadingOverlay label="正在读取审批…" />}</div>
    <ApprovalDetailDrawer approvalId={selectedId} onClose={() => setSelectedId(null)} onChanged={() => { void load(); onChanged(); }} onCloseAutoFocus={(event) => {
      event.preventDefault();
      (triggerRef.current?.isConnected && !triggerRef.current.disabled ? triggerRef.current : contentRef.current)?.focus({ preventScroll: true });
    }} />
  </section>;
}
