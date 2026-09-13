import { AlertDialog, AlertDialogAction, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import type { OrganizationUnit } from "@/types";

/** 删除空部门确认弹窗。 */
export function DeleteDepartmentDialog({ target, busy, onOpenChange, onConfirm }: { target: OrganizationUnit | null; busy: boolean; onOpenChange: (open: boolean) => void; onConfirm: () => void }) {
  return <AlertDialog open={target !== null} onOpenChange={onOpenChange}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>删除部门</AlertDialogTitle></AlertDialogHeader><AlertDialogBody><AlertDialogDescription>确定删除“{target?.name}”吗？删除后无法恢复。</AlertDialogDescription></AlertDialogBody><AlertDialogFooter><AlertDialogCancel disabled={busy}>取消</AlertDialogCancel><AlertDialogAction onClick={onConfirm} disabled={busy} className="bg-red-600 text-white hover:bg-red-700">{busy ? "删除中…" : "确认删除"}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>;
}
