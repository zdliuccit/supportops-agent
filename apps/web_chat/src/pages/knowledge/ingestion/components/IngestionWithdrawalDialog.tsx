import { AlertDialog, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withdrawalReasons, type EditableIngestionGovernance } from "../lib/governance";

export function IngestionWithdrawalDialog({ snapshot, reason, error, busy, onReason, onClose, onConfirm }: {
  snapshot: EditableIngestionGovernance | null; reason: string; error: string; busy: boolean;
  onReason: (value: string) => void; onClose: () => void; onConfirm: () => void;
}) {
  return <AlertDialog open={snapshot !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <AlertDialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <AlertDialogHeader><AlertDialogTitle>撤回本次未发布候选</AlertDialogTitle></AlertDialogHeader>
      <AlertDialogBody className="space-y-5">
        <AlertDialogDescription>当前正式版本、知识 ACL 和索引保持不变。本候选将转为已退役并停止送审或发布，已有审核历史和导入回执保留；不能直接恢复为可发布状态。</AlertDialogDescription>
        <FormField label="撤回原因" htmlFor="ingestion-withdraw-reason" required error={error}>
          <Select value={reason} onValueChange={onReason} disabled={busy}>
            <SelectTrigger id="ingestion-withdraw-reason" aria-invalid={Boolean(error)}
              aria-describedby={error ? "ingestion-withdraw-reason-error" : undefined}><SelectValue placeholder="请选择撤回原因" /></SelectTrigger>
            <SelectContent>{Object.entries(withdrawalReasons).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent>
          </Select>
        </FormField>
        <p className="text-xs leading-5 text-muted-foreground">这是您主动记录的处置原因，不代表系统已证实来源内容冲突。服务器会再次核对当前候选和治理范围；若发生变化，需要刷新后重新确认。</p>
        {snapshot && <p className="break-all text-xs text-muted-foreground">任务 {snapshot.job_id}<br />物化记录 {snapshot.materialization_id}</p>}
      </AlertDialogBody>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>返回</AlertDialogCancel>
        <Button variant="destructive" disabled={busy || !snapshot} onClick={onConfirm}>{busy ? "正在撤回…" : "确认撤回候选"}</Button></AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}
