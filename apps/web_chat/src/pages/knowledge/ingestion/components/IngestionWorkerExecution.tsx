import { AlertDialog, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import type { KnowledgeIngestionExecutionRequest, KnowledgeIngestionJob } from "@/types";
import { useIngestionWorkerExecution } from "../hooks/useIngestionWorkerExecution";
import { ingestionTime } from "../lib/presentation";

const labels: Record<KnowledgeIngestionExecutionRequest["status"], string> = {
  queued: "意图已提交，待领取", claimed: "意图已领取，需查询任务结果", completed: "已确认原任务完成",
  rejected: "执行意图已拒绝", unconfirmed: "执行结果未确认",
};
const errors: Record<NonNullable<KnowledgeIngestionExecutionRequest["error_code"]>, string> = {
  REQUEST_EXPIRED: "意图已过期，未被Worker领取。",
  AUTHORIZATION_OR_SCOPE_CHANGED: "当前登录、来源批准或权限范围不可确认，已拒绝领取。",
  JOB_CHANGED: "原任务状态、修订或累计尝试已变化，已拒绝领取。",
  EXECUTION_UNCONFIRMED: "Worker未确认本次意图成功，请查询原任务与草稿记录。不要重派或新建任务掩盖未知结果。",
};

export function IngestionWorkerExecution({ job, canSubmit, busy, onBusyChange, onIntentObserved }: {
  job: KnowledgeIngestionJob; canSubmit: boolean; busy: boolean; onBusyChange: (busy: boolean) => void;
  onIntentObserved: (jobId: string) => void;
}) {
  const worker = useIngestionWorkerExecution(job, canSubmit, busy, onBusyChange, onIntentObserved);
  const request = worker.result;
  const disabledReason = worker.uncertain ? "提交结果未确认，只能查询原意图"
    : request ? "原任务已有唯一执行意图，不能再次派发"
      : !canSubmit || job.status !== "queued" || job.attempts !== 0 ? "需要当前获准且从未领取的排队任务"
        : !worker.loaded ? "请先成功查询原执行意图" : "当前任务或登录状态不可确认";
  return <>
    <section className="relative min-h-36 space-y-3 rounded-2xl bg-muted/40 p-4" aria-label="单次Worker导入">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-sm font-semibold">单次Worker执行</h2>
        <Button size="sm" variant="outline" disabled={busy || worker.loading || worker.saving}
          onClick={() => void worker.load()}>查询原执行意图</Button></div>
      <p className="text-xs leading-5 text-muted-foreground">此入口需维护人员单独启用，并准备独立读取和解析进程。每个任务最多提交一份执行意图；提交后不自动重试或发布知识。</p>
      {worker.error && <p role="alert" className="text-sm text-red-600">{worker.error}</p>}
      {worker.uncertain && <p role="status" className="text-sm text-amber-700">提交结果未确认，请只查询原意图；未观察到记录不能证明在途提交尚未保存。</p>}
      {!request && worker.loaded && !worker.uncertain && <p role="status" className="text-sm text-muted-foreground">本次查询未观察到原任务的Worker执行意图。</p>}
      {request && <>
        <StatusBadge value={request.status === "rejected" ? "failed" : request.status === "unconfirmed" ? "unknown"
          : request.status === "claimed" ? "running" : request.status} label={labels[request.status]} />
        <dl className="space-y-2 text-sm">
          <div><dt className="text-muted-foreground">执行意图 ID</dt><dd className="break-all">{request.id}</dd></div>
          <div><dt className="text-muted-foreground">确认的原任务修订</dt><dd>{request.expected_job_revision}</dd></div>
          <div><dt className="text-muted-foreground">固定截止</dt><dd>{ingestionTime(request.expires_at)}</dd></div>
          <div><dt className="text-muted-foreground">领取时间</dt><dd>{ingestionTime(request.claimed_at)}</dd></div>
        </dl>
        {request.error_code && <p role="status" className="text-sm text-muted-foreground">{errors[request.error_code]}</p>}
      </>}
      <Button variant="outline" disabled={busy || worker.saving || !worker.available}
        title={worker.available ? undefined : disabledReason} onClick={worker.begin}>交给Worker执行一次</Button>
      {!worker.available && !worker.loading && <p className="text-xs text-muted-foreground">{disabledReason}。</p>}
      {worker.loading && <ListLoadingOverlay label="正在查询原Worker执行意图…" />}
    </section>
    <AlertDialog open={worker.confirmation !== null} onOpenChange={(open) => { if (!open) worker.close(); }}>
      <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>提交一次Worker执行意图</AlertDialogTitle>
        <AlertDialogDescription>本操作会保存一份不可重复派发的执行意图，由Worker在当前批准范围内读取来源并生成待复核草稿。不会自动审核或发布。</AlertDialogDescription>
      </AlertDialogHeader><AlertDialogBody><p className="break-all text-sm">原任务：{job.id}，确认修订：{worker.confirmation?.revision}。</p>
        <p className="mt-3 text-sm text-muted-foreground">需要已启用人工Worker入口，并已准备一次独立读取和解析进程。领取后取消、崩溃、超时或响应未知都不重新派发，请查询原任务结果。</p>
      </AlertDialogBody><AlertDialogFooter><AlertDialogCancel disabled={worker.saving} onClick={worker.close}>取消</AlertDialogCancel>
        <Button disabled={busy || worker.saving || !worker.available} onClick={() => void worker.confirm()}>
          {worker.saving ? "正在提交…" : "确认提交一次"}</Button></AlertDialogFooter></AlertDialogContent>
    </AlertDialog>
  </>;
}
