import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { listMockResourceGrants, listUsers, setMockResourceGrant } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { SearchableSelect } from "@/components/SearchableSelect";
import { PageHeader } from "@/components/PageHeader";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/FormField";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { MockResourceGrant } from "@/types";

/** 管理员配置显式人员授权；审批与客服处理分别授予并保留撤销历史。 */
export function MockResourceGrantPanel() {
  const [items, setItems] = useState<MockResourceGrant[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [owner, setOwner] = useState("");
  const [user, setUser] = useState("");
  const [permission, setPermission] = useState<MockResourceGrant["permission"]>("approve");
  const sequence = useRef(0);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const savingRef = useRef(false);
  useLayoutEffect(() => { if (busy && open) dialogRef.current?.focus(); }, [busy, open]);
  const userOptions = useCallback(async (keywords: string, pageSize: number) => {
    const response = await withRefreshedToken((token) => listUsers(token, { page: 1, pageSize, keywords }));
    return response.value.items.map((item) => ({ value: item.id, label: item.display_name }));
  }, []);
  async function load(pagination = page) {
    const current = ++sequence.current;
    setLoading(true); setError(null); setItems([]);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => listMockResourceGrants(token, pagination.current, pagination.pageSize)));
      if (current === sequence.current) { setItems(response.value.items); setTotal(response.value.total); }
    } catch (cause) { if (current === sequence.current) { setTotal(0); setError(cause instanceof Error ? cause.message : "读取显式授权失败"); } }
    finally { if (current === sequence.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, []);
  async function save(existing?: MockResourceGrant) {
    if (savingRef.current || (!existing && (!owner || !user || permission === "approve" && owner === user))) return;
    savingRef.current = true;
    setBusy(true); setError(null);
    try {
      await withRefreshedToken((token) => setMockResourceGrant(token, existing ? {
        owner_user_id: existing.owner_user_id, user_id: existing.user_id, permission: existing.permission,
        is_enabled: !existing.is_enabled, expected_revision: existing.revision,
      } : { owner_user_id: owner, user_id: user, permission, is_enabled: true, expected_revision: 0 }));
      notify.success("显式资源授权已更新。"); setOpen(false); await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存授权失败"); notify.error(cause, "保存授权失败"); } finally { savingRef.current = false; setBusy(false); }
  }
  const columns: AppTableColumn<MockResourceGrant>[] = [
    { title: "资源主体", dataIndex: "owner_user_id", key: "owner", render: (_, grant) => <span className="break-all">{grant.owner_user_id}</span> },
    { title: "授权人员", dataIndex: "user_id", key: "user", render: (_, grant) => <span className="break-all">{grant.user_id}</span> },
    { title: "权限", key: "permission", render: (_, grant) => ({ approve: "独立审批", ticket_handle: "客服处理", support_context_read: "Mock 排查上下文读取" })[grant.permission] },
    { title: "状态", key: "enabled", render: (_, grant) => grant.is_enabled ? "已启用" : "已撤销" },
    { title: "操作", key: "actions", render: (_, grant) => <Button size="sm" variant="outline" disabled={busy} onClick={() => void save(grant)}>{grant.is_enabled ? "撤销授权" : "重新启用"}</Button> },
  ];
  return <section className="space-y-3"><PageHeader title="Mock 项目人员授权" description="仅配置隔离项目的明确审批/客服人员，不能授予知识正文读取权限。"
    actions={<Button disabled={busy} onClick={(event) => { triggerRef.current = event.currentTarget; notify.dismissSuccess(); setOwner(""); setUser(""); setPermission("approve"); setError(null); setOpen(true); }}>授予权限</Button>} />
    <ListToolbar title="显式授权列表" loading={loading} onRefresh={() => void load()} />
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <div className="relative min-h-32"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="Mock 项目人员授权"
      pagination={{ ...page, total, onChange: (current, pageSize) => { const next = { current, pageSize }; setPage(next); void load(next); } }} />
      {loading && <ListLoadingOverlay label="正在读取显式授权…" />}</div>
    <Dialog open={open} onOpenChange={(value) => { if (!busy) setOpen(value); }}><DialogContent ref={dialogRef} tabIndex={-1} closeDisabled={busy}
      onCloseAutoFocus={(event) => { event.preventDefault(); triggerRef.current?.focus({ preventScroll: true }); }} onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>授予 Mock 项目权限</DialogTitle><DialogDescription>指定项目资源主体与处理人员；审批者不得是该资源主体本人。</DialogDescription></DialogHeader>
      <DialogBody className="space-y-4"><FormField label="资源主体" htmlFor="grant-owner" required><SearchableSelect id="grant-owner" value={owner} onValueChange={setOwner} loadOptions={userOptions} placeholder="选择项目资源主体" disabled={busy} className="w-full min-w-0" /></FormField>
        <FormField label="授权人员" htmlFor="grant-user" required error={permission === "approve" && owner === user && owner ? "不能授予本人自审权限。" : undefined}><SearchableSelect id="grant-user" value={user} onValueChange={setUser} loadOptions={userOptions} placeholder="选择当前租户处理人员" disabled={busy}
          ariaInvalid={Boolean(permission === "approve" && owner === user && owner)} ariaDescribedBy={permission === "approve" && owner === user && owner ? "grant-user-error" : undefined} className="w-full min-w-0" /></FormField>
    <Select value={permission} onValueChange={(value) => { if (value === "approve" || value === "ticket_handle" || value === "support_context_read") setPermission(value); }} disabled={busy}>
          <SelectTrigger aria-label="授权类型"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="approve">独立审批</SelectItem><SelectItem value="ticket_handle">客服处理</SelectItem><SelectItem value="support_context_read">Mock 排查上下文读取（知识仍按 ACL）</SelectItem></SelectContent></Select>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      </DialogBody><DialogFooter><Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>取消</Button>
        <Button disabled={busy || !owner || !user || permission === "approve" && owner === user} onClick={() => void save()}>{busy ? "正在保存…" : "授予权限"}</Button></DialogFooter>
    </DialogContent></Dialog>
  </section>;
}
