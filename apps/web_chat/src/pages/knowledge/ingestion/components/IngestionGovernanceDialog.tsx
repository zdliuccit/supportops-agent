import { useCallback } from "react";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { SearchableSelect, type SearchableSelectOption } from "@/components/SearchableSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { EditableIngestionGovernance } from "../lib/governance";

export function IngestionGovernanceDialog({ snapshot, owner, due, errors, busy, onOwner, onDue,
  onClose, onSave, loadOwners }: {
  snapshot: EditableIngestionGovernance | null; owner: string; due: string; errors: { owner: string; due: string };
  busy: boolean; onOwner: (value: string) => void; onDue: (value: string) => void;
  onClose: () => void; onSave: () => void;
  loadOwners: (keywords: string, pageSize: number) => Promise<SearchableSelectOption[]>;
}) {
  const resolveOwner = useCallback(async (value: string) => snapshot?.owner_user_id === value ? {
    value, label: snapshot.owner_display_name ?? value, description: snapshot.owner_active ? "当前负责人" : "当前负责人已停用",
  } : null, [snapshot]);
  return <Dialog open={snapshot !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <form noValidate onSubmit={(event) => { event.preventDefault(); if (!busy) onSave(); }}>
        <DialogHeader><DialogTitle>设置负责人和复核日期</DialogTitle>
          <DialogDescription>仅更新文档治理信息。负责人不会因此获得知识 ACL，候选仍须独立审核后发布。</DialogDescription></DialogHeader>
        <DialogBody className="space-y-5">
          <FormField label="文档负责人" htmlFor="ingestion-review-owner" required error={errors.owner}>
            <div id="ingestion-review-owner" role="group" aria-label="文档负责人" aria-invalid={Boolean(errors.owner)}
              aria-describedby={errors.owner ? "ingestion-review-owner-error" : undefined}>
              <SearchableSelect value={owner} onValueChange={onOwner} disabled={busy} loadOptions={loadOwners}
                resolveOption={resolveOwner} placeholder="请选择文档负责人" searchPlaceholder="请输入负责人姓名"
                emptyText="暂无匹配的有效用户，请输入姓名继续查询" clearLabel="清除选择" />
            </div>
          </FormField>
          <FormField label="复核日期" htmlFor="ingestion-review-due" error={errors.due}
            hint={`按浏览器本地时区（${Intl.DateTimeFormat().resolvedOptions().timeZone}）输入；留空会清除已配置日期。`}>
            <Input id="ingestion-review-due" type="datetime-local" step={1} value={due} disabled={busy}
              placeholder="请输入复核日期" onChange={(event) => onDue(event.target.value)}
              aria-invalid={Boolean(errors.due)} aria-describedby={errors.due ? "ingestion-review-due-error" : undefined} />
          </FormField>
          <p className="text-xs leading-5 text-muted-foreground">已到期日期会显示页面提示，不会自动失效知识、重置审核或发送通知。保存时若候选、ACL或治理信息变化，服务器会拒绝覆盖。更换负责人后，原导入权限映射需要另行复核。</p>
        </DialogBody>
        <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>返回</Button>
          <Button type="submit" disabled={busy || !snapshot}>{busy ? "正在保存…" : "确认保存"}</Button></DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}
