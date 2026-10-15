import { useRef, useState } from "react";
import { createErasureRequest } from "@/api";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withAuthenticatedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import type { DataCategory, ErasureRequestInput } from "@/types";
import { categoryLabels } from "../lib";

export function CreateRequestDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [categories, setCategories] = useState<DataCategory[]>([]);
  const [reason, setReason] = useState<ErasureRequestInput["reason_code"]>("privacy_request");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false), previous = useRef<{ body: string; key: string } | null>(null);
  function changeCategories(category: DataCategory, checked: boolean) {
    const next = checked ? [...categories, category] : categories.filter((value) => value !== category);
    setCategories(next); setError(next.length ? undefined : "请至少选择一个数据类别");
  }
  async function submit() {
    if (submitting.current) return;
    if (!categories.length) { setError("请至少选择一个数据类别"); return; }
    const payload: ErasureRequestInput = { categories: (Object.keys(categoryLabels) as DataCategory[]).filter((value) => categories.includes(value)), reason_code: reason };
    const body = JSON.stringify(payload);
    const submission = previous.current?.body === body ? previous.current : { body, key: crypto.randomUUID() };
    previous.current = submission;
    const key = submission.key;
    submitting.current = true; setBusy(true);
    try {
      await withAuthenticatedToken((token) => createErasureRequest(token, payload, key));
      notify.success("本人数据申请已保存；可进一步生成影响预览。"); onCreated();
    } catch (cause) { notify.error(cause, "申请结果未确认，同一范围重试将复用幂等键"); }
    finally { submitting.current = false; setBusy(false); }
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>申请本人数据影响预览</DialogTitle><DialogDescription>保存本人申请意向，生成后仍需独立审查；当前不会隔离或擦除任何数据。</DialogDescription></DialogHeader>
      <DialogBody><form id="create-data-request" noValidate className="space-y-5" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <FormField label="数据类别" htmlFor="data-categories" required error={error}>
          <div id="data-categories" role="group" aria-label="数据类别" aria-invalid={Boolean(error)} aria-describedby={error ? "data-categories-error" : undefined} className="grid gap-3 sm:grid-cols-2">
            {(Object.keys(categoryLabels) as DataCategory[]).map((category) => <Label key={category} className="flex items-center gap-2 font-normal">
              <Checkbox disabled={busy} checked={categories.includes(category)} onCheckedChange={(checked) => changeCategories(category, checked === true)} />{categoryLabels[category]}</Label>)}
          </div>
        </FormField>
        <FormField label="申请原因" htmlFor="data-reason" required>
          <Select value={reason} disabled={busy} onValueChange={(value) => { if (value === "privacy_request" || value === "account_closure" || value === "data_correction") setReason(value); }}>
            <SelectTrigger id="data-reason"><SelectValue /></SelectTrigger><SelectContent>
              <SelectItem value="privacy_request">个人数据权益申请</SelectItem><SelectItem value="account_closure">账户关闭意向</SelectItem><SelectItem value="data_correction">数据纠正意向</SelectItem>
            </SelectContent></Select>
        </FormField>
        <p className="text-sm text-muted-foreground">申请仅属于当前登录本人。关闭账户意向不会注销账户；共享知识、凭据、审计与外部副本仍需盘点。</p>
      </form></DialogBody>
      <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
        <Button type="submit" form="create-data-request" disabled={busy}>{busy ? "保存中…" : "保存申请"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
