import {
  AlertDialog, AlertDialogAction, AlertDialogBody, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/AppDialog";
import type { ToolDiscoveryView } from "@/types";

export function SourceInvalidationDialog({ source, busy, onClose, onConfirm }: {
  source: ToolDiscoveryView; busy: boolean; onClose: () => void; onConfirm: () => Promise<void>;
}) {
  return <AlertDialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>使当前目录失效</AlertDialogTitle>
        <AlertDialogDescription>{source.source_key} · 修订 {source.revision}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogBody><p className="text-sm text-muted-foreground">确认后，原固定目录不能继续解析，需要重新提交目录验证。历史记录保留。此操作不会连接远端来源，也不会自动重新验证。</p></AlertDialogBody>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
        <AlertDialogAction disabled={busy} onClick={(event) => { event.preventDefault(); void onConfirm(); }}>{busy ? "处理中…" : "确认使目录失效"}</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}
