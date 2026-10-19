import { useCallback, useEffect, useRef, useState } from "react";

import { cancelToolConnectionProbe, controlToolConnection, getToolConnection, getToolConnectionProbe } from "@/api";
import { AlertDialog, AlertDialogAction, AlertDialogBody, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/AppDialog";
import { GlobalLoading } from "@/components/GlobalLoading";
import { StatusBadge } from "@/components/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import type { ToolConnectionProbe, ToolConnectionView, ToolDiscoveryView } from "@/types";
import { requireConnection, requireProbe } from "../contracts";

const reasons: Record<string, string> = {
  MOCK_PROBE_DISABLED: "当前环境未启用隔离连接探测。", SOURCE_NOT_READY: "需要当前有效批准与已验证目录。",
  SOURCE_IMPLEMENTATION_UNAVAILABLE: "来源实现完整性无法确认。", SOURCE_IMPLEMENTATION_MISMATCH: "来源批准与当前隔离Mock实现不一致。",
  RECOVERY_COOLDOWN: "恢复冷却期尚未结束，请稍后刷新。", PROBE_IN_PROGRESS: "已有未结束探测，请查询或取消原任务。",
};
const circuitLabels = { closed: "未熔断", open: "已熔断", half_open: "恢复探测中" };
const healthLabels = { unknown: "尚无有效观察", healthy: "隔离探测通过", unhealthy: "隔离探测失败", stale: "观察已过期" };

export function SourceConnectionPanel({ source, onBusyChange, onGateChange }: {
  source: ToolDiscoveryView; onBusyChange: (busy: boolean) => void; onGateChange: (open: boolean) => void;
}) {
  const token = getStoredAccessToken();
  const [view, setView] = useState<ToolConnectionView | null>(null);
  const [job, setJob] = useState<ToolConnectionProbe | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false), [clock, setClock] = useState(Date.now());
  const [pending, setPending] = useState<{ action: "open" | "probes" | "cancel"; view: ToolConnectionView; job: ToolConnectionProbe | null } | null>(null);
  const sequence = useRef(0), live = useRef(true), lock = useRef(false);
  const current = useCallback(() => live.current && token !== null && getStoredAccessToken() === token, [token]);
  const request = useCallback(async <T,>(operation: (value: string) => Promise<T>) => {
    const result = await withAuthenticatedToken((value) => {
      if (!current() || value !== token) throw new Error("登录会话已变化，请重新打开来源。");
      return operation(value);
    });
    return result.value;
  }, [current, token]);

  const load = useCallback(async (acknowledge = false) => {
    const id = ++sequence.current;
    setLoading(true); setView(null); setJob(null); setError(null); onGateChange(false);
    try {
      const result = requireConnection(await request((value) => getToolConnection(value, source.source_key)), source);
      if (!current() || id !== sequence.current) return;
      const probe = result.latest_probe_id ? requireProbe(await request((value) => getToolConnectionProbe(value, source.source_key, result.latest_probe_id!)), source.source_key) : null;
      if (probe && probe.id !== result.latest_probe_id) throw new Error("原探测任务无法确认，请刷新连接状态。");
      if (!current() || id !== sequence.current) return;
      setView(result); setJob(probe); setClock(Date.now()); onGateChange(result.circuit_state === "closed");
      if (acknowledge) { setUnconfirmed(false); notify.warning("已读取当前连接事实；后续操作需要重新确认。"); }
    } catch (cause) {
      if (current() && id === sequence.current) setError(cause instanceof Error ? cause.message : "连接状态读取失败");
    } finally { if (current() && id === sequence.current) setLoading(false); }
  }, [current, onGateChange, request, source]);

  useEffect(() => { live.current = true; void load(); return () => { live.current = false; sequence.current += 1; }; }, [load]);
  useEffect(() => {
    if (!view?.valid_until) return;
    const timer = window.setTimeout(() => setClock(Date.now()), Math.min(2_147_483_647, Math.max(0, Date.parse(view.valid_until)-Date.now()+1)));
    return () => window.clearTimeout(timer);
  }, [view]);

  async function confirm() {
    if (!pending || lock.current || !current()) return;
    const target = pending;
    lock.current = true; setBusy(true); onBusyChange(true); onGateChange(false);
    try {
      if (target.action === "open") {
        const result = requireConnection(await request((value) => controlToolConnection(value, source.source_key, target.view.revision, target.view.source_record_id, "open")), source);
        if (result.revision !== target.view.revision+1 || result.circuit_state !== "open") throw new Error("熔断回执无法确认，请查询状态。");
      } else if (target.action === "probes") {
        const result = requireProbe(await request((value) => controlToolConnection(value, source.source_key, target.view.revision, target.view.source_record_id, "probes")), source.source_key);
        if (result.source_record_id !== source.record_id || result.status !== "queued") throw new Error("探测提交回执无法确认，请查询状态。");
      } else if (target.job) {
        const jobId = target.job.id, revision = target.job.revision;
        const result = requireProbe(await request((value) => cancelToolConnectionProbe(value, source.source_key, jobId, revision)), source.source_key);
        if (result.id !== jobId || result.status !== "cancelled") throw new Error("取消回执无法确认，请查询原任务。");
      } else { throw new Error("原任务未定位，请刷新查询。"); }
      if (current()) notify.success(target.action === "open" ? "人工熔断已记录。" : target.action === "probes" ? "探测已排队，尚未确认通过。" : "取消已记录，迟到结果不能恢复连接。");
    } catch (cause) {
      if (current()) { setUnconfirmed(true); notify.error(cause, "结果尚未确认，仅刷新查询，不自动重发。"); }
    } finally {
      lock.current = false;
      if (current()) { setBusy(false); onBusyChange(false); setPending(null); await load(); }
    }
  }

  const health = view?.health_state === "healthy" && view.valid_until && Date.parse(view.valid_until) <= clock ? "stale" : view?.health_state;
  return <section className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-medium">隔离连接探测与熔断</h3>
      <Button size="sm" variant="outline" disabled={busy || loading} onClick={() => void load(unconfirmed)}>刷新连接状态</Button></div>
    <p className="text-sm text-muted-foreground">当前仅支持已批准的隔离Mock。通过不代表真实业务服务可用，也不授予业务执行权限。</p>
    {loading && <GlobalLoading fullScreen={false} size="sm" label="正在读取连接状态…" />}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {unconfirmed && <p role="status" className="text-sm text-amber-700">上次操作尚未确认，写入已暂停。请刷新查询当前事实，不会重发。</p>}
    {view && !loading && <>
      <div className="flex flex-wrap gap-2"><StatusBadge value={view.circuit_state === "open" ? "failed" : view.circuit_state === "half_open" ? "pending" : "unknown"} label={circuitLabels[view.circuit_state]} />
        <StatusBadge value={health === "healthy" ? "passed" : health === "unhealthy" ? "failed" : "unknown"} label={healthLabels[health ?? "unknown"]} /></div>
      <p className="text-sm text-muted-foreground">连接修订 {view.revision} · 连续失败 {view.failure_count}/3{view.valid_until ? ` · 观察有效至 ${new Date(view.valid_until).toLocaleString()}` : ""}</p>
      {view.unavailable_reason && <p className="text-sm text-muted-foreground">{reasons[view.unavailable_reason] ?? "当前探测条件未满足，请核对来源或配置。"}</p>}
      {job && <p className="break-all text-sm">最近任务：{job.id} · {job.status}{job.error_code ? ` · ${job.error_code}` : ""}{job.source_record_id !== source.record_id ? "（旧来源版本）" : ""}</p>}
      <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy || unconfirmed || !view.probe_available}
        onClick={() => setPending({ action: "probes", view, job })}>{view.circuit_state === "closed" ? "检查隔离连接" : "请求恢复探测"}</Button>
        <Button size="sm" variant="destructive" disabled={busy || unconfirmed || view.circuit_state === "open"} onClick={() => setPending({ action: "open", view, job })}>人工熔断</Button>
        {job && ["queued", "running"].includes(job.status) && <Button size="sm" variant="outline" disabled={busy || unconfirmed} onClick={() => setPending({ action: "cancel", view, job })}>取消原探测</Button>}
      </div>
    </>}
    <AlertDialog open={pending !== null} onOpenChange={(open) => { if (!open && !lock.current) setPending(null); }}><AlertDialogContent>
      <AlertDialogHeader><AlertDialogTitle>{pending?.action === "open" ? "确认人工熔断" : pending?.action === "cancel" ? "确认取消探测" : "确认隔离连接探测"}</AlertDialogTitle><AlertDialogDescription>{source.source_key}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogBody><p className="text-sm text-muted-foreground">{pending?.action === "open" ? "本来源的固定定义解析将被阻止，在途探测失效；恢复需要冷却后重新探测通过。" : pending?.action === "cancel" ? "取消后迟到结果不能恢复连接；不能据此断言隔离请求此前未发生。" : "创建一次只读隔离探测任务，不重跑Agent或业务工具。排队与通过分开，失败不会自动重试。"}</p></AlertDialogBody>
      <AlertDialogFooter><AlertDialogCancel disabled={busy}>取消</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={(event) => { event.preventDefault(); void confirm(); }}>{busy ? "处理中…" : "确认"}</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>
  </section>;
}
