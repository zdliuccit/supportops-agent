import { useCallback, useState } from "react";
import { amendBusinessAction, listActionApprovers } from "@/api";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { SearchableSelect } from "@/components/SearchableSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import type { BusinessActionDetail } from "@/types";

/** 仅允许未取得执行许可的提案改参；改参必定提交新的独立审批。 */
export function ActionAmendDialog({ action, runId, onReplaced }: { action: BusinessActionDetail; runId: string; onReplaced: (action: BusinessActionDetail) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [approverId, setApproverId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const options = useCallback(async (keywords: string, pageSize: number) => {
    const response = await withRefreshedToken((token) => listActionApprovers(token, action.id, runId, keywords, pageSize));
    return response.value.items.map((user) => ({ value: user.user_id, label: user.display_name }));
  }, [action.id, runId]);
  async function save() {
    setBusy(true); setError(null);
    try {
      const response = await withRefreshedToken((token) => amendBusinessAction(token, action, runId, approverId, title.trim(), summary.trim()));
      notify.success("原提案已保留并失效；修改后的动作需重新独立审批。"); setOpen(false); onReplaced(response.value);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "当前无法修改提案，请刷新状态"); }
    finally { setBusy(false); }
  }
  if (action.generation !== 0 || !["proposed", "waiting_approval", "queued", "cancelled"].includes(action.status)) return null;
  return <><Button variant="outline" onClick={() => { setTitle(action.parameters.title); setSummary(action.parameters.summary); setApproverId(""); setError(null); setOpen(true); }}>修改提案并重新审批</Button>
    <Dialog open={open} onOpenChange={(value) => { if (!busy) setOpen(value); }}><DialogContent>
      <DialogHeader><DialogTitle>修改未执行提案</DialogTitle><DialogDescription>只修改标题和受限摘要，保留原业务资源。原批准失效，新提案需要指定非本人重新审批。</DialogDescription></DialogHeader>
      <DialogBody className="space-y-4"><div className="space-y-2"><Label htmlFor={`amend-title-${action.id}`}>标题</Label>
        <Input id={`amend-title-${action.id}`} value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} disabled={busy} /></div>
        <div className="space-y-2"><Label htmlFor={`amend-summary-${action.id}`}>摘要</Label><Textarea id={`amend-summary-${action.id}`} value={summary} maxLength={2000} onChange={(event) => setSummary(event.target.value)} disabled={busy} placeholder="只填写最小支持摘要，不粘贴日志、密钥或完整 Prompt。" /></div>
        <div className="space-y-2"><p className="text-sm font-medium">指定重新审批人员</p><SearchableSelect value={approverId} onValueChange={setApproverId} loadOptions={options} disabled={busy}
          placeholder="选择本项目获准非本人审批者" emptyText="暂无有效的显式项目审批人" /></div>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      </DialogBody><DialogFooter><Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>取消</Button>
        <Button disabled={busy || !title.trim() || !summary.trim() || !approverId || title.trim() === action.parameters.title && summary.trim() === action.parameters.summary}
          onClick={() => void save()}>{busy ? "正在提交…" : "保存新提案并送审"}</Button></DialogFooter>
    </DialogContent></Dialog>
  </>;
}
