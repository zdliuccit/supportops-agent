import { useEffect, useState } from "react";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { MockTicketCommandKind } from "@/types";

export const commandLabels: Record<MockTicketCommandKind, string> = { claim: "认领", note: "内部备注", reply: "回复申请者", close: "关闭", escalate: "升级", release: "释放接管" };

/** 未确认命令锁定原参数，关闭焦点由详情页恢复到原操作或内容区域。 */
export function TicketCommandDialog({ kind, body, busy, pending, canSubmit, error, onBodyChange, onClose, onExecute, onCloseAutoFocus }: {
  kind: MockTicketCommandKind | null;
  body: string;
  busy: boolean;
  pending: boolean;
  canSubmit: boolean;
  error: string | null;
  onBodyChange: (value: string) => void;
  onClose: () => void;
  onExecute: () => void;
  onCloseAutoFocus: (event: Event) => void;
}) {
  const [touched, setTouched] = useState(false);
  useEffect(() => { setTouched(false); }, [kind]);
  const required = kind === "note" || kind === "reply";
  const invalid = required && !body.trim();
  const label = kind === "note" ? "仅获准客服可读的备注" : kind === "reply" ? "申请者可读的回复" : "操作说明（可选）";
  return <Dialog open={kind !== null} onOpenChange={(open) => { if (!open && !busy && !pending) onClose(); }}>
    <DialogContent closeDisabled={busy || pending} className="[overflow-wrap:anywhere]" onCloseAutoFocus={onCloseAutoFocus}>
      <DialogHeader><DialogTitle>{kind ? commandLabels[kind] : "人工操作"}</DialogTitle><DialogDescription>只操作当前 Mock 工单。认领冻结关联 Agent 写动作；关闭或释放后明确结束接管。</DialogDescription></DialogHeader>
      <DialogBody className="space-y-4">
        <FormField label={label} htmlFor="mock-command-body" required={required} error={touched && invalid ? "请输入非空的备注或回复。" : undefined}>
          <Textarea id="mock-command-body" placeholder={`请输入${label}`} value={body} onChange={(event) => { setTouched(true); onBodyChange(event.target.value); }} maxLength={1000}
            aria-invalid={touched && invalid || undefined} aria-describedby={touched && invalid ? "mock-command-body-error" : undefined} disabled={busy || pending} />
        </FormField>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        {pending && !busy && <p className="text-sm text-muted-foreground">尚未收到确认，重试会复用同一命令和原参数，不重新生成操作。</p>}
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" disabled={busy || pending} onClick={onClose}>取消</Button>
        <Button variant={kind === "close" ? "destructive" : "default"} disabled={busy || !canSubmit || invalid} onClick={onExecute}>
          {busy ? "正在提交…" : pending ? "重试同一命令" : "确认操作"}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
