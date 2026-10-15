import { useState } from "react";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/AppDialog";
import type { AgentAuditEvent, AgentVersion } from "@/types";
import { ReleaseManifestDrawer } from "./ReleaseManifestDrawer";
import { ReleaseReadinessDrawer } from "./ReleaseReadinessDrawer";
import { ReleaseRolloutDrawer } from "./ReleaseRolloutDrawer";

type HistoryDialogMode = "versions" | "audit";

interface AgentHistoryDialogProps {
  open: boolean;
  mode: HistoryDialogMode | null;
  versions: AgentVersion[];
  versionTotal: number;
  auditEvents: AgentAuditEvent[];
  auditTotal: number;
  currentPage: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
  activeVersionId: string | null;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onPageChange: (page: number, pageSize: number) => void;
  onActivate: (version: AgentVersion) => void;
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function shortId(value: string | null): string {
  return value ? `${value.slice(0, 8)}…` : "-";
}

/** Agent 历史版本和审计记录的分页表格弹窗。 */
export function AgentHistoryDialog({ open, mode, versions, versionTotal, auditEvents, auditTotal, currentPage, pageSize, loading, error, activeVersionId, busy, onOpenChange, onPageChange, onActivate }: AgentHistoryDialogProps) {
  const [manifestVersion, setManifestVersion] = useState<AgentVersion | null>(null);
  const [readinessVersion, setReadinessVersion] = useState<AgentVersion | null>(null);
  const [rolloutVersion, setRolloutVersion] = useState<AgentVersion | null>(null);
  const versionColumns: AppTableColumn<AgentVersion>[] = [
    {
      title: "版本",
      key: "version",
      width: "10%",
      minWidth: 90,
      render: (_, version) => <div><span className="font-semibold">v{version.version_number}{version.id === activeVersionId ? " · 当前" : ""}</span>
        {version.withdrawn_at && <span className="block text-xs text-red-600">已撤回</span>}</div>,
    },
    {
      title: "版本说明",
      key: "release_notes",
      width: "26%",
      minWidth: 180,
      render: (_, version) => <span className="block max-w-[260px] truncate">{version.release_notes || "未填写"}</span>,
    },
    {
      title: "模型版本",
      key: "model_endpoint_version_id",
      width: "17%",
      minWidth: 160,
      render: (_, version) => <span className="font-mono text-xs">{shortId(version.model_endpoint_version_id)}</span>,
    },
    {
      title: "工具",
      key: "resolved_tool_ids",
      width: "12%",
      minWidth: 90,
      render: (_, version) => <span>{version.resolved_tool_ids.length} 个</span>,
    },
    {
      title: "发布时间",
      key: "published_at",
      width: "20%",
      minWidth: 180,
      render: (_, version) => <span className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(version.published_at)}</span>,
    },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      minWidth: 112,
      align: "right",
      width: "15%",
      render: (_, version) => <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={() => setManifestVersion(version)}>发布清单</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setRolloutVersion(version)}>灰度发布</Button>
        {version.id === activeVersionId ? <span className="self-center text-xs text-emerald-600">当前版本</span>
          : <Button size="sm" variant="outline" onClick={() => setReadinessVersion(version)} disabled={busy}>检查并激活</Button>}
        {version.id === activeVersionId && <Button size="sm" variant="outline" onClick={() => setReadinessVersion(version)} disabled={busy}>发布门禁</Button>}
      </div>,
    },
  ];

  const auditColumns: AppTableColumn<AgentAuditEvent>[] = [
    {
      title: "时间",
      key: "created_at",
      width: "20%",
      minWidth: 180,
      render: (_, event) => <span className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(event.created_at)}</span>,
    },
    {
      title: "操作",
      key: "action",
      width: "17%",
      minWidth: 100,
      render: (_, event) => <span className="font-medium">{event.action}</span>,
    },
    {
      title: "操作人",
      key: "actor_user_id",
      width: "17%",
      minWidth: 120,
      render: (_, event) => <span className="font-mono text-xs">{shortId(event.actor_user_id)}</span>,
    },
    {
      title: "关联版本",
      key: "version_id",
      width: "17%",
      minWidth: 140,
      render: (_, event) => <span className="font-mono text-xs">{shortId(event.version_id)}</span>,
    },
    {
      title: "请求关联 ID",
      key: "correlation_id",
      width: "17%",
      minWidth: 160,
      render: (_, event) => <span className="font-mono text-xs">{shortId(event.correlation_id)}</span>,
    },
    {
      title: "详情",
      key: "metadata_payload",
      width: "12%",
      minWidth: 180,
      render: (_, event) => <span className="block max-w-[180px] truncate text-xs text-muted-foreground">{Object.keys(event.metadata_payload).length > 0 ? JSON.stringify(event.metadata_payload) : "-"}</span>,
    },
  ];

  const isVersions = mode === "versions";
  const title = isVersions ? "历史版本" : "操作记录";
  const description = isVersions ? "查看已发布的不可变配置版本，并按需切换当前活动版本。" : "查看 Agent 的发布、激活、授权和配置变更记录。";

  return (
    <><Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) { setManifestVersion(null); setReadinessVersion(null); setRolloutVersion(null); } onOpenChange(nextOpen); }}>
      <DialogContent className="min-h-[min(360px,calc(100svh-32px))] max-w-6xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogBody>
        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
        {loading ? (
          <div className="relative min-h-48"><ListLoadingOverlay label={`正在加载${title}…`} /></div>
        ) : isVersions ? (
          <AppTable columns={versionColumns} dataSource={versions} rowKey="id" scroll={{ x: 1000 }} emptyText="暂无历史版本" ariaLabel="Agent 历史版本" pagination={{ current: currentPage, pageSize, total: versionTotal, onChange: onPageChange }} />
        ) : (
          <AppTable columns={auditColumns} dataSource={auditEvents} rowKey="id" scroll={{ x: 1000 }} emptyText="暂无操作记录" ariaLabel="Agent 操作记录" pagination={{ current: currentPage, pageSize, total: auditTotal, onChange: onPageChange }} />
        )}
        </DialogBody>
      </DialogContent>
    </Dialog><ReleaseManifestDrawer version={open && !loading ? manifestVersion : null} onClose={() => setManifestVersion(null)} />
    <ReleaseReadinessDrawer key={readinessVersion?.id ?? "no-version"} version={open && !loading ? readinessVersion : null} activeVersionId={activeVersionId} busy={busy}
      onClose={() => setReadinessVersion(null)} onActivate={onActivate} />
    {open && !loading && rolloutVersion && <ReleaseRolloutDrawer key={rolloutVersion.id} version={rolloutVersion} activeVersionId={activeVersionId} onClose={() => setRolloutVersion(null)} />}</>
  );
}
