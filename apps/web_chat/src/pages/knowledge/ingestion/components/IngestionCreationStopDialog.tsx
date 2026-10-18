import { AlertDialog, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import { Button } from "@/components/ui/button";
import type { IngestionCreationSnapshot } from "../hooks/useIngestionCreation";

export function IngestionCreationStopDialog({ snapshot, busy, onClose, onConfirm }: {
  snapshot: IngestionCreationSnapshot | null; busy: boolean; onClose: () => void; onConfirm: () => void;
}) {
  return <AlertDialog open={snapshot !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <AlertDialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <AlertDialogHeader><AlertDialogTitle>封锁原创建请求</AlertDialogTitle></AlertDialogHeader>
      <AlertDialogBody><AlertDialogDescription>
        服务器会检查该请求是否已经生成任务。若任务已存在，会返回原任务，不会取消它；
        若任务尚未生成，则永久封锁这个请求标识，迟到的创建请求也不能再生成任务。此操作不会读取来源或发布知识。
      </AlertDialogDescription>
        {snapshot && <p className="mt-4 break-all text-xs text-muted-foreground">原请求 {snapshot.requestKey}</p>}
        <p className="mt-3 text-sm text-muted-foreground">只有服务器确认原任务或停止事实后，页面才清除对应的待确认定位。响应不明时请继续查询。</p>
      </AlertDialogBody>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>继续保留并查询</AlertDialogCancel>
        <Button variant="destructive" disabled={busy || !snapshot} onClick={onConfirm}>{busy ? "正在确认…" : "确认封锁原请求"}</Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}
