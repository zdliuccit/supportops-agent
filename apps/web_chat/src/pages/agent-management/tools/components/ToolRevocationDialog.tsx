import {
  AlertDialog, AlertDialogAction, AlertDialogBody, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/AppDialog";
import type { ToolCatalogEntry } from "@/types";

interface ToolRevocationDialogProps {
  tool: ToolCatalogEntry | null;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}

export function ToolRevocationDialog({ tool, busy, onClose, onConfirm }: ToolRevocationDialogProps) {
  const restoring = tool?.is_revoked === true;
  return <AlertDialog open={tool !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{restoring ? "解除紧急撤销" : "紧急撤销工具"}</AlertDialogTitle>
        <AlertDialogDescription>{tool?.name}</AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogBody>
        <p className="text-sm text-muted-foreground">{restoring
          ? "解除后仍按目录启停、当前权限和风险策略判断调用。目录未启用时，工具仍不可执行。"
          : "已发布版本与排队请求的后续调用将被拒绝。正在执行的只读调用返回时会重新检查撤销状态，审计记录继续保留。"}</p>
      </AlertDialogBody>
      <AlertDialogFooter>
        <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
        <AlertDialogAction disabled={busy} onClick={(event) => { event.preventDefault(); void onConfirm(); }}>{busy ? "处理中…" : restoring ? "确认解除" : "确认撤销"}</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}
