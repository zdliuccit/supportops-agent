import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import type { KnowledgeIndexJob } from "@/types";
import type { useKnowledgeIndex } from "./useKnowledgeIndex";

/** 索引事实与就绪状态由服务端提供，提交任务不等同构建成功。 */
export function KnowledgeIndexPanel({ index, canRebuild }: { index: ReturnType<typeof useKnowledgeIndex>; canRebuild: boolean }) {
  const columns: AppTableColumn<KnowledgeIndexJob>[] = [
    { title: "事件", dataIndex: "event_type", key: "event_type" },
    { title: "消费次数", dataIndex: "attempts", key: "attempts", align: "center" },
    { title: "状态", key: "status", render: (_, job) => job.processed_at
      ? <StatusBadge value="completed" label="已消费" /> : job.dead_at
        ? <StatusBadge value="failed" label="死信" /> : <StatusBadge value="queued" label="待消费" /> },
    { title: "错误", key: "error", render: (_, job) => <span className="break-all text-xs">{job.error_code ?? "—"}</span> },
    { title: "操作", key: "action", align: "right", render: (_, job) => job.retry_available
      ? <Button size="sm" variant="outline" disabled={index.busy || index.loading} onClick={() => void index.retry(job)}>恢复任务</Button>
      : job.dead_at ? "已有恢复任务，旧记录保留" : "—" },
  ];
  const generation = index.state?.generation;
  return <section className="space-y-3 rounded-2xl bg-white p-5" aria-label="知识关键词索引">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 className="font-semibold">关键词索引</h2><p className="mt-1 text-xs text-muted-foreground">关键词检索；向量检索暂未启用。构建状态不代替当前权限和有效期检查。</p></div>
      <Button variant="outline" disabled={!canRebuild || index.busy || index.loading} onClick={() => void index.rebuild()}
        title={!canRebuild ? "需要当前发布版本才能重建" : undefined}>{index.busy ? "处理中…" : "重建索引"}</Button>
    </div>
    {index.error && <div role="alert" className="text-sm text-red-600">{index.error}</div>}
    {index.state && <div className="flex flex-wrap items-center gap-3 text-sm">
      {generation ? <><StatusBadge value={generation.status === "ready" ? "completed" : generation.status === "invalidated" ? "expired" : generation.status}
        label={generation.status === "ready" ? "已构建" : generation.status === "invalidated" ? "已失效" : generation.status} />
        <span>代次 {generation.generation_number} · {generation.chunk_count} 个分块</span></> : <span>尚未构建索引</span>}
      <span>待消费 {index.state.pending_jobs} · 历史死信 {index.state.dead_jobs}</span>
    </div>}
    <ListToolbar title="索引任务列表" onRefresh={() => void index.load()} loading={index.loading || index.busy} />
    <div className="relative min-h-[200px]">
      <AppTable columns={columns} dataSource={index.jobs} rowKey="id" ariaLabel="知识索引任务" emptyText="暂无索引任务"
        pagination={{ ...index.pagination, total: index.total, onChange: index.changePage }} />
      {index.loading && <ListLoadingOverlay label="正在读取关键词索引状态…" />}
    </div>
  </section>;
}
