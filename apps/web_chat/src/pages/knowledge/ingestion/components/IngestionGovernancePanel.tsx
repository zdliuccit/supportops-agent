import { Link } from "react-router-dom";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { StatusBadge } from "@/components/StatusBadge";
import { Button, buttonVariants } from "@/components/ui/button";
import type { KnowledgeIngestionGovernance } from "@/types";
import { editableGovernance, withdrawalAvailable } from "../lib/governance";
import { ingestionTime } from "../lib/presentation";

export function IngestionGovernancePanel({ view, loading, error, busy, onReload, onClose, onEdit, onWithdraw }: {
  view: KnowledgeIngestionGovernance | null; loading: boolean; error: string | null; busy: boolean;
  onReload: () => void; onClose: () => void; onEdit: () => void;
  onWithdraw: () => void;
}) {
  const available = editableGovernance(view);
  return <section className="relative min-h-36 space-y-4 rounded-2xl bg-muted/40 p-4" aria-label="导入候选复核安排">
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-semibold">负责人和复核安排</h3>
      <div className="flex gap-2"><Button size="sm" variant="outline" disabled={busy || loading} onClick={onReload}>刷新复核信息</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onClose}>收起复核安排</Button></div></div>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {view && (available ? <>
      <StatusBadge value={view.review_timing === "due" ? "expired" : view.review_timing === "scheduled" ? "pending" : "unknown"}
        label={view.review_timing === "due" ? "已到复核时间" : view.review_timing === "scheduled" ? "已安排复核" : "尚未安排日期"} />
      {view.review_timing === "due" && <p role="status" className="text-sm">复核日期已到，请由负责人检查候选和当前知识状态，再按独立流程处理。</p>}
      {!view.owner_active && <p role="alert" className="text-sm text-red-600">当前负责人已停用或不可用，请重新分配有效用户。</p>}
      <dl className="space-y-2 text-sm">
        <div><dt className="text-muted-foreground">当前负责人</dt><dd className="break-all">{view.owner_display_name ?? "名称不可用"} · {view.owner_user_id}</dd></div>
        <div><dt className="text-muted-foreground">复核日期</dt><dd>{view.review_due_at ? ingestionTime(view.review_due_at) : "尚未配置"}</dd></div>
        <div><dt className="text-muted-foreground">候选状态</dt><dd>{view.candidate_status === "draft" ? "草稿待送审" : view.candidate_status === "in_review" ? "审核中" : "当前已发布"}</dd></div>
      </dl>
      <p className="text-xs leading-5 text-muted-foreground">提示以服务端 {ingestionTime(view.observed_at)} 的观察为准，刷新可更新。安排负责人和日期不会授予正文权限，不发送外部消息，不自动批准、发布或修改索引。</p>
      <div className="flex flex-wrap gap-3"><Button size="sm" disabled={busy || loading} onClick={onEdit}>设置负责人和日期</Button>
        {busy ? <Button size="sm" variant="outline" disabled>前往知识治理</Button> : <Link className={buttonVariants({ size: "sm", variant: "outline" })} to={`/knowledge/${view.document_id}`}>前往知识治理</Link>}
        <Button size="sm" variant="destructive" disabled={busy || loading || !withdrawalAvailable(view)}
          title={withdrawalAvailable(view) ? undefined : "只允许本人创建、尚未发布且仍为最新的候选"} onClick={onWithdraw}>撤回本次候选</Button></div>
    </> : <p role={view.schema_version === "knowledge_ingestion_governance_v1" && view.status === "candidate_withdrawn" ? "status" : "alert"} className="text-sm text-muted-foreground">{view.schema_version !== "knowledge_ingestion_governance_v1" ? "治理契约不可确认，请刷新。"
      : view.status === "candidate_withdrawn" ? "本次候选已撤回，导入回执与审核历史保留，当前正式版本和索引未因撤回而改变。后续更新须生成新候选并独立审核。"
      : view.status === "newer_candidate" ? "已有更新候选，不能从旧物化记录修改当前复核安排。"
        : view.status === "target_unavailable" ? "目标已不可用，请独立复核文档状态。" : "治理范围或记录完整性无法确认，已禁用修改。"}</p>)}
    {loading && <ListLoadingOverlay label="正在读取复核安排…" />}
  </section>;
}
