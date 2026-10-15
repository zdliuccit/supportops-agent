import { AlertDialog, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import { Button } from "@/components/ui/button";
import type { AuthSession } from "@/types";

export function RevokeSessionDialog({ target, busy, onClose, onConfirm }: {
  target: AuthSession | "all" | null; busy: boolean; onClose: () => void; onConfirm: () => void;
}) {
  const all = target === "all";
  return <AlertDialog open={target !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <AlertDialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <AlertDialogHeader><AlertDialogTitle>{all ? "撤销全部登录会话" : "撤销登录会话"}</AlertDialogTitle></AlertDialogHeader>
      <AlertDialogBody><AlertDialogDescription>
        {all ? "所有当前登录会话将失效，包括本页。需要重新输入账号和密码登录。"
          : target && target.is_current ? "本页登录会话将失效，需要重新登录。" : "此会话将永久失效，持有该会话的页面需要重新登录。"}
        已提交的 Agent 任务仍按当前业务授权执行；注销不等于取消业务任务。
      </AlertDialogDescription></AlertDialogBody>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
        <Button variant="destructive" disabled={busy} onClick={onConfirm}>{busy ? "撤销中…" : "确认撤销"}</Button>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}
