import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import type { RoutingPolicyCandidate, RoutingPolicyVersionView } from "@/types";
import { policyLabels, type AuthenticatedRead } from "../contracts";
import { RoutingPreviewForm } from "./RoutingPreviewForm";

export type RoutingDetailState = {
  policyId: string; versionId: string; digest: string; value: RoutingPolicyVersionView | null; loading: boolean; error: string | null;
};

export function RoutingPolicyDrawer({ state, onClose, onRefresh, request }: {
  state: RoutingDetailState | null; onClose: () => void; onRefresh: (target: RoutingDetailState) => void; request: AuthenticatedRead;
}) {
  return <AppDrawer open={state !== null} onOpenChange={(open) => { if (!open) onClose(); }} title="路由策略详情"
    description="固定版本及只读配置预览；保存、激活和真实评测需独立门禁。" showConfirm={false} cancelText="关闭" className="sm:max-w-4xl">
    {state?.loading && <div className="relative min-h-40"><ListLoadingOverlay label="正在读取固定策略…" /></div>}
    {state?.error && <p role="alert" className="text-sm text-red-600">{state.error}</p>}
    {state && !state.loading && <Button variant="outline" onClick={() => onRefresh(state)}>刷新原版本</Button>}
    {state?.value && <RoutingPolicyDetails key={`${state.versionId}:${state.digest}`} policy={state.value} request={request} />}
  </AppDrawer>;
}

function RoutingPolicyDetails({ policy, request }: { policy: RoutingPolicyVersionView; request: AuthenticatedRead }) {
  const definition = policy.record.definition;
  const columns: AppTableColumn<RoutingPolicyCandidate>[] = [
    { title: "优先级", dataIndex: "priority" },
    { title: "固定模型版本", dataIndex: "model_version_id", render: (value) => <span className="break-all font-mono text-xs">{String(value)}</span> },
    { title: "凭据修订", dataIndex: "credential_revision" },
    { title: "角色", key: "primary", render: (_, row) => row.candidate_id === definition.primary_candidate_id ? "原主候选" : "固定候选" },
  ];
  return <div className="mt-5 space-y-6">
    <section className="space-y-3 text-sm" aria-label="路由状态">
      <div className="flex flex-wrap items-center gap-3"><StatusBadge value={policy.status === "candidate" ? "pending" : "disabled"} label={policyLabels[policy.status]} />
        <span>版本 {policy.record.revision} · 读取时未验收</span></div>
      <div className="flex items-center gap-3"><Switch id="routing-enabled" checked={false} disabled aria-describedby="routing-disabled-reason" />
        <label htmlFor="routing-enabled">启用路由（关闭）</label></div>
      <p id="routing-disabled-reason">须完成启动决策、独立批准、发布评测和运行时接线后才能启用；当前预览不改变版本、预算或执行许可。</p>
      <dl className="space-y-2"><div><dt className="text-muted-foreground">固定版本</dt><dd className="break-all font-mono text-xs">{policy.record.version_id}</dd></div>
        <div><dt className="text-muted-foreground">原版本摘要</dt><dd className="break-all font-mono text-xs">{policy.policy_digest}</dd></div>
        <div><dt className="text-muted-foreground">原有效期</dt><dd>{new Date(definition.valid_until).toLocaleString()}</dd></div>
        {policy.revocation && <div><dt className="text-muted-foreground">永久撤销时间</dt><dd>{new Date(policy.revocation.revoked_at).toLocaleString()}</dd></div>}
        <div><dt className="text-muted-foreground">允许用途</dt><dd className="break-words">{definition.allowed_purposes.join("、")}</dd></div>
        <div><dt className="text-muted-foreground">允许处理地域</dt><dd className="break-words">{definition.allowed_regions.join("、")}</dd></div>
        <div><dt className="text-muted-foreground">允许数据分类</dt><dd className="break-words">{definition.allowed_data_classifications.join("、")}</dd></div>
      </dl>
    </section>
    <section className="space-y-3"><h3 className="font-medium">完整固定候选</h3>
      <AppTable columns={columns} dataSource={[...definition.candidates].sort((a, b) => a.priority - b.priority)} rowKey="candidate_id"
        ariaLabel="策略的完整固定候选" emptyText="固定候选缺失" />
    </section>
    <RoutingPreviewForm policy={policy} request={request} />
  </div>;
}
