import { AlertDialog, AlertDialogAction, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import type { ConversationSummary } from "@/types";

/** 删除对话确认弹窗。 */
export function DeleteConversationDialog({ target, busy, onOpenChange, onConfirm }: { target: ConversationSummary | null; busy: boolean; onOpenChange: (open: boolean) => void; onConfirm: () => void }) {
  return <AlertDialog open={target !== null} onOpenChange={onOpenChange}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除这个对话？</AlertDialogTitle></AlertDialogHeader><AlertDialogBody><AlertDialogDescription>“{target?.title || "未命名对话"}”及其消息和运行记录将被永久删除，此操作无法撤销。</AlertDialogDescription></AlertDialogBody><AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction onClick={onConfirm} disabled={busy}>删除</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>;
}
