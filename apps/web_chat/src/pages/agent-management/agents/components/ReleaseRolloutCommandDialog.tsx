import { useState } from "react";
import { AlertDialog, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ReleaseRolloutCommand, ReleaseRolloutSummary } from "@/types";

export const rolloutCommandLabels = { start: "启动灰度", pause: "暂停灰度", resume: "恢复灰度", expand: "扩大灰度", stop: "停止并撤回候选" };
export function ReleaseRolloutCommandDialog({ operation, rollout, busy, onClose, onConfirm }: {
  operation: ReleaseRolloutCommand["operation"]; rollout: ReleaseRolloutSummary; busy: boolean; onClose: () => void;
  onConfirm: (payload: ReleaseRolloutCommand) => Promise<void>;
}) {
  const [bps, setBps] = useState("");
  const [error, setError] = useState<string | undefined>();
  const maximum = Math.min(9000, rollout.candidate_bps + 1000);
  function validate(value: string): string | undefined {
    const number = Number(value);
    if (!value.trim() || !Number.isInteger(number) || number <= rollout.candidate_bps || number > maximum)
      return `请输入大于${rollout.candidate_bps}且不超过${maximum}的整数万分比`;
  }
  async function confirm() {
    const invalid = operation === "expand" ? validate(bps) : undefined;
    setError(invalid); if (invalid || busy) return;
    await onConfirm({ operation, expected_revision: rollout.revision, ...(operation === "expand" ? { candidate_bps: Number(bps) } : {}) });
  }
  return <AlertDialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}><AlertDialogContent onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
    <AlertDialogHeader><AlertDialogTitle>{rolloutCommandLabels[operation]}</AlertDialogTitle></AlertDialogHeader>
    <AlertDialogBody className="space-y-4"><AlertDialogDescription>{operation === "stop"
      ? "停止后不能恢复。已经启动过的候选版本将被永久撤回，禁止新激活及新写动作；旧会话固定版本、已发出或未知动作仍保留并继续对账。未启动草稿不会撤回候选。"
      : operation === "pause" ? "停止为新会话选择候选版本；既有会话保持原版本。恢复需要重新通过两版本完整门禁，并重新计算观察窗口。"
      : "服务器会重新检查固定基线、两版本完整实测、批准策略与当前修订；扩大还须满足观察窗口、两组样本和完整计量。操作不会改写旧会话，也不会自动扩量。"}</AlertDialogDescription>
      {operation === "expand" && <FormField htmlFor="rollout-expand-bps" label="新的候选万分比" required error={error} hint={`当前${rollout.candidate_bps}，本次最高${maximum}；100为1%。`}>
        <Input id="rollout-expand-bps" type="number" step={1} value={bps} placeholder="请输入新的候选万分比" disabled={busy} aria-invalid={Boolean(error)} aria-describedby={error ? "rollout-expand-bps-error" : undefined}
          onChange={(event) => { setBps(event.target.value); setError(validate(event.target.value)); }} />
      </FormField>}
    </AlertDialogBody><AlertDialogFooter><AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
      <Button variant={operation === "stop" ? "destructive" : "default"} onClick={() => void confirm()} disabled={busy}>{busy ? "正在提交…" : `确认${rolloutCommandLabels[operation]}`}</Button>
    </AlertDialogFooter></AlertDialogContent></AlertDialog>;
}
