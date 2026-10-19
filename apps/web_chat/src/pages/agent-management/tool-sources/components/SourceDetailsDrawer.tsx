import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { GlobalLoading } from "@/components/GlobalLoading";
import { StatusBadge } from "@/components/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { DiscoveredToolDescriptor, ToolDiscoveryBinding, ToolDiscoveryView } from "@/types";
import { discoveryLabels } from "../contracts";

export type SourceDetailState = {
  sourceKey: string; source: ToolDiscoveryView | null; loading: boolean; error: string | null;
  descriptor: DiscoveredToolDescriptor | null; descriptorLoading: boolean; descriptorError: string | null;
};

export function SourceDetailsDrawer({ state, onClose, onRefresh, onDescribe }: {
  state: SourceDetailState; onClose: () => void; onRefresh: () => void;
  onDescribe: (source: ToolDiscoveryView, tool: ToolDiscoveryBinding) => void;
}) {
  const source = state.source;
  const columns: AppTableColumn<ToolDiscoveryBinding>[] = [
    { title: "远端名称", dataIndex: "remote_name", key: "name" },
    { title: "描述摘要", key: "digest", render: (_, tool) => <code className="break-all text-xs">{tool.descriptor_digest}</code> },
    { title: "操作", key: "actions", align: "right", render: (_, tool) => <Button variant="outline" size="sm" disabled={state.descriptorLoading}
      onClick={() => { if (source) onDescribe(source, tool); }}>查看定义</Button> },
  ];
  return <AppDrawer open title="工具来源详情" description={state.sourceKey} className="sm:max-w-3xl"
    onOpenChange={(open) => { if (!open) onClose(); }} showConfirm={false} cancelText="关闭">
    <Button variant="outline" disabled={state.loading || state.descriptorLoading} onClick={onRefresh}>刷新当前修订</Button>
    {state.loading && <GlobalLoading fullScreen={false} size="sm" label="正在读取工具来源…" />}
    {state.error && <Alert variant="destructive" className="mt-4"><AlertDescription>{state.error}</AlertDescription></Alert>}
    {source && !state.loading && <div className="mt-5 space-y-5">
      <div className="flex flex-wrap items-center gap-3"><StatusBadge value={source.status === "ready" ? "passed" : source.status === "rejected" ? "failed" : source.status} label={discoveryLabels[source.status]} />
        <StatusBadge value="unknown" label="连接未接入" /><span className="text-sm">修订 {source.revision}</span></div>
      <p className="text-sm text-muted-foreground">目录验证仅核对批准的描述与Schema。连接健康尚未确认，当前没有远端执行权限。</p>
      <dl className="space-y-3 text-sm">
        <div><dt className="text-muted-foreground">批准截止时间</dt><dd>{new Date(source.valid_until).toLocaleString()}</dd></div>
        <div><dt className="text-muted-foreground">来源指纹</dt><dd className="break-all font-mono text-xs">{source.source_fingerprint}</dd></div>
        <div><dt className="text-muted-foreground">批准策略摘要</dt><dd className="break-all font-mono text-xs">{source.policy_digest}</dd></div>
        <div><dt className="text-muted-foreground">当前目录摘要</dt><dd className="break-all font-mono text-xs">{source.catalog_digest ?? "无可用目录"}</dd></div>
        <div><dt className="text-muted-foreground">失败码</dt><dd>{source.failure_code ?? "无"}</dd></div>
      </dl>
      <section><h3 className="mb-3 font-medium">当前可用工具</h3><AppTable columns={columns} dataSource={source.tools} rowKey="tool_id" ariaLabel="来源完整工具绑定集合" emptyText="当前没有可解析的工具绑定" /></section>
      <section><h3 className="mb-3 font-medium">批准工具</h3>
        {source.policy.allowed_tools.length === 0 ? <p className="text-sm text-muted-foreground">尚未批准工具</p> : <ul className="space-y-3">
          {source.policy.allowed_tools.map((tool) => <li key={tool.name} className="text-sm"><p>{tool.name}</p><code className="break-all text-xs text-muted-foreground">{tool.descriptor_digest}</code></li>)}
        </ul>}
      </section>
      {state.descriptorLoading && <GlobalLoading fullScreen={false} size="sm" label="正在核对固定工具定义…" />}
      {state.descriptorError && <Alert variant="destructive"><AlertDescription>{state.descriptorError}</AlertDescription></Alert>}
      {state.descriptor && <section><h3 className="mb-3 font-medium">固定版本定义</h3><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted p-4 text-xs">{JSON.stringify(state.descriptor, null, 2)}</pre></section>}
    </div>}
  </AppDrawer>;
}
