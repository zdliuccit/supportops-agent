import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getAdminUser, getKnowledgeAcl, listOrganizationUnits, listUsers, replaceKnowledgeAcl } from "@/api";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { SearchableSelect } from "@/components/SearchableSelect";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { KnowledgeAclEntry, OrganizationUnit } from "@/types";
import { useKnowledgeBusyDialogFocus } from "../lib/useKnowledgeDialogFocus";

const roles = { platform_admin: "平台管理员", employee: "员工", agent_user: "客服", customer: "客户" };
function flatten(units: OrganizationUnit[]): OrganizationUnit[] {
  return units.flatMap(unit => [unit, ...flatten(unit.children ?? [])]);
}

export function KnowledgeAclDialog({ documentId, onClose, onSaved, onCloseAutoFocus }: { documentId: string; onClose: () => void; onSaved: () => Promise<void>; onCloseAutoFocus?: (event: Event) => void }) {
  const [entries, setEntries] = useState<KnowledgeAclEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const contentRef = useKnowledgeBusyDialogFocus(busy);
  const [error, setError] = useState<string | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const sequence = useRef(0);
  const submitting = useRef(false);
  async function load() {
    const request = ++sequence.current;
    setLoading(true); setLoaded(false); setError(null); setEntries([]);
    try {
      const result = await delayRequest(() => withRefreshedToken(token => getKnowledgeAcl(token, documentId)));
      if (request !== sequence.current) return;
      setEntries(result.value); setLoaded(true); setUnconfirmed(false);
    } catch (cause) {
      if (request === sequence.current) setError(cause instanceof Error ? cause.message : "加载访问范围失败");
    } finally { if (request === sequence.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [documentId]);
  const loadUsers = useCallback(async (keywords: string, pageSize: number) => {
    const result = await withRefreshedToken(token => listUsers(token, { keywords, pageSize, page: 1 }));
    return result.value.items.map(user => ({ value: user.id, label: user.display_name, description: user.email }));
  }, []);
  const resolveUser = useCallback(async (id: string) => {
    const result = await withRefreshedToken(token => getAdminUser(token, id));
    return { value: result.value.id, label: result.value.display_name };
  }, []);
  const loadUnits = useCallback(async (keywords: string) => {
    const result = await withRefreshedToken(token => listOrganizationUnits(token));
    return flatten(result.value.items).filter(unit => unit.name.includes(keywords)).map(unit => ({ value: unit.id, label: unit.name }));
  }, []);
  const resolveUnit = useCallback(async (id: string) => (await loadUnits("")).find(option => option.value === id) ?? null, [loadUnits]);
  function change(index: number, patch: Partial<KnowledgeAclEntry>) {
    setEntries(current => current.map((entry, i) => i === index ? { ...entry, ...patch } : entry)); setError(null);
  }
  const valid = entries.every(entry => entry.subject_id.trim()) && new Set(entries.map(entry => `${entry.subject_type}:${entry.subject_id}`)).size === entries.length;
  const invalidEntry = (entry: KnowledgeAclEntry) => !entry.subject_id.trim() || entries.filter(other => other.subject_type === entry.subject_type && other.subject_id === entry.subject_id).length > 1;
  async function save() {
    if (submitting.current || !loaded || !valid || unconfirmed) return;
    submitting.current = true; setBusy(true);
    try {
      await withRefreshedToken(token => replaceKnowledgeAcl(token, documentId, entries));
      notify.success("访问范围已更新，旧批准可能需要重新送审。");
      await onSaved(); onClose();
    } catch (cause) {
      notify.error(cause, "保存访问范围失败");
      if (!(cause instanceof ApiError) || cause.status >= 500) setUnconfirmed(true);
      // 保存可能已经提交，重新复检父页正文权限，不能继续保留撤权前的编辑副本。
      await onSaved();
    } finally { submitting.current = false; setBusy(false); }
  }
  return <Dialog open onOpenChange={open => { if (!open && !submitting.current) onClose(); }}>
    <DialogContent ref={contentRef} aria-busy={busy} className="max-w-2xl" closeDisabled={busy} onCloseAutoFocus={onCloseAutoFocus} onEscapeKeyDown={event => { if (submitting.current) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>配置访问范围</DialogTitle><DialogDescription>保存将整体替换当前文档的授权。角色、用户与直属部门任一命中即可读取；管理员不隐式获权。留空将拒绝所有正文访问，ACL 变化会使旧批准失效。</DialogDescription></DialogHeader>
      <DialogBody className="relative min-h-32 space-y-4">
        {loading && <ListLoadingOverlay label="正在加载访问范围…" />}
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        {!loading && !loaded && <Button variant="outline" onClick={() => void load()}>重新加载访问范围</Button>}
        {loaded && <>
          {entries.length === 0 && <p role="status" className="text-sm text-muted-foreground">当前配置为空：所有用户均无正文访问权限。</p>}
          {entries.map((entry, index) => <div key={index} className="flex flex-wrap items-start gap-2 rounded-lg border p-3">
            <Select value={entry.subject_type} disabled={busy || unconfirmed} onValueChange={value => change(index, { subject_type: value as KnowledgeAclEntry["subject_type"], subject_id: "" })}>
              <SelectTrigger aria-label={`第${index + 1}条授权类型`} className="w-32"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="role">角色</SelectItem><SelectItem value="user">用户</SelectItem><SelectItem value="organization_unit">直属部门</SelectItem></SelectContent>
            </Select>
            {entry.subject_type === "role" ? <Select value={entry.subject_id} disabled={busy || unconfirmed} onValueChange={value => change(index, { subject_id: value })}>
              <SelectTrigger aria-label={`第${index + 1}条授权对象`} aria-invalid={invalidEntry(entry)} aria-describedby={invalidEntry(entry) ? "knowledge-acl-validation" : undefined} className="min-w-40 flex-1"><SelectValue placeholder="请选择角色" /></SelectTrigger><SelectContent>{Object.entries(roles).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
            </Select> : <SearchableSelect key={entry.subject_type} ariaLabel={`第${index + 1}条授权对象`} ariaInvalid={invalidEntry(entry)} ariaDescribedBy={invalidEntry(entry) ? "knowledge-acl-validation" : undefined} value={entry.subject_id} onValueChange={value => change(index, { subject_id: value })} loadOptions={entry.subject_type === "user" ? loadUsers : loadUnits} resolveOption={entry.subject_type === "user" ? resolveUser : resolveUnit} placeholder={entry.subject_type === "user" ? "请选择用户" : "请选择直属部门"} searchPlaceholder={entry.subject_type === "user" ? "请输入用户姓名" : "请输入部门名称"} disabled={busy || unconfirmed} className="min-w-40 flex-1" />}
            <Button variant="outline" disabled={busy || unconfirmed} aria-label={`移除第${index + 1}条授权`} onClick={() => setEntries(current => current.filter((_, i) => i !== index))}>移除</Button>
          </div>)}
          {!valid && <p id="knowledge-acl-validation" role="alert" className="text-sm text-red-600">请选择每条授权的对象，且不要添加重复授权。</p>}
          <Button variant="outline" disabled={busy || unconfirmed || entries.length >= 100} onClick={() => setEntries(current => [...current, { subject_type: "role", subject_id: "" }])}>添加授权</Button>
          {entries.length >= 100 && <p className="text-sm text-muted-foreground">最多100条授权。</p>}
        </>}
        {unconfirmed && <div role="alert" className="space-y-2 text-sm text-red-600"><p>保存结果未确认，请重新读取服务器配置后核对；不会自动重发保存。</p><Button variant="outline" onClick={() => void load()}>重新加载访问范围</Button></div>}
      </DialogBody>
      <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button disabled={loading || busy || !loaded || !valid || unconfirmed} onClick={() => void save()}>{busy ? "保存中…" : "保存访问范围"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
