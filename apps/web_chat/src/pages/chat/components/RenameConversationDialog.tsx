import type { FormEvent } from "react";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { Input } from "@/components/ui/input";
import type { ConversationSummary } from "@/types";

/** 对话重命名弹窗。 */
export function RenameConversationDialog({ target, value, error, busy, onOpenChange, onSubmit, onValueChange }: { target: ConversationSummary | null; value: string; error?: string; busy: boolean; onOpenChange: (open: boolean) => void; onSubmit: (event: FormEvent) => void; onValueChange: (value: string) => void }) {
  return <Dialog open={target !== null} onOpenChange={onOpenChange}><DialogContent><form onSubmit={onSubmit} noValidate><DialogHeader><DialogTitle>重命名对话</DialogTitle><DialogDescription>输入一个便于在历史记录中识别的名称。</DialogDescription></DialogHeader><DialogBody><FormField label="会话名称" htmlFor="rename-conversation" required error={error} className="mt-5"><Input id="rename-conversation" value={value} onChange={(event) => onValueChange(event.target.value)} placeholder="请输入会话名称" maxLength={200} autoFocus aria-label="会话名称" aria-invalid={Boolean(error)} aria-describedby={error ? "rename-conversation-error" : undefined} /></FormField></DialogBody><DialogFooter className="mt-6"><DialogClose asChild><Button variant="outline">取消</Button></DialogClose><Button type="submit" disabled={busy}>保存</Button></DialogFooter></form></DialogContent></Dialog>;
}
