import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { Button } from "@/components/ui/button";
import type { IngestionCreationSnapshot } from "../hooks/useIngestionCreation";
import { ingestionTime } from "../lib/presentation";

export function IngestionCreateDialog({ snapshot, busy, storageError, onClose, onCreate, onQuery, onStop }: {
  snapshot: IngestionCreationSnapshot | null; busy: boolean;
  storageError: string | null;
  onClose: () => void; onCreate: () => void; onQuery: () => void; onStop: () => void;
}) {
  return <Dialog open={snapshot !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>{snapshot?.uncertain ? "查询待确认创建" : "创建知识导入任务"}</DialogTitle>
        <DialogDescription>{snapshot?.uncertain ? "按原请求查询状态，或另行确认封锁该创建请求。" : "仅登记当前获准来源的单次任务；创建后仍需在详情中明确执行。"}</DialogDescription></DialogHeader>
      <DialogBody className="space-y-4">
        {snapshot && <dl className="space-y-3 text-sm">
          <div><dt className="text-muted-foreground">知识来源</dt><dd className="break-all">{snapshot.source?.source_name ?? snapshot.sourceId}</dd></div>
          <div><dt className="text-muted-foreground">获准文件数</dt><dd>{snapshot.source?.object_count ?? "未恢复，仅查询原请求"}</dd></div>
          <div><dt className="text-muted-foreground">来源批准截止</dt><dd>{ingestionTime(snapshot.source?.valid_until ?? null)}</dd></div>
          <div><dt className="text-muted-foreground">本次请求标识</dt><dd className="break-all text-xs">{snapshot.requestKey}</dd></div>
        </dl>}
        <p className="text-sm leading-6 text-muted-foreground">执行只读取已批准的精确清单，生成的候选仍需独立审核和发布。不能通过此页面填写目录、扩大来源范围或修改知识权限。</p>
        {snapshot?.uncertain && <p role="status" className="text-sm leading-6">创建结果尚未确认，请查询本次请求。暂时没有查询到记录也不能证明在途请求未提交；若决定放弃，可明确封锁原创建请求，服务器若已生成任务则保留并返回该任务。</p>}
        {storageError && <p role="alert" className="text-sm text-red-600">{storageError}</p>}
      </DialogBody>
      <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>返回</Button>
        {snapshot?.uncertain && <Button variant="outline" disabled={busy} onClick={onStop}>封锁原创建请求</Button>}
        {snapshot?.uncertain ? <Button disabled={busy} onClick={onQuery}>{busy ? "正在查询…" : "查询创建结果"}</Button>
          : <Button disabled={busy || !snapshot || Boolean(storageError)} onClick={onCreate}>{busy ? "正在创建…" : "确认创建任务"}</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
