import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button, buttonVariants } from "@/components/ui/button";
import type { KnowledgeIngestionComparison, KnowledgeIngestionDraft, PaginatedListResponse } from "@/types";
import { ingestionTime } from "../lib/presentation";

const unavailableLabels: Record<Exclude<KnowledgeIngestionComparison["status"], "available">, string> = {
  content_unavailable: "当前没有有效发布版本或当前知识 ACL 未授予正文权限。仅凭管理员和本人任务身份不能查看正文。",
  target_unavailable: "关联文档或版本当前不可用，请在知识治理中独立复核。",
  newer_candidate: "已有更新候选或原候选已被取代，请复核最新版本，不能复活旧候选。",
  candidate_withdrawn: "本次候选已撤回，保留导入与审核历史，当前正式版本和索引未因撤回而改变。",
  scope_changed: "当前负责人或知识权限与导入映射不同，需重新复核范围。",
  integrity_failed: "任务回执、目标绑定或内容摘要无法确认，已隐藏内容。",
  too_large: "内容超过完整比较预算（两侧共 64 KiB、每侧 4000 行），没有返回部分差异。",
};

export function IngestionDrafts({ result, page, loading, error, busy, onReload, onPage, onSelect,
  selectedId, comparison, comparisonLoading, comparisonError, onReloadComparison, onGovernance, governance,
  onProvenance, provenance }: {
  result: PaginatedListResponse<KnowledgeIngestionDraft> | null;
  page: { current: number; pageSize: number }; loading: boolean; error: string | null; busy: boolean;
  onReload: () => void; onPage: (current: number, pageSize: number) => void; onSelect: (id: string | null) => void;
  selectedId: string | null; comparison: KnowledgeIngestionComparison | null;
  comparisonLoading: boolean; comparisonError: string | null; onReloadComparison: () => void;
  onGovernance: (id: string) => void; governance: ReactNode;
  onProvenance: (id: string) => void; provenance: ReactNode;
}) {
  const columns: AppTableColumn<KnowledgeIngestionDraft>[] = [
    { title: "物化记录", key: "id", render: (_, row) => <span className="break-all text-xs">{row.id}</span> },
    { title: "首次创建", key: "created", render: (_, row) => ingestionTime(row.created_at) },
    { title: "操作", key: "actions", align: "right", render: (_, row) => <div className="flex flex-wrap justify-end gap-2">
      <Button size="sm" variant="outline" disabled={busy || loading || comparisonLoading} onClick={() => onSelect(row.id)}>查看差异</Button>
      <Button size="sm" variant="outline" disabled={busy || loading} onClick={() => onProvenance(row.id)}>来源追溯</Button>
      <Button size="sm" variant="outline" disabled={busy || loading} onClick={() => onGovernance(row.id)}>负责人和复核</Button></div> },
  ];
  return <section className="space-y-3">
    <ListToolbar title="本任务首次创建的草稿" loading={loading || busy} onRefresh={onReload} />
    <p className="text-xs leading-5 text-muted-foreground">列表仅记录本任务首次创建的草稿；原版本复用保留在任务计数中，不在此重复列入。查看差异会重新校验当前知识权限。</p>
    {error && <div role="alert" className="space-y-2 text-sm text-red-600"><p>{error}</p>
      <Button size="sm" variant="outline" disabled={loading || busy} onClick={onReload}>重试读取草稿</Button></div>}
    <div className="relative min-h-36"><AppTable columns={columns} dataSource={result?.items ?? []} rowKey="id"
      ariaLabel="本任务首次创建的知识草稿" emptyText={error ? "草稿记录暂时无法读取" : "本任务没有首次创建的草稿"}
      pagination={{ ...page, total: result?.total ?? 0, onChange: onPage }} />
      {loading && <ListLoadingOverlay label="正在读取草稿记录…" />}</div>
    {governance}
    {provenance}
    {selectedId && <section className="relative min-h-36 space-y-4 rounded-2xl bg-muted/40 p-4" aria-label="导入候选差异">
      <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-semibold">与当前发布版本比较</h3>
        <div className="flex gap-2"><Button size="sm" variant="outline" disabled={busy || comparisonLoading} onClick={onReloadComparison}>重新比较</Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onSelect(null)}>收起差异</Button></div></div>
      {comparisonError && <p role="alert" className="text-sm text-red-600">{comparisonError}</p>}
      {comparison && <ComparisonContent comparison={comparison} />}
      {comparisonLoading && <ListLoadingOverlay label="正在校验权限并比较草稿…" />}
    </section>}
  </section>;
}

function ComparisonContent({ comparison }: { comparison: KnowledgeIngestionComparison }) {
  if (comparison.schema_version !== "knowledge_ingestion_comparison_v1" || comparison.automatic_publish_enabled !== false) {
    return <p role="alert" className="text-sm text-red-600">比较契约不可确认，已隐藏正文，请刷新。</p>;
  }
  if (comparison.status !== "available") {
    return <p role="status" className="text-sm text-muted-foreground">{unavailableLabels[comparison.status] ?? "未知比较状态，已隐藏正文，请刷新。"}</p>;
  }
  const { candidate, baseline, change } = comparison;
  if (!candidate || !baseline || !change || !comparison.document_id
    || !["draft", "in_review", "published"].includes(comparison.candidate_status ?? "")) {
    return <p role="alert" className="text-sm text-red-600">比较回执不完整，已隐藏正文，请重新比较。</p>;
  }
  const stateLabel = comparison.candidate_status === "published" ? "当前已发布版本"
    : comparison.candidate_status === "in_review" ? "审核中" : "草稿待送审";
  const decisionLabel = comparison.review_decision === "approved" ? "已记录批准，发布时仍需复检"
    : comparison.review_decision === "rejected" ? "已拒绝，需重新复核"
      : comparison.review_decision === "pending" ? "待独立审核" : "尚未记录审核决定";
  return <>
    <StatusBadge value={comparison.candidate_status === "published" ? "published" : "pending"} label={stateLabel} />
    <dl className="space-y-2 text-sm">
      <div><dt className="text-muted-foreground">当前发布版 → 导入候选</dt><dd className="break-words">v{baseline.version_number} {baseline.title} → v{candidate.version_number} {candidate.title}</dd></div>
      <div><dt className="text-muted-foreground">当前负责人 ID</dt><dd className="break-all">{comparison.owner_user_id ?? "未提供"}</dd></div>
      <div><dt className="text-muted-foreground">复核时间</dt><dd>{comparison.review_due_at ? ingestionTime(comparison.review_due_at) : "尚未配置"}</dd></div>
      <div><dt className="text-muted-foreground">审核事实</dt><dd>{decisionLabel}</dd></div>
    </dl>
    <p className="text-xs leading-5 text-muted-foreground">仅比较字面变化；共同开头 {change.common_prefix_lines} 行、结尾 {change.common_suffix_lines} 行已省略，中间区域完整展示。该区间可能包含相同的中间行，不代表最小差异或语义冲突结论。{change.title_changed ? "标题已变化。" : "标题未变化。"}</p>
    {!change.content_changed ? <p role="status" className="text-sm">正文与当前发布版本相同。</p> : <div className="grid min-w-0 gap-4">
      <div className="min-w-0"><h4 className="mb-2 text-sm font-medium">当前发布版替换区间</h4><pre className="whitespace-pre-wrap break-words rounded-lg bg-white p-3 text-xs">{change.removed_text || "（空区间）"}</pre></div>
      <div className="min-w-0"><h4 className="mb-2 text-sm font-medium">导入候选替换区间</h4><pre className="whitespace-pre-wrap break-words rounded-lg bg-white p-3 text-xs">{change.added_text || "（空区间）"}</pre></div>
    </div>}
    <p className="text-xs text-muted-foreground">比较基线为本次读取时的当前发布版。比较结果不授予批准或发布许可，继续沿用独立审核与发布流程。</p>
    <Link className={buttonVariants({ variant: "outline" })} to={`/knowledge/${comparison.document_id}`}>前往知识治理详情</Link>
  </>;
}
