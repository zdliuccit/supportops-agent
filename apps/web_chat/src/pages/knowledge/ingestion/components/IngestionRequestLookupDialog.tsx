import { useId } from "react";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { IngestionRequestLookupResult } from "../hooks/useIngestionRequestLookup";
import { ingestionStatusLabel, ingestionTime } from "../lib/presentation";

export function IngestionRequestLookupDialog({ open, value, fieldError, error, loading, result, busy,
  onChange, onClose, onQuery, onOpenJob }: {
  open: boolean; value: string; fieldError: string; error: string | null; loading: boolean;
  result: IngestionRequestLookupResult | null; busy: boolean;
  onChange: (value: string) => void; onClose: () => void; onQuery: () => void; onOpenJob: () => void;
}) {
  const fieldId = useId(), formId = useId();
  const recorded = result?.outcome.status === "recorded";
  return <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
    <DialogContent>
      <DialogHeader><DialogTitle>按原请求标识查询</DialogTitle>
        <DialogDescription>使用创建时保留的请求标识查询本人记录。查询不读取来源或改变任务；任务ID不能替代创建请求标识。</DialogDescription></DialogHeader>
      <DialogBody className="space-y-5">
        <form id={formId} noValidate onSubmit={(event) => { event.preventDefault(); if (!loading && !busy) onQuery(); }}>
          <FormField label="创建请求标识" htmlFor={fieldId} required error={fieldError} hint="请输入创建确认中显示的完整UUID，支持粘贴。">
            <Input id={fieldId} value={value} maxLength={64} placeholder="请输入创建请求标识" autoComplete="off" spellCheck={false} autoFocus
              aria-required="true" aria-invalid={Boolean(fieldError)} aria-describedby={fieldError ? `${fieldId}-error` : undefined}
              onChange={(event) => onChange(event.target.value)} />
          </FormField>
        </form>
        <div className="relative min-h-28" aria-busy={loading}>
          {loading ? <ListLoadingOverlay label="正在查询本人原请求…" /> : error ? <p role="alert" className="text-sm text-red-600">{error}</p>
            : !result ? <p className="text-sm text-muted-foreground">输入原标识后查询。标签页记录丢失、来源未配置或批准撤销时也可核对服务器记录。</p>
              : <div className="space-y-3 text-sm" role="status">
                <StatusBadge value={recorded ? "completed" : result.outcome.status === "stopped" ? "cancelled" : "unknown"}
                  label={recorded ? "原任务已生成" : result.outcome.status === "stopped" ? "原请求已封锁" : "当前尚未观察到"} />
                {result.outcome.status === "recorded" ? <dl className="space-y-2">
                  <div><dt className="text-muted-foreground">原任务</dt><dd className="break-all text-xs">{result.outcome.job.id}</dd></div>
                  <div><dt className="text-muted-foreground">任务状态</dt><dd>{ingestionStatusLabel(result.outcome.job)}</dd></div>
                </dl> : result.outcome.status === "stopped" ? <p>服务器记录的封锁时间：{ingestionTime(result.stoppedAt)}。该请求不会再创建新任务。</p>
                  : <p>本次未观察到不代表在途创建未提交。请保留原标识继续核对，避免重复创建。</p>}
                <p className="text-xs text-muted-foreground">本次观察时间：{ingestionTime(result.observedAt)}</p>
              </div>}
        </div>
        <p className="text-xs leading-5 text-muted-foreground">此查询不会清除或替换本标签页的待确认定位。关闭或修改标识只取消本次只读查询，不会取消导入任务。</p>
      </DialogBody>
      <DialogFooter><Button variant="outline" onClick={onClose}>关闭</Button>
        <Button type="submit" form={formId} variant={recorded ? "outline" : "default"} disabled={loading || busy}>
          {loading ? "正在查询…" : error ? "重新查询" : "查询原请求"}</Button>
        {recorded && <Button disabled={loading || busy} onClick={onOpenJob}>查看原任务</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
