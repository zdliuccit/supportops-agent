import { useMemo } from "react";
import { Check, CheckCircle2, CircleAlert, LoaderCircle, RefreshCw, Wifi, Zap } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/AppDialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { ModelEndpoint, ModelTestRun } from "@/types";

import { elapsed } from "./modelTypes";

interface ModelTestDialogProps {
  /** 当前测试弹窗是否打开。 */
  open: boolean;
  /** 弹窗状态变更。 */
  onOpenChange: (open: boolean) => void;
  /** 被测试连接。 */
  endpoint: ModelEndpoint | null;
  /** 当前选择的稳定模型 ID。 */
  modelId: string;
  /** 切换测试模型。 */
  onModelChange: (modelId: string) => void;
  /** 当前测试运行记录。 */
  testRun: ModelTestRun | null;
  /** 开始或重新测试。 */
  onRun: () => void;
  /** 测试是否正在运行。 */
  running: boolean;
  /** 页面操作是否繁忙。 */
  busy: boolean;
}

/** 展示逐模型真实流式测试阶段和诊断摘要。 */
export function ModelTestDialog({
  open,
  onOpenChange,
  endpoint,
  modelId,
  onModelChange,
  testRun,
  onRun,
  running,
  busy,
}: ModelTestDialogProps) {
  const passed = testRun?.status === "passed";
  const failed = testRun?.status === "failed";
  const stages = useMemo(
    () => [
      { id: "request_sent", label: "请求已发出", time: null },
      { id: "response_headers", label: "收到响应头", time: testRun?.response_headers_ms ?? null },
      { id: "first_content", label: "收到首包内容", time: testRun?.first_content_ms ?? null },
      { id: "completed", label: "测试完成", time: testRun?.total_ms ?? null },
    ],
    [testRun],
  );

  return (
    <Dialog open={open} onOpenChange={(value) => !running && onOpenChange(value)}>
      <DialogContent className="sm:max-w-[960px]">
        <DialogHeader className="flex-row items-start gap-3 space-y-0">
          <span className="grid size-12 shrink-0 place-items-center rounded-xl border border-violet-200 bg-violet-50 text-violet-600"><Wifi className="size-6" /></span>
          <div>
            <DialogTitle className="text-2xl">连通性测试</DialogTitle>
            <DialogDescription className="mt-1">对 {endpoint?.name ?? "模型"} 发起一次无业务数据的真实流式推理请求，分阶段记录耗时。</DialogDescription>
          </div>
        </DialogHeader>
        <DialogBody><div className="mt-4 flex items-center justify-between gap-3 rounded-xl border bg-muted/20 p-4">
          <div className="flex items-center gap-2.5">
            <span className="text-sm text-muted-foreground">测试模型</span>
            <Select value={modelId} onValueChange={onModelChange} disabled={running}>
              <SelectTrigger className="w-[220px] bg-white"><SelectValue /></SelectTrigger>
              <SelectContent>{endpoint?.models.map((model) => <SelectItem key={model.id} value={model.id}>{model.display_name || model.upstream_model_id}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <span className={cn("rounded-full border px-3 py-1 text-sm", running ? "border-violet-200 bg-violet-50 text-violet-600" : testRun?.status === "passed" ? "border-emerald-200 bg-emerald-50 text-emerald-600" : testRun?.status === "failed" ? "border-red-200 bg-red-50 text-red-600" : "border-slate-200 bg-white text-muted-foreground")}>
            {running ? "测试中…" : testRun?.status === "passed" ? "测试通过" : testRun?.status === "failed" ? "测试失败" : "等待测试"}
          </span>
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-[270px_minmax(0,1fr)]">
          <div className="space-y-0">
            {stages.map((stage, index) => {
              const reached = Boolean(testRun?.milestones[stage.id]);
              const active = testRun?.stage === stage.id;
              return (
                <div key={stage.id} className="relative flex min-h-16 gap-3">
                  <div className="flex flex-col items-center">
                    <span className={cn("grid size-9 place-items-center rounded-full border-2", reached ? "border-violet-500 bg-violet-50 text-violet-600" : active ? "border-violet-300 text-violet-500" : "border-slate-200 text-slate-300")}>{reached ? <Check className="size-5" /> : active ? <Zap className="size-5" /> : <CheckCircle2 className="size-4" />}</span>
                    {index < stages.length - 1 && <span className={cn("w-px flex-1", reached ? "bg-violet-300" : "bg-slate-200")} />}
                  </div>
                  <div className="flex flex-1 items-start justify-between pt-2 text-sm"><span className={cn(reached || active ? "font-medium text-[#1c252e]" : "text-muted-foreground")}>{stage.label}</span>{stage.time !== null && <span className="font-mono text-muted-foreground">{elapsed(stage.time)}</span>}</div>
                </div>
              );
            })}
            {testRun?.error_message && <Alert variant="destructive"><CircleAlert /><AlertDescription>{testRun.error_message}</AlertDescription></Alert>}
          </div>
          <div className="min-h-[280px] overflow-hidden rounded-xl border border-[#28282d] bg-[#101012] font-mono text-sm text-[#a9a9b2] shadow-sm">
            <div className="flex items-center justify-between border-b border-[#28282d] bg-[#151518] px-5 py-4">
              <span className="text-[#aaaab4]">&gt; {testRun?.request_host || endpoint?.base_url || "等待请求"}</span>
              {testRun?.provider_status && (
                <span className={cn(
                  "rounded-full border px-4 py-1 text-sm",
                  passed ? "border-emerald-800 bg-emerald-950/60 text-emerald-400" : "border-red-900 bg-red-950/50 text-red-400",
                )}>
                  HTTP {testRun.provider_status}
                </span>
              )}
            </div>
            <div className="space-y-1.5 border-b border-[#28282d] px-5 py-5 text-[#a9a9b2]">
              <div><span className="text-violet-400">POST</span> <span className="text-[#d6d6dc]">{testRun?.request_path || "—"}</span></div>
              <div>Host: {testRun?.request_host || "—"}</div>
              <div>Accept: text/event-stream</div>
              <div>Question: 请回答：1+1等于几？</div>
            </div>
            <div className="border-b border-[#28282d] px-5 py-6">
              <div className="text-[#73737e]">// 模型返回</div>
              <div
                className={cn(
                  "mt-4 max-h-32 overflow-y-auto whitespace-pre-wrap break-words",
                  passed ? "text-emerald-400" : failed ? "text-red-400" : "text-[#c3c3ca]",
                )}
              >
                {testRun?.response_content || (running ? "等待模型返回…" : failed ? "未收到有效响应内容" : "尚未开始")}
              </div>
            </div>
            {(passed || failed) && (
              <div className={cn("flex items-center gap-2 px-5 py-4", passed ? "text-emerald-400" : "text-red-400")}>
                {passed ? <Check className="size-5" /> : <CircleAlert className="size-5" />}
                <span>{passed ? "测试通过" : "测试失败"}</span>
              </div>
            )}
          </div>
        </div>
        {(testRun?.response_headers_ms !== null || testRun?.first_content_ms !== null || testRun?.total_ms !== null) && (
          <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
            <span className="rounded-full border border-slate-200 bg-slate-100 px-4 py-1.5 text-slate-600">响应头&nbsp; {elapsed(testRun?.response_headers_ms ?? null)}</span>
            <span className="rounded-full border border-amber-200 bg-amber-50 px-4 py-1.5 text-amber-600">首包&nbsp; {elapsed(testRun?.first_content_ms ?? null)}</span>
            <span className="rounded-full border border-slate-200 bg-slate-100 px-4 py-1.5 text-slate-600">总耗时&nbsp; {elapsed(testRun?.total_ms ?? null)}</span>
          </div>
        )}
        <div className="mt-3 text-sm text-muted-foreground">首包耗时用于衡量真实对话响应速度；首次测试可能包含建立连接的额外开销。</div>
        <div className="mt-3 text-sm text-muted-foreground">首包耗时用于衡量真实对话响应速度；首次测试可能包含建立连接的额外开销。</div></DialogBody>
        <DialogFooter className="mt-5"><Button variant="outline" onClick={() => onOpenChange(false)} disabled={running}>关闭</Button><Button onClick={onRun} disabled={busy || !endpoint || !modelId}>{running ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}{testRun ? "重新测试" : "开始测试"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
