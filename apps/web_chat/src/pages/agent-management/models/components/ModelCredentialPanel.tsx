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
import { getStoredAccessToken, subscribeAuthChanges, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { ModelEndpoint, ModelCredentialRevision, ModelCredentialAudit } from "@/types";

interface ModelCredentialPanelProps {
  /** 当前端点，只使用控制面元数据。 */
  endpoint: ModelEndpoint;
  /** 撤销请求结束后刷新当前连接摘要。 */
  onChanged: () => Promise<void>;
}

interface RevocationConfirmation {
  /** 打开确认时选中的固定历史修订。 */
  revision: ModelCredentialRevision;
  /** 打开确认时的当前修订号，不能在提交时随资料刷新而替换。 */
  expectedCurrentRevision: number;
}

function revocationReasonError(value: string): string | undefined {
  const reason = value.trim();
  if (!reason) return "请输入撤销说明";
  return Array.from(reason).length > 500 ? "撤销说明不能超过500个字符" : undefined;
}

/** 切换端点时销毁原确认及请求状态，不能沿用另一个连接的操作意图。 */
export function ModelCredentialPanel(props: ModelCredentialPanelProps) {
  return <ModelCredentialPanelContent key={props.endpoint.id} {...props} />;
}

/** 永久撤销只针对指定 revision；轮换不重写历史或解除撤销。 */
function ModelCredentialPanelContent({ endpoint, onChanged }: ModelCredentialPanelProps) {
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
  const [selected, setSelected] = useState<RevocationConfirmation | null>(null);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [sessionChanged, setSessionChanged] = useState(false);
  const panelToken = useRef(getStoredAccessToken()).current;
  const mounted = useRef(false);
  const mutation = useRef(false);
  const reasonInput = useRef<HTMLTextAreaElement>(null);
  const sequence = useRef({ revision: 0, audit: 0, scope: 0 });
  function currentSession() {
    return mounted.current && panelToken !== null && panelToken === getStoredAccessToken();
  }
  function assertCurrentSession(token: string) {
    if (!currentSession() || token !== panelToken) throw new Error("登录会话已变化，请重新打开模型详情。");
  }
  async function loadRevisions(page = revisionPage) {
    if (!currentSession()) return;
    const current = ++sequence.current.revision;
    const scope = sequence.current.scope;
    const active = () => currentSession() && scope === sequence.current.scope && current === sequence.current.revision;
    setRevisionLoading(true); setRevisionError(null); setRevisions([]); setRevisionTotal(0);
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => {
        assertCurrentSession(token);
        if (!active()) throw new Error("凭据历史查询已失效。");
        return listModelCredentialRevisions(token, endpoint.id, page.current, page.pageSize);
      }));
      if (active() && response.token === panelToken) { setRevisions(response.value.items); setRevisionTotal(response.value.total); }
    } catch (cause) { if (active()) setRevisionError(cause instanceof Error ? cause.message : "读取凭据历史失败"); }
    finally { if (active()) setRevisionLoading(false); }
  }
  async function loadAudits(page = auditPage) {
    if (!currentSession()) return;
    const current = ++sequence.current.audit;
    const scope = sequence.current.scope;
    const active = () => currentSession() && scope === sequence.current.scope && current === sequence.current.audit;
    setAuditLoading(true); setAuditError(null); setAudits([]); setAuditTotal(0);
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => {
        assertCurrentSession(token);
        if (!active()) throw new Error("凭据审计查询已失效。");
        return listModelCredentialAudits(token, endpoint.id, page.current, page.pageSize);
      }));
      if (active() && response.token === panelToken) { setAudits(response.value.items); setAuditTotal(response.value.total); }
    } catch (cause) { if (active()) setAuditError(cause instanceof Error ? cause.message : "读取凭据审计失败"); }
    finally { if (active()) setAuditLoading(false); }
  }
  useEffect(() => {
    mounted.current = true;
    const stop = subscribeAuthChanges(() => {
      sequence.current.scope += 1; setSessionChanged(true); setSelected(null); setReason("");
      setRevisions([]); setAudits([]);
    });
    const first = { current: 1, pageSize: 20 }; setRevisionPage(first); setAuditPage(first); setSelected(null);
    void loadRevisions(first); void loadAudits(first);
    return () => { mounted.current = false; stop(); sequence.current.revision += 1; sequence.current.audit += 1; sequence.current.scope += 1; }; }, [endpoint.id]);

  function beginRevoke(revision: ModelCredentialRevision) {
    const expected = endpoint.credential_revision;
    if (!currentSession() || busy || mutation.current || revision.revoked_at !== null
      || typeof expected !== "number" || !Number.isSafeInteger(expected) || expected < 1) return;
    setSelected({ revision, expectedCurrentRevision: expected }); setReason(""); setReasonError(undefined);
  }
  async function revoke() {
    if (!selected || mutation.current) return;
    const confirmation = selected;
    if (!currentSession() || confirmation.expectedCurrentRevision !== endpoint.credential_revision) {
      setSelected(null); setReason(""); notify.warning("登录会话或当前凭据修订已变化，请刷新后重新确认。"); return;
    }
    const requestedReason = reason.trim();
    const validation = revocationReasonError(requestedReason);
    if (validation) {
      setReasonError(validation); reasonInput.current?.focus(); return;
    }
    const scope = sequence.current.scope;
    const active = () => currentSession() && scope === sequence.current.scope;
    mutation.current = true;
    setBusy(true);
    try {
      const response = await withAuthenticatedToken((token) => {
        assertCurrentSession(token);
        return revokeModelCredentialRevision(token, endpoint.id, confirmation.revision.revision,
          confirmation.expectedCurrentRevision, requestedReason);
      });
      if (!active() || response.token !== panelToken) return;
      const receipt = response.value;
      if (!receipt || receipt.id !== confirmation.revision.id || receipt.revision !== confirmation.revision.revision
        || typeof receipt.revoked_at !== "string" || !Number.isFinite(Date.parse(receipt.revoked_at))
        || typeof receipt.revoked_by !== "string" || !receipt.revoked_by.trim()) throw new Error("撤销回执与确认对象不一致，请刷新核对。");
      setSelected(null); notify.success("指定凭据 revision 已永久撤销；后续调用停止，已发出的请求仍需等待收敛。");
    } catch (cause) {
      if (!active()) return;
      setSelected(null);
      notify.error(cause, "撤销未确认", { description: "请核对同一修订的当前状态；再次操作须重新打开确认，不会自动重发撤销。" });
    } finally {
      if (active()) {
        setReason("");
        const refresh = await Promise.allSettled([
          Promise.resolve().then(() => { if (active()) return onChanged(); }), loadRevisions(), loadAudits(),
        ]);
        if (active() && refresh.some((result) => result.status === "rejected")) notify.warning("凭据资料刷新失败，请手动刷新后核对修订状态。");
      }
      mutation.current = false;
      if (active()) setBusy(false);
    }
  }
  const revisionColumns: AppTableColumn<ModelCredentialRevision>[] = [
    { title: "修订", key: "revision", render: (_, item) => `${item.revision}${item.revision === endpoint.credential_revision ? "（当前）" : ""}` },
    { title: "脱敏提示 / Provider", key: "hint", render: (_, item) => <span>{item.masked_hint} · {item.provider}</span> },
    { title: "状态", key: "status", render: (_, item) => <StatusBadge value={item.revoked_at ? "revoked" : item.provider === "local_envelope_v1" ? "active" : "unknown"} label={item.revoked_at ? "永久撤销" : item.provider === "local_envelope_v1" ? "未撤销" : "Provider 不可用"} /> },
    { title: "撤销说明", key: "reason", render: (_, item) => item.revocation_reason ?? "—" },
    { title: "操作", key: "actions", align: "right", render: (_, item) => <Button variant="destructive" size="sm" disabled={busy || item.revoked_at !== null || endpoint.credential_revision === null}
      onClick={() => beginRevoke(item)}>永久撤销此修订</Button> },
  ];
  const labels: Record<string, string> = { credential_rotated: "轮换", credential_revoked: "永久撤销", credential_resolved: "受控解封", credential_access_denied: "拒绝访问" };
  const auditColumns: AppTableColumn<ModelCredentialAudit>[] = [
    { title: "时间", key: "time", render: (_, item) => new Date(item.created_at).toLocaleString() },
    { title: "操作", key: "action", render: (_, item) => labels[item.action] ?? item.action },
    { title: "修订 / 用途", key: "scope", render: (_, item) => `${item.metadata.credential_revision ?? "—"} · ${item.metadata.purpose ?? "控制面"}` },
    { title: "原因", key: "code", render: (_, item) => item.metadata.code ?? "—" },
  ];
  if (sessionChanged || !panelToken || panelToken !== getStoredAccessToken()) return <p role="alert">登录会话已变化，请重新打开模型详情。</p>;
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
    <Dialog open={selected !== null} onOpenChange={(open) => { if (!open && !mutation.current) { setSelected(null); setReason(""); } }}><DialogContent>
      <DialogHeader><DialogTitle>永久撤销凭据修订 {selected?.revision.revision}</DialogTitle><DialogDescription>确认时当前凭据为修订 {selected?.expectedCurrentRevision}。原历史记录仍可追踪，但后续模型/工具调用及最终提交将拒绝使用目标修订。已发出的外部请求不能保证撤回。</DialogDescription></DialogHeader>
      <form noValidate onSubmit={(event) => { event.preventDefault(); void revoke(); }}><DialogBody>
        <FormField label="撤销说明" htmlFor="credential-revoke-reason" required error={reasonError}>
          <Textarea id="credential-revoke-reason" ref={reasonInput} placeholder="请输入撤销说明" value={reason} maxLength={500} disabled={busy}
            aria-invalid={Boolean(reasonError)} aria-describedby={reasonError ? "credential-revoke-reason-error" : undefined}
            onChange={(event) => {
              if (mutation.current) return;
              setReason(event.target.value); setReasonError(revocationReasonError(event.target.value));
            }} />
        </FormField>
      </DialogBody><DialogFooter><Button variant="outline" disabled={busy} onClick={() => { if (!mutation.current) { setSelected(null); setReason(""); } }}>取消</Button>
        <Button type="submit" variant="destructive" disabled={busy}>{busy ? "正在撤销…" : "确认永久撤销"}</Button></DialogFooter></form>
    </DialogContent></Dialog>
  </section>;
}
