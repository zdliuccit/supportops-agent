import { Link } from "react-router-dom";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { Button } from "@/components/ui/button";
import type { KnowledgeIngestionProvenance, KnowledgeIngestionProvenanceAnchor } from "@/types";
import { ingestionTime } from "../lib/presentation";
import { provenanceSupported, provenanceUnavailableLabels } from "../lib/provenance";

export function IngestionProvenancePanel({ view, loading, error, busy, onReload, onClose }: {
  view: KnowledgeIngestionProvenance | null; loading: boolean; error: string | null; busy: boolean;
  onReload: () => void; onClose: () => void;
}) {
  return <section className="relative min-h-36 space-y-4 rounded-2xl bg-muted/40 p-4" aria-label="导入来源追溯">
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-semibold">来源追溯与章节</h3>
      <div className="flex gap-2"><Button size="sm" variant="outline" disabled={busy || loading} onClick={onReload}>重新读取</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onClose}>收起来源追溯</Button></div></div>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    {view && <ProvenanceContent view={view} />}
    {loading && <ListLoadingOverlay label="正在校验权限并读取来源追溯…" />}
  </section>;
}

function ProvenanceContent({ view }: { view: KnowledgeIngestionProvenance }) {
  if (!provenanceSupported(view)) return <p role="alert" className="text-sm text-red-600">来源追溯契约无法确认，已隐藏内容。</p>;
  if (view.status !== "available" || view.record === null) return <p role="status" className="text-sm text-muted-foreground">
    {view.status !== "available" ? provenanceUnavailableLabels[view.status] : "来源追溯记录不完整。"}</p>;
  const record = view.record;
  const rows = [
    ["知识来源 ID", record.source_id], ["知识版本", `v${record.version_number} · ${record.knowledge_version_id}`],
    ["首次物化时间", ingestionTime(record.materialized_at)], ["来源对象摘要", record.object_key],
    ["原始内容 SHA-256", record.raw_content_digest], ["规范正文 SHA-256", record.parsed_content_digest],
    ["来源版本摘要", record.source_revision_digest ?? "原回执未记录"], ["任务范围摘要", record.scope_digest],
    ["来源清单契约摘要", record.source_contract_digest], ["固定读取实现摘要", record.adapter_manifest_digest],
    ["固定解析实现摘要", record.parser_manifest_digest],
  ];
  const columns: AppTableColumn<KnowledgeIngestionProvenanceAnchor>[] = [
    { title: "层级", key: "level", width: 70, render: (_, anchor) => `H${anchor.level}` },
    { title: "章节标题", key: "title", render: (_, anchor) => <span className="whitespace-pre-wrap break-words">{anchor.title || "（空标题）"}</span> },
    { title: "锚点", key: "anchor", render: (_, anchor) => <code className="break-all text-xs">{anchor.id}</code> },
  ];
  return <>
    <p className="text-xs leading-5 text-muted-foreground">摘要来自原导入回执，章节来自已保存的候选版本。本次未重新读取或解析来源，记录不代表来源当前内容、批准或发布决定。</p>
    <dl className="space-y-3 text-sm">{rows.map(([label, value]) => <div key={label} className="grid gap-1 sm:grid-cols-[10rem_1fr] sm:gap-3">
      <dt className="text-muted-foreground">{label}</dt><dd className="min-w-0 break-all">{value}</dd></div>)}</dl>
    <AppTable columns={columns} dataSource={record.section_anchors} rowKey="id" ariaLabel="导入候选完整章节锚点"
      emptyText="候选没有章节标题" />
    <Button variant="outline" asChild><Link to={`/knowledge/${record.document_id}`}>前往知识治理详情</Link></Button>
  </>;
}
