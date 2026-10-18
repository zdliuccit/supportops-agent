import type { ReactNode } from "react";
import { AppDrawer } from "@/components/AppDrawer";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import type { KnowledgeIngestionJob } from "@/types";
import { ingestionContractSupported, ingestionFailureLabel, ingestionStatusLabel, ingestionTime, type IngestionCommand } from "../lib/presentation";

export function IngestionJobDrawer({ open, job, loading, error, busy, drafts, executeAvailable, onClose, onReload, onCommand, onLookupRequest }: {
  open: boolean; job: KnowledgeIngestionJob | null; loading: boolean; error: string | null; busy: boolean;
  onClose: () => void; onReload: () => void; onCommand: (job: KnowledgeIngestionJob, kind: IngestionCommand["kind"]) => void;
  executeAvailable: boolean;
  onLookupRequest: (requestKey: string) => void;
  drafts: ReactNode;
}) {
  const supported = job ? ingestionContractSupported(job) : false;
  const rows = job ? [
    ["任务ID", job.id], ["知识来源", job.source_name ?? job.source_id ?? "—"],
    ["创建请求标识", job.creation_request_key ?? "旧记录未提供可查询标识"],
    ["创建时间", ingestionTime(job.created_at)], ["固定截止时间", ingestionTime(job.deadline_at)],
    ["实际尝试 / 上限", `${job.attempts ?? "—"} / ${job.max_attempts ?? "—"}`],
    ["已提交页 / 上限", `${job.committed_pages ?? "—"} / ${job.max_pages ?? "—"}`],
    ["单页对象上限", String(job.max_objects_per_page ?? "—")],
    ["当前执行租约截止", ingestionTime(job.lease_expires_at)], ["可重试时间", ingestionTime(job.retry_at)],
    ["任务修订", String(job.revision ?? "—")], ["失败原因", ingestionFailureLabel(job.failure_code)],
  ] : [];
  return <AppDrawer open={open} onOpenChange={(next) => { if (!next && !busy) onClose(); }} title="导入任务详情"
    description="查看本人的导入处理事实与固定预算。处理完成后，新知识仍需独立审核和发布。"
    showConfirm={false} cancelText="关闭" loading={busy}>
    <div className="relative min-h-48 space-y-5">
      {error && <div role="alert" className="space-y-3 text-sm text-red-600"><p>{error}</p>
        <Button variant="outline" size="sm" disabled={loading || busy} onClick={onReload}>重新读取详情</Button></div>}
      {job && <>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <StatusBadge value={supported ? job.status === "retry_wait" ? "pending" : job.status : "unknown"} label={ingestionStatusLabel(job)} />
          <Button size="sm" variant="outline" disabled={loading || busy} onClick={onReload}>刷新详情</Button>
        </div>
        {!supported ? <p role="alert" className="text-sm text-red-600">任务完整性或契约版本不可确认，已隐藏执行信息并禁用操作。请刷新或联系维护人员。</p>
          : <>
            <dl className="space-y-4 text-sm">{rows.map(([label, value]) => <div key={label} className="grid gap-1 sm:grid-cols-[9rem_1fr] sm:gap-3">
              <dt className="text-muted-foreground">{label}</dt><dd className="min-w-0 break-all">{value}</dd>
            </div>)}</dl>
            <section className="rounded-2xl bg-muted/40 p-4">
              <h2 className="mb-3 text-sm font-semibold">已提交对象</h2>
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-muted-foreground">新草稿</dt><dd>{job.counts?.draft_created ?? "—"}</dd></div>
                <div><dt className="text-muted-foreground">原版本复用</dt><dd>{job.counts?.unchanged ?? "—"}</dd></div>
                <div><dt className="text-muted-foreground">来源移除观察</dt><dd>{job.counts?.source_removed ?? "—"}</dd></div>
                <div><dt className="text-muted-foreground">明确拒绝</dt><dd>{job.counts?.rejected ?? "—"}</dd></div>
                <div><dt className="text-muted-foreground">隔离待复核</dt><dd>{job.counts?.quarantined ?? "—"}</dd></div>
              </dl>
            </section>
            <p className="text-xs leading-5 text-muted-foreground">只显示已提交计数。租约过期或到达可重试时间不会在读取时改变任务状态。取消和记录失败都保留已有草稿、回执与审计。</p>
            {drafts}
            <div className="flex flex-wrap gap-3">
              <Button variant="outline" disabled={busy || loading || !job.creation_request_key}
                title={job.creation_request_key ? undefined : "旧记录未提供规范UUID创建请求标识"}
                onClick={() => { if (job.creation_request_key) onLookupRequest(job.creation_request_key); }}>核对创建记录</Button>
              <Button disabled={busy || loading || !executeAvailable} title={executeAvailable ? undefined : "需要与任务一致的当前来源批准、排队状态和有效执行预算；处理中或未知结果仅查询"}
                onClick={() => onCommand(job, "execute")}>执行一次</Button>
              <Button variant="outline" disabled={busy || loading || job.cancel_available !== true}
                title={job.cancel_available ? undefined : "终态任务不能取消"} onClick={() => onCommand(job, "cancel")}>取消任务</Button>
              <Button variant="outline" disabled={busy || loading || job.expire_available !== true}
                title={job.expire_available ? undefined : "尚未到期或尚未耗尽执行次数"} onClick={() => onCommand(job, "expire")}>记录超时失败</Button>
            </div>
          </>}
      </>}
      {loading && <ListLoadingOverlay label="正在读取导入任务详情…" />}
    </div>
  </AppDrawer>;
}
