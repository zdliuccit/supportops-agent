import { useEffect, useRef, useState } from "react";
import { compareAgentReleaseVersions } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { AgentReleaseComparison, AgentVersion } from "@/types";

const labels = { equal: "相同", changed: "变化", added: "新增", removed: "移除", unknown: "历史未知" };

/** 完整两个版本差异的聚合详情，不在客户端请求全部历史版本。 */
export function ReleaseComparisonPanel({ version }: { version: AgentVersion }) {
  const [result, setResult] = useState<AgentReleaseComparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);
  useEffect(() => {
    const current = ++sequence.current;
    setLoading(true); setResult(null); setError(null);
    void delayRequest(() => withAuthenticatedToken((token) => compareAgentReleaseVersions(token, version.agent_id, version.id)))
      .then(({ value }) => { if (current === sequence.current) setResult(value); })
      .catch((cause: unknown) => { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "版本差异读取失败"); })
      .finally(() => { if (current === sequence.current) setLoading(false); });
    return () => { sequence.current += 1; };
  }, [revision, version.agent_id, version.id]);
  const columns: AppTableColumn<AgentReleaseComparison["differences"][number]>[] = [
    { title: "组件", key: "component", render: (_, row) => <span className="break-all text-xs">{row.component}</span> },
    { title: "差异", key: "status", render: (_, row) => <StatusBadge value={row.status === "equal" ? "active" : row.status === "unknown" ? "unknown" : "pending"} label={labels[row.status]} /> },
    { title: "基线", key: "before", render: (_, row) => <span className="block max-w-40 break-all font-mono text-xs" title={row.before ?? undefined}>{row.before ? `${row.before.slice(0, 16)}${row.before.length > 16 ? "…" : ""}` : "未记录或未绑定"}</span> },
    { title: "查看版本", key: "after", render: (_, row) => <span className="block max-w-40 break-all font-mono text-xs" title={row.after ?? undefined}>{row.after ? `${row.after.slice(0, 16)}${row.after.length > 16 ? "…" : ""}` : "未记录或未绑定"}</span> },
  ];
  return <section className="space-y-3">
    <h3 className="text-sm font-medium">与当前活动版本比较</h3>
    <p className="text-xs text-muted-foreground">比较固定版本摘要，不进行评测或批准发布。历史缺记录保持未知；刷新会重新选择读取时的活动基线。</p>
    {error && <div role="alert" className="flex items-center gap-2 text-sm text-red-600"><span>{error}</span>
      <Button size="sm" variant="outline" disabled={loading} onClick={() => setRevision((value) => value + 1)}>重试</Button></div>}
    {result && <p role="status" className="break-all text-xs text-muted-foreground">{result.status === "no_baseline" ? "当前没有活动版本，无法比较。"
      : `基线 ${result.baseline_version_id} · 已知变化 ${result.changed_count} 项，未知 ${result.unknown_count} 项`}</p>}
    {result && (result.version_manifest_status === "integrity_failed" || result.baseline_manifest_status === "integrity_failed")
      && <p role="alert" className="text-sm text-red-600">清单完整性失败，部分组件未知，请联系管理员核对。</p>}
    <ListToolbar title="版本差异列表" loading={loading} onRefresh={() => setRevision((value) => value + 1)} />
    <div className="relative min-h-32"><AppTable columns={columns} dataSource={result?.differences ?? []} rowKey="component"
      emptyText={error ? "差异暂时无法读取" : result?.status === "no_baseline" ? "没有活动基线" : "暂无组件差异"} ariaLabel="发布版本组件差异" />
      {loading && <ListLoadingOverlay label="正在读取版本差异…" />}
    </div>
  </section>;
}
