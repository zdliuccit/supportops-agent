import { AlertDialog, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import { Button } from "@/components/ui/button";
import type { IngestionCommand } from "../lib/presentation";

export function IngestionCommandDialog({ command, busy, onClose, onConfirm }: {
  command: IngestionCommand | null; busy: boolean; onClose: () => void; onConfirm: () => void;
}) {
  const cancelling = command?.kind === "cancel";
  const executing = command?.kind === "execute";
  return <AlertDialog open={command !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <AlertDialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <AlertDialogHeader><AlertDialogTitle>{executing ? "执行一次知识导入" : cancelling ? "取消导入任务" : "记录任务超时失败"}</AlertDialogTitle></AlertDialogHeader>
      <AlertDialogBody><AlertDialogDescription>
        {executing ? "将读取服务器已经批准的精确清单，并将本页候选保存为草稿。不会自动审核、发布或改变知识权限；执行结果不明时请查询任务，不要重复执行。"
          : cancelling ? "取消将停止该任务后续提交，任务历史和已经保存的知识草稿会保留。取消后不能恢复此任务。"
          : "仅在任务已到期，或执行租约过期且尝试次数耗尽时，记录明确的失败状态。此操作不会重新读取来源，也不会重试导入。"}
        {" "}服务器会再次核对当前任务修订；状态变化时需要刷新并重新确认。
      </AlertDialogDescription>
        {command && <p className="mt-4 break-all text-xs text-muted-foreground">任务 {command.job.id} · 修订 {command.job.revision ?? "不可确认"}</p>}
      </AlertDialogBody>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>返回</AlertDialogCancel>
        <Button variant={executing ? "default" : "destructive"} disabled={busy || !command} onClick={onConfirm}>
          {busy ? executing ? "正在执行…" : "正在提交…" : executing ? "确认执行一次" : cancelling ? "确认取消任务" : "确认记录失败"}
        </Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}
