import { useEffect, useRef, useState } from "react";
import { Save, Wrench } from "lucide-react";

import { listToolCatalog, setToolRevocation, updateToolCatalogEntry } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { ToolCatalogEntry } from "@/types";
import { ToolRevocationDialog } from "./components/ToolRevocationDialog";
import { StatusBadge } from "@/components/StatusBadge";

export function ToolManagementPage() {
  const [tools, setTools] = useState<ToolCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyTool, setBusyTool] = useState<string | null>(null);
  const [revocationTarget, setRevocationTarget] = useState<ToolCatalogEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });
  const [total, setTotal] = useState(0);
  const requestId = useRef(0);

  async function load(nextPagination = pagination) {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const result = await delayRequest(() => withRefreshedToken((token) => listToolCatalog(token, { page: nextPagination.current, pageSize: nextPagination.pageSize })));
      if (currentRequest !== requestId.current) return;
      setTools(result.value.items);
      setTotal(result.value.total);
    } catch (cause) {
      if (currentRequest !== requestId.current) return;
      setError(cause instanceof Error ? cause.message : "读取工具目录失败");
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  }

  useEffect(() => { void load(); return () => { requestId.current += 1; }; }, []);

  function updateLocal(toolId: string, patch: Partial<ToolCatalogEntry>) {
    setTools((current) => current.map((item) => item.tool_id === toolId ? { ...item, ...patch } : item));
  }

  async function save(tool: ToolCatalogEntry) {
    setBusyTool(tool.tool_id);
    try {
      const result = await withRefreshedToken((token) => updateToolCatalogEntry(token, tool.tool_id, {
        name: tool.name,
        description: tool.description,
        required_roles: tool.required_roles,
        risk_level: tool.risk_level,
        is_enabled: tool.is_enabled,
      }));
      notify.success(`${tool.name} 已保存，版本 v${result.value.version}。`);
      await load();
    } catch (cause) {
      notify.error(cause, "保存工具目录失败");
    } finally {
      setBusyTool(null);
    }
  }

  async function confirmRevocation() {
    if (revocationTarget === null) return;
    const target = revocationTarget;
    setBusyTool(target.tool_id);
    try {
      await withRefreshedToken((token) => setToolRevocation(token, target.tool_id, !target.is_revoked, target.version));
      notify.success(target.is_revoked ? "紧急撤销已解除，其他策略继续生效。" : "工具已紧急撤销。");
      setRevocationTarget(null);
      await load();
    } catch (cause) {
      notify.error(cause, "更新紧急撤销失败，请刷新后重新确认");
    } finally {
      setBusyTool(null);
    }
  }

  const columns: AppTableColumn<ToolCatalogEntry>[] = [{
    title: "工具配置", key: "tool",
    render: (_, tool) => <section>
        <div className="flex items-start gap-4">
          <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-emerald-50 text-emerald-700"><Wrench className="size-5" /></div>
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">{tool.name}</h2><code className="rounded bg-muted px-2 py-0.5 text-xs">{tool.tool_id}</code><span className="text-xs text-muted-foreground">v{tool.version}</span></div><p className="mt-2 text-sm text-muted-foreground">实现：{tool.implementation_key}</p></div>
          <label className="flex items-center gap-2 text-sm"><Switch checked={tool.is_enabled} disabled={busyTool !== null || loading} onCheckedChange={(checked) => updateLocal(tool.tool_id, { is_enabled: checked })} />启用</label>
          {tool.is_revoked && <StatusBadge value="failed" label="已紧急撤销" />}
        </div>
        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <label className="block text-sm font-medium">名称<Input className="mt-2" placeholder="请输入工具名称" disabled={busyTool !== null || loading} value={tool.name} onChange={(event) => updateLocal(tool.tool_id, { name: event.target.value })} /></label>
          <label className="block text-sm font-medium">所需角色<Input className="mt-2" value={tool.required_roles.join(", ")} disabled={busyTool !== null || loading} onChange={(event) => updateLocal(tool.tool_id, { required_roles: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} placeholder="请输入所需角色（逗号分隔）" /></label>
          <label className="block text-sm font-medium">描述<Textarea className="mt-2 min-h-20" placeholder="请输入工具描述" disabled={busyTool !== null || loading} value={tool.description} onChange={(event) => updateLocal(tool.tool_id, { description: event.target.value })} /></label>
          <div className="text-sm font-medium"><span id={`risk-label-${tool.tool_id}`}>风险级别</span><Select value={tool.risk_level} disabled={busyTool !== null || loading} onValueChange={(value) => updateLocal(tool.tool_id, { risk_level: value as ToolCatalogEntry["risk_level"] })}><SelectTrigger className="mt-2 w-full" aria-labelledby={`risk-label-${tool.tool_id}`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="low">低</SelectItem><SelectItem value="medium">中</SelectItem><SelectItem value="high">高（必须人工审批）</SelectItem></SelectContent></Select></div>
        </div>
        <Button className="mt-5" onClick={() => void save(tool)} disabled={busyTool !== null || loading}><Save aria-hidden="true" />{busyTool === tool.tool_id ? "保存中…" : "保存工具配置"}</Button>
        <Button className="ml-3 mt-5" variant={tool.is_revoked ? "outline" : "destructive"} disabled={busyTool !== null || loading} onClick={() => setRevocationTarget(tool)}>{tool.is_revoked ? "解除紧急撤销" : "紧急撤销"}</Button>
        <p className="mt-3 text-xs text-muted-foreground">低风险对应 R1，中风险对应 R3，高风险对应 R4；需要审批的工具在审批流程接通前不可执行。</p>
      </section>,
  }];

  return <>
    <PageHeader title="工具目录" description="管理 Agent 工具的实时启停、角色、风险与独立紧急撤销。" />
    {error && <Alert variant="destructive" className="mt-5"><AlertDescription>{error}</AlertDescription></Alert>}
    <ListToolbar title="工具列表" onRefresh={() => void load()} loading={loading || busyTool !== null} />
    <div className="relative min-h-[360px]">
      <AppTable columns={columns} dataSource={tools} rowKey="tool_id" ariaLabel="工具目录列表" emptyText="当前租户暂无可管理工具" pagination={{ ...pagination, total, onChange: (current, pageSize) => { if (busyTool !== null || loading) return; const nextPagination = { current, pageSize }; setPagination(nextPagination); void load(nextPagination); } }} />
      {loading && <ListLoadingOverlay label="正在加载工具目录…" />}
    </div>
    <ToolRevocationDialog tool={revocationTarget} busy={busyTool !== null} onClose={() => setRevocationTarget(null)} onConfirm={confirmRevocation} />
  </>;
}
