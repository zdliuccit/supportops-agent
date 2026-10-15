import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { StatusBadge } from "@/components/StatusBadge";
import type { AgentReleaseManifest, AgentVersion } from "@/types";
import { ReleaseComparisonPanel } from "./ReleaseComparisonPanel";

function Digest({ label, value }: { label: string; value: string | null | undefined }) {
  return <div className="space-y-1"><dt className="text-xs text-muted-foreground">{label}</dt>
    <dd className="break-all font-mono text-xs">{value ?? "未记录"}</dd></div>;
}

export function ReleaseManifestDrawer({ version, onClose }: { version: AgentVersion | null; onClose: () => void }) {
  const manifest = version?.release_manifest;
  const columns: AppTableColumn<AgentReleaseManifest["tools"][number]>[] = [
    { title: "工具", dataIndex: "tool_id" }, { title: "实现版本", dataIndex: "implementation_version" },
    { title: "目录版本", key: "catalog", render: (_, row) => row.catalog_version ?? "未登记" },
    { title: "最低风险", dataIndex: "minimum_risk" }, { title: "适配契约", dataIndex: "connector" },
    { title: "Schema 摘要", key: "schema", render: (_, row) => <span className="font-mono text-xs" title={row.schema_digest}>{row.schema_digest.slice(0, 16)}…</span> },
  ];
  return <AppDrawer open={version !== null} onOpenChange={(open) => { if (!open) onClose(); }} title={`版本 v${version?.version_number ?? ""} 发布清单`}
    description="查看发布时版本与策略摘要；当前执行仍重新授权，清单不代表评测通过。" showConfirm={false} cancelText="关闭" className="sm:max-w-3xl">
    {!manifest ? <p role={version?.release_manifest_status === "integrity_failed" ? "alert" : "status"} className="text-sm text-muted-foreground">
      {version?.release_manifest_status === "integrity_failed" ? "发布清单与摘要或版本引用不一致，已隐藏内容，请联系管理员核对历史记录。"
        : "历史版本未记录发布清单；不使用当前代码或当前知识补造旧记录。"}</p>
      : <div className="space-y-6">
        <p role="status" className="flex items-center gap-2 text-sm"><StatusBadge value="pending" label="发布时未评测" />此为不可变发布清单；最新评测适用性请在历史版本的“发布门禁”中查看。</p>
        <dl className="grid gap-4 sm:grid-cols-2">
          <Digest label="发布清单摘要" value={version?.release_manifest_digest} /><Digest label="固定模型版本" value={manifest.model.model_version_id} />
          <Digest label="配置摘要" value={manifest.config_digest} /><Digest label="Prompt 摘要" value={manifest.prompt_digest} />
          <Digest label="运行图策略摘要" value={manifest.graph_policy_digest} /><Digest label="审批策略摘要" value={manifest.authorization.approval_policy_digest} />
        </dl>
        <section className="space-y-2 text-sm"><h3 className="font-medium">固定版本与当前事实</h3>
          <p>引擎：{manifest.engine}；模型协议：{manifest.model.protocol}。发布凭据 revision {manifest.model.credential_revision_at_publish} 仅记录历史，实际 revision 在创建每轮 Run 时固定。</p>
          <p>知识：{manifest.knowledge.enabled ? "已绑定关键词检索" : "未绑定知识检索"}；检索时按当前 ACL 选择有效快照，Run 证据保存 snapshot/chunk/digest。向量关闭，未冻结整个语料。</p>
          <p>发布时 grant {manifest.authorization.grant_count_at_publish} 项；后续运行每个边界重新校验当前身份、资源与目录，发布快照不授予权限。</p>
        </section>
        <section className="space-y-3"><h3 className="text-sm font-medium">工具与适配契约</h3>
          <AppTable columns={columns} dataSource={manifest.tools} rowKey="tool_id" emptyText="没有绑定工具" ariaLabel="发布清单工具" />
        </section>
        <section className="space-y-3"><h3 className="text-sm font-medium">源码与依赖</h3>
          <dl className="space-y-3">{Object.entries(manifest.source_digests).map(([name, digest]) => <Digest key={name} label={name} value={digest} />)}</dl>
          <dl className="grid gap-3 sm:grid-cols-2">{Object.entries(manifest.dependencies).map(([name, value]) => <Digest key={name} label={name} value={value} />)}</dl>
        </section>
      </div>}
    {version && <ReleaseComparisonPanel key={version.id} version={version} />}
  </AppDrawer>;
}
