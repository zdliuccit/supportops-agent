import { useEffect, useState } from "react";

import { decideKnowledgeReview } from "@/api";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import type { KnowledgeVersion } from "@/types";

/** 决策明确且绑定当前版本摘要；不将送审自动视作批准。 */
export function KnowledgeReviewDialog({ version, onClose, onSaved }: {
  version: KnowledgeVersion | null; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [decision, setDecision] = useState<"approved" | "rejected">("approved");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setDecision("approved"); setComment(""); setError(null); }, [version?.id]);
  async function save() {
    if (!version) return;
    if (decision === "rejected" && !comment.trim()) { setError("拒绝审核需要填写原因。"); return; }
    setBusy(true); setError(null);
    try {
      await withRefreshedToken((token) => decideKnowledgeReview(token, version.id, {
        decision, expected_review_revision: version.review_revision ?? 0, expected_content_digest: version.content_digest, comment: comment.trim(),
      }));
      notify.success(decision === "approved" ? "审核已批准；仍需单独发布。" : "审核已拒绝，版本已回到草稿。");
      onClose();
      await onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "提交审核决策失败");
    } finally { setBusy(false); }
  }
  return <Dialog open={version !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent>
      <DialogHeader><DialogTitle>审核知识版本 v{version?.version_number}</DialogTitle>
        <DialogDescription>创建人和提交人不能自审，请核对版本内容及文档访问范围。批准后仍需发布。</DialogDescription>
      </DialogHeader>
      <DialogBody className="space-y-4">
        <p className="break-words text-sm font-medium">{version?.title}</p>
        <p className="text-xs text-muted-foreground">创建人：{version?.created_by_name ?? version?.created_by} · 提交人：{version?.submitted_by_name ?? version?.submitted_by ?? "历史记录"}</p>
        <p className="break-all font-mono text-xs text-muted-foreground">内容 SHA-256：{version?.content_digest}</p>
        <div className="space-y-2"><label htmlFor="knowledge-review-decision" className="text-sm font-medium">审核决策</label>
          <Select value={decision} onValueChange={(value) => { if (value === "approved" || value === "rejected") setDecision(value); }} disabled={busy}>
            <SelectTrigger id="knowledge-review-decision"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="approved">批准</SelectItem><SelectItem value="rejected">拒绝并退回草稿</SelectItem></SelectContent>
          </Select>
        </div>
        <div className="space-y-2"><label htmlFor="knowledge-review-comment" className="text-sm font-medium">审核说明{decision === "rejected" ? "（必填）" : "（可选）"}</label>
          <Textarea id="knowledge-review-comment" value={comment} onChange={(event) => setComment(event.target.value)} maxLength={1000}
            disabled={busy} aria-required={decision === "rejected"} placeholder="说明适用范围或需要修改的内容；不要填写密钥或个人敏感信息。" />
        </div>
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      </DialogBody>
      <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
        <Button disabled={busy || (decision === "rejected" && !comment.trim())} onClick={() => void save()}>{busy ? "提交中…" : "提交审核决策"}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
