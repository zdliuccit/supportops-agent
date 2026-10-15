import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { Button } from "@/components/ui/button";
import type { ErasureRequestSummary } from "@/types";

export interface RequestCommandTarget {
  /** 服务器返回的本人申请及并发版本。 */
  request: ErasureRequestSummary;
  /** 仅预览或撤回，没有批准/擦除指令。 */
  command: "preview" | "withdraw";
}
export function RequestCommandDialog({ target, busy, onClose, onConfirm }: {
  target: RequestCommandTarget | null; busy: boolean; onClose: () => void; onConfirm: () => void;
}) {
  const preview = target?.command === "preview";
  return <Dialog open={target !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>{preview ? "生成一次影响预览" : "撤回本人申请"}</DialogTitle></DialogHeader>
      <DialogBody><DialogDescription>{preview
        ? "按申请固定类别和截止时间统计已知关联，保留未知与阻塞原因。预览生成后不可覆盖；重新盘点须新建申请。不会批准或擦除数据。"
        : "撤回后申请不可重新开启，原申请和已生成预览仍保留。此操作不会撤回既有业务动作或擦除任何数据。"}</DialogDescription></DialogBody>
      <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
        <Button disabled={busy} onClick={onConfirm}>{busy ? "处理中…" : preview ? "生成预览" : "确认撤回"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
