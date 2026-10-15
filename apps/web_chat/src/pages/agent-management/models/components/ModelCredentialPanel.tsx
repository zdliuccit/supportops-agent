import { useEffect, useRef, useState } from "react";
import { listModelCredentialRevisions, listModelCredentialAudits, revokeModelCredentialRevision } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogBody, DialogFooter } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { ModelEndpoint, ModelCredentialRevision, ModelCredentialAudit } from "@/types";

/** 永久撤销只针对指定 revision；轮换不重写历史或解除撤销。 */
export function ModelCredentialPanel({ endpoint, onChanged }: { endpoint: ModelEndpoint; onChanged: () => Promise<void> }) {
  const [revisions, setRevisions] = useState<ModelCredentialRevision[]>([]);
  const [audits, setAudits] = useState<ModelCredentialAudit[]>([]);
  const [revisionTotal, setRevisionTotal] = useState(0);
  const [auditTotal, setAuditTotal] = useState(0);
  const [revisionPage, setRevisionPage] = useState({ current: 1, pageSize: 20 });
  const [auditPage, setAuditPage] = useState({ current: 1, pageSize: 20 });
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [revisionError, setRevisionError] = useState<string | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ModelCredentialRevision | null>(null);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const sequence = useRef({ revision: 0, audit: 0, scope: 0 });
  async function loadRevisions(page = revisionPage) {
    const current = ++sequence.current.revision;
    setRevisionLoading(true); setRevisionError(null); setRevisions([]); setRevisionTotal(0);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => listModelCredentialRevisions(token, endpoint.id, page.current, page.pageSize)));
      if (current === sequence.current.revision) { setRevisions(response.value.items); setRevisionTotal(response.value.total); }
    } catch (cause) { if (current === sequence.current.revision) setRevisionError(cause instanceof Error ? cause.message : "读取凭据历史失败"); }
    finally { if (current === sequence.current.revision) setRevisionLoading(false); }
  }
  async function loadAudits(page = auditPage) {
    const current = ++sequence.current.audit;
    setAuditLoading(true); setAuditError(null); setAudits([]); setAuditTotal(0);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => listModelCredentialAudits(token, endpoint.id, page.current, page.pageSize)));
      if (current === sequence.current.audit) { setAudits(response.value.items); setAuditTotal(response.value.total); }
    } catch (cause) { if (current === sequence.current.audit) setAuditError(cause instanceof Error ? cause.message : "读取凭据审计失败"); }
    finally { if (current === sequence.current.audit) setAuditLoading(false); }
  }
  useEffect(() => { const first = { current: 1, pageSize: 20 }; setRevisionPage(first); setAuditPage(first); setSelected(null);
    void loadRevisions(first); void loadAudits(first);
    return () => { sequence.current.revision += 1; sequence.current.audit += 1; sequence.current.scope += 1; }; }, [endpoint.id]);
  async function revoke() {
    if (!selected || !endpoint.credential_revision) return;
    if (!reason.trim()) { setReasonError("请输入撤销说明"); return; }
    const scope = sequence.current.scope;
    setBusy(true);
    try {
      await withRefreshedToken((token) => revokeModelCredentialRevision(token, endpoint.id, selected.revision, endpoint.credential_revision!, reason.trim()));
      if (scope !== sequence.current.scope) return;
      setSelected(null); notify.success("指定凭据 revision 已永久撤销；后续调用停止，已发出的请求仍需等待收敛。");
      await onChanged(); await loadRevisions(); await loadAudits();
    } catch (cause) { if (scope === sequence.current.scope) notify.error(cause, "撤销未确认，请刷新后检查同一 revision 状态"); }
    finally { if (scope === sequence.current.scope) setBusy(false); }
  }
  const revisionColumns: AppTableColumn<ModelCredentialRevision>[] = [
    { title: "修订", key: "revision", render: (_, item) => `${item.revision}${item.revision === endpoint.credential_revision ? "（当前）" : ""}` },
    { title: "脱敏提示 / Provider", key: "hint", render: (_, item) => <span>{item.masked_hint} · {item.provider}</span> },
    { title: "状态", key: "status", render: (_, item) => <StatusBadge value={item.revoked_at ? "revoked" : item.provider === "local_envelope_v1" ? "active" : "unknown"} label={item.revoked_at ? "永久撤销" : item.provider === "local_envelope_v1" ? "未撤销" : "Provider 不可用"} /> },
    { title: "撤销说明", key: "reason", render: (_, item) => item.revocation_reason ?? "—" },
    { title: "操作", key: "actions", align: "right", render: (_, item) => <Button variant="destructive" size="sm" disabled={busy || item.revoked_at !== null || endpoint.credential_revision === null}
      onClick={() => { setSelected(item); setReason(""); setReasonError(undefined); }}>永久撤销此修订</Button> },
  ];
  const labels: Record<string, string> = { credential_rotated: "轮换", credential_revoked: "永久撤销", credential_resolved: "受控解封", credential_access_denied: "拒绝访问" };
  const auditColumns: AppTableColumn<ModelCredentialAudit>[] = [
    { title: "时间", key: "time", render: (_, item) => new Date(item.created_at).toLocaleString() },
    { title: "操作", key: "action", render: (_, item) => labels[item.action] ?? item.action },
    { title: "修订 / 用途", key: "scope", render: (_, item) => `${item.metadata.credential_revision ?? "—"} · ${item.metadata.purpose ?? "控制面"}` },
    { title: "原因", key: "code", render: (_, item) => item.metadata.code ?? "—" },
  ];
  return <section className="mt-5 space-y-4" aria-label="凭据生命周期">
    <p className="text-sm text-muted-foreground">当前凭据 {endpoint.credential_status === "available" ? "可用" : endpoint.credential_status === "revoked" ? "已永久撤销" : "不可用"} · 修订 {endpoint.credential_revision ?? "未知"}。撤销不可解除；轮换只能产生新修订，旧 Run 不会改用新密钥。</p>
    <div className="rounded-2xl bg-white"><ListToolbar title="凭据修订列表" loading={revisionLoading} onRefresh={() => void loadRevisions()} />
      {revisionError && <p role="alert" className="text-sm text-red-600">{revisionError}</p>}
      <div className="relative min-h-24"><AppTable columns={revisionColumns} dataSource={revisions} rowKey="id" ariaLabel="凭据修订历史" emptyText="暂无可见凭据修订"
        pagination={{ ...revisionPage, total: revisionTotal, onChange: (current, pageSize) => { const next = { current, pageSize }; setRevisionPage(next); void loadRevisions(next); } }} />
        {revisionLoading && <ListLoadingOverlay label="正在读取凭据修订…" />}</div></div>
    <div className="rounded-2xl bg-white"><ListToolbar title="凭据审计列表" loading={auditLoading} onRefresh={() => void loadAudits()} />
      {auditError && <p role="alert" className="text-sm text-red-600">{auditError}</p>}
      <div className="relative min-h-24"><AppTable columns={auditColumns} dataSource={audits} rowKey="id" ariaLabel="凭据访问审计" emptyText="暂无凭据审计"
        pagination={{ ...auditPage, total: auditTotal, onChange: (current, pageSize) => { const next = { current, pageSize }; setAuditPage(next); void loadAudits(next); } }} />
        {auditLoading && <ListLoadingOverlay label="正在读取凭据审计…" />}</div></div>
    <Dialog open={selected !== null} onOpenChange={(open) => { if (!open && !busy) setSelected(null); }}><DialogContent>
      <DialogHeader><DialogTitle>永久撤销凭据修订 {selected?.revision}</DialogTitle><DialogDescription>原历史记录仍可追踪，但后续模型/工具调用及最终提交将拒绝使用此修订。已发出的外部请求不能保证撤回。</DialogDescription></DialogHeader>
      <form noValidate onSubmit={(event) => { event.preventDefault(); void revoke(); }}><DialogBody>
        <FormField label="撤销说明" htmlFor="credential-revoke-reason" required error={reasonError}>
          <Textarea id="credential-revoke-reason" placeholder="请输入撤销说明" value={reason} maxLength={500} disabled={busy}
            aria-invalid={Boolean(reasonError)} aria-describedby={reasonError ? "credential-revoke-reason-error" : undefined}
            onChange={(event) => { setReason(event.target.value); setReasonError(event.target.value.trim() ? undefined : "请输入撤销说明"); }} />
        </FormField>
      </DialogBody><DialogFooter><Button variant="outline" disabled={busy} onClick={() => setSelected(null)}>取消</Button>
        <Button type="submit" variant="destructive" disabled={busy}>{busy ? "正在撤销…" : "确认永久撤销"}</Button></DialogFooter></form>
    </DialogContent></Dialog>
  </section>;
}
