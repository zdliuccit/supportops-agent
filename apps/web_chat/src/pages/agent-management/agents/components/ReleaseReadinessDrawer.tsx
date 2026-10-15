import { useEffect, useRef, useState } from "react";
import { getAgentReleaseReadiness, listAgentReleaseEvaluations } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { AgentVersion, ReleaseEvaluationSummary, ReleaseGateCheck, ReleaseReadiness } from "@/types";

const categoryLabels = { references: "固定引用", evidence: "评测证据", functionality: "功能", security: "安全", quality: "质量", cost: "费用", latency: "延迟" };
const statusLabels = { passed: "通过", failed: "失败", missing: "缺失", stale: "不再适用" };
function dateLabel(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未记录";
}
function metricLabel(value: number | null, category: ReleaseGateCheck["category"]): string {
  if (value === null) return "未知";
  return category === "quality" ? `${(value * 100).toFixed(2)}%` : category === "latency" ? `${value} ms` : String(value);
}

/** 显式查看门禁后激活；投影不授予权限，服务器提交时再次检查。 */
export function ReleaseReadinessDrawer({ version, activeVersionId, busy, onClose, onActivate }: {
  version: AgentVersion | null;
  activeVersionId: string | null;
  busy: boolean;
  onClose: () => void;
  onActivate: (version: AgentVersion) => void;
}) {
  const [readiness, setReadiness] = useState<ReleaseReadiness | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);
  useEffect(() => {
    const current = ++sequence.current;
    setReadiness(null); setError(null); setLoading(version !== null);
    if (!version) return;
    void delayRequest(() => withAuthenticatedToken((token) => getAgentReleaseReadiness(token, version.agent_id, version.id)))
      .then(({ value }) => { if (current === sequence.current) setReadiness(value); })
      .catch((cause: unknown) => { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "发布门禁读取失败"); })
      .finally(() => { if (current === sequence.current) setLoading(false); });
    return () => { sequence.current += 1; };
  }, [revision, version?.agent_id, version?.id]);
  const columns: AppTableColumn<ReleaseGateCheck>[] = [
    { title: "类别", key: "category", render: (_, row) => categoryLabels[row.category] },
    { title: "检查", key: "message", render: (_, row) => <div className="max-w-80 space-y-1 text-xs"><p>{row.message}</p><p className="break-all font-mono text-muted-foreground">{row.code}</p></div> },
    { title: "状态", key: "status", render: (_, row) => <StatusBadge value={row.status === "stale" ? "expired" : row.status === "missing" ? "unknown" : row.status} label={statusLabels[row.status]} /> },
    { title: "实测 / 阈值", key: "value", render: (_, row) => <span className="text-xs">{metricLabel(row.observed, row.category)} / {metricLabel(row.threshold, row.category)}{row.sample_count !== null && <span className="block text-muted-foreground">{row.sample_count} 个样本</span>}</span> },
  ];
  const isCurrent = version?.id === activeVersionId;
  const currentReadiness = readiness?.version_id === version?.id ? readiness : null;
  const canActivate = version !== null && currentReadiness !== null && currentReadiness.activation_allowed && !loading && !error && !busy && !isCurrent;
  return <AppDrawer open={version !== null} onOpenChange={(open) => { if (!open && !busy) onClose(); }}
    title={`v${version?.version_number ?? ""} 发布门禁`} description="检查当前环境、固定版本与最新评测事实；正式环境缺少适用结果时拒绝激活。"
    showFooter={false} loading={busy} className="sm:max-w-4xl">
    <div className="space-y-5">
      {error && <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-red-600"><span>{error}</span><Button variant="outline" size="sm" onClick={() => setRevision((value) => value + 1)} disabled={loading || busy}>重试</Button></div>}
      {readiness && <div className="space-y-2 text-sm">
        <p role="status" className="flex flex-wrap items-center gap-2"><StatusBadge value={readiness.status === "blocked" ? "failed" : readiness.status === "development_only" ? "pending" : "passed"}
          label={readiness.status === "blocked" ? "拒绝激活" : readiness.status === "development_only" ? "仅开发使用 · 未验收" : "门禁通过"} />{readiness.environment} · {readiness.stage.toUpperCase()}</p>
        {readiness.enforcement === "development_only" && <p className="text-amber-700">当前为开发环境。允许的开发激活不代表业务评测或正式发布通过；已知功能、安全、完整性失败仍会拒绝。</p>}
        <p className="break-all text-xs text-muted-foreground">最新评测：{readiness.evaluation_id ?? "尚无记录"}；完成：{dateLabel(readiness.evaluation_completed_at)}；到期：{dateLabel(readiness.evaluation_expires_at)}</p>
        <p className="break-all font-mono text-xs text-muted-foreground">策略摘要：{readiness.policy_digest}<br />当前范围：{readiness.current_scope_digest ?? "无法核验"}</p>
      </div>}
      <ListToolbar title="发布检查列表" loading={loading} onRefresh={() => setRevision((value) => value + 1)} />
      <div className="relative min-h-40"><AppTable columns={columns} dataSource={readiness?.checks ?? []} rowKey="code" ariaLabel="固定版本发布门禁检查"
        emptyText={error ? "门禁暂时无法读取" : "尚未读取门禁"} />{loading && <ListLoadingOverlay label="正在读取发布门禁…" />}</div>
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">{isCurrent ? "此版本已是当前版本；新会话仍受当前正式门禁检查。" : !readiness || loading ? "读取完整检查后才能激活。" : readiness.activation_allowed ? "激活时服务器会重新检查；既有会话仍固定原版本。" : "当前门禁未满足，请先处理上方失败、缺失或不再适用的项目。"}</p>
        <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" onClick={onClose} disabled={busy}>关闭</Button>
          {!isCurrent && <Button onClick={() => { if (canActivate && version) onActivate(version); }} disabled={!canActivate}>
            {busy ? "正在激活…" : readiness?.status === "development_only" ? "仅开发激活（未验收）" : "显式激活版本"}</Button>}</div>
      </div>
      {version && <EvaluationHistory key={version.id} version={version} />}
    </div>
  </AppDrawer>;
}

function EvaluationHistory({ version }: { version: AgentVersion }) {
  const [items, setItems] = useState<ReleaseEvaluationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);
  useEffect(() => {
    const current = ++sequence.current;
    setLoading(true); setItems([]); setTotal(0); setError(null);
    void delayRequest(() => withAuthenticatedToken((token) => listAgentReleaseEvaluations(token, version.agent_id, version.id, { page, pageSize })))
      .then(({ value }) => { if (current === sequence.current) { setItems(value.items); setTotal(value.total); } })
      .catch((cause: unknown) => { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "评测历史读取失败"); })
      .finally(() => { if (current === sequence.current) setLoading(false); });
    return () => { sequence.current += 1; };
  }, [page, pageSize, revision, version.agent_id, version.id]);
  const columns: AppTableColumn<ReleaseEvaluationSummary>[] = [
    { title: "评测 ID", key: "id", render: (_, row) => <span className="block max-w-52 break-all font-mono text-xs">{row.id}</span> },
    { title: "完成时间", key: "completed_at", render: (_, row) => dateLabel(row.completed_at) },
    { title: "报告摘要", key: "report_digest", render: (_, row) => <span className="font-mono text-xs" title={row.report_digest}>{row.report_digest.slice(0, 16)}…</span> },
  ];
  return <section className="space-y-3"><h3 className="text-sm font-medium">追加式评测历史</h3>
    <p className="text-xs text-muted-foreground">仅展示可信离线执行器追加的元数据。当前评测执行器尚未接通，没有手动上传或填写通过状态的入口；空列表不代表通过。</p>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <ListToolbar title="评测历史列表" loading={loading} onRefresh={() => setRevision((value) => value + 1)} />
    <div className="relative min-h-32"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="固定版本评测历史"
      emptyText={error ? "评测历史暂时无法读取" : "没有评测事实"} pagination={{ current: page, pageSize, total,
        onChange: (nextPage, nextSize) => { setPage(nextSize === pageSize ? nextPage : 1); setPageSize(nextSize); } }} />
      {loading && <ListLoadingOverlay label="正在读取评测历史…" />}</div>
  </section>;
}
