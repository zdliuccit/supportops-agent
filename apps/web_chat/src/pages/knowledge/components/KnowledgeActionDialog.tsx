import { AlertDialog, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import { Button } from "@/components/ui/button";
import type { KnowledgeVersion } from "@/types";
import { useKnowledgeBusyDialogFocus } from "../lib/useKnowledgeDialogFocus";

export type KnowledgeAction = { action: "submit" | "publish" | "retire" | "rollback"; version: KnowledgeVersion } | { action: "archive" };
const labels = { submit: "提交审核", publish: "发布版本", retire: "失效版本", rollback: "创建回滚草稿", archive: "归档文档" };
const descriptions = {
  submit: "送审不等于批准；创建人和提交人不能审核自己的版本。",
  publish: "仅发布当前独立审核批准的范围，替换文档当前版本；访问范围或发布基线变化后须重新送审。",
  retire: "失效后该版本不能再作为当前知识证据；历史正文保留，恢复需要创建回滚草稿并重新审核。",
  rollback: "只创建新的草稿，不直接恢复发布指针或旧批准；仍需独立审核和发布。",
  archive: "归档将隐藏文档及全部版本并使当前证据失效。此页面不提供撤销，请核对文档标识后确认。",
};
export function KnowledgeActionDialog({ target, documentKey, busy, onClose, onConfirm, onCloseAutoFocus }: {
  target: KnowledgeAction; documentKey: string; busy: boolean; onClose: () => void; onConfirm: () => void; onCloseAutoFocus?: (event: Event) => void;
}) {
  const contentRef = useKnowledgeBusyDialogFocus(busy);
  return <AlertDialog open onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <AlertDialogContent ref={contentRef} aria-busy={busy} onCloseAutoFocus={onCloseAutoFocus} onEscapeKeyDown={event => { if (busy) event.preventDefault(); }}>
      <AlertDialogHeader><AlertDialogTitle>{labels[target.action]}</AlertDialogTitle><AlertDialogDescription>{descriptions[target.action]}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogBody><p className="break-all text-sm">文档：{documentKey}</p>{"version" in target && <p className="mt-2 break-all text-sm">版本：v{target.version.version_number} · {target.version.title}</p>}</AlertDialogBody>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>取消</AlertDialogCancel><Button variant={target.action === "archive" || target.action === "retire" ? "destructive" : "default"} disabled={busy} onClick={onConfirm}>{busy ? "处理中…" : `确认${labels[target.action]}`}</Button></AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}
