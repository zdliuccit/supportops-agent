import { useEffect, useState } from "react";
import { LoaderCircle, RefreshCw, Save, Wrench } from "lucide-react";

import { listToolCatalog, updateToolCatalogEntry } from "@/api";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import type { ToolCatalogEntry } from "@/types";

export function ToolManagementPage() {
  const [tools, setTools] = useState<ToolCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyTool, setBusyTool] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken((token) => listToolCatalog(token));
      setTools(result.value.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取工具目录失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), []);

  function updateLocal(toolId: string, patch: Partial<ToolCatalogEntry>) {
    setTools((current) => current.map((item) => item.tool_id === toolId ? { ...item, ...patch } : item));
  }

  async function save(tool: ToolCatalogEntry) {
    setBusyTool(tool.tool_id);
    try {
      await withRefreshedToken((token) => updateToolCatalogEntry(token, tool.tool_id, {
        name: tool.name,
        description: tool.description,
        required_roles: tool.required_roles,
        risk_level: tool.risk_level,
        is_enabled: tool.is_enabled,
      }));
      notify.success(`${tool.name} 已保存，版本 v${tool.version + 1}。`);
      await load();
    } catch (cause) {
      notify.error(cause, "保存工具目录失败");
    } finally {
      setBusyTool(null);
    }
  }

  return <>
    <PageHeader title="工具目录" description="管理可绑定到 Agent 的服务端受控工具；不允许上传或执行任意代码。" actions={<Button variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "animate-spin" : ""} />刷新</Button>} />
    {error && <Alert variant="destructive" className="mt-5"><AlertDescription>{error}</AlertDescription></Alert>}
    {loading ? <div className="grid h-52 place-items-center"><LoaderCircle className="size-5 animate-spin" aria-label="加载工具目录" /></div> : <div className="mt-7 grid gap-5 xl:grid-cols-2">
      {tools.map((tool) => <section key={tool.tool_id} className="rounded-3xl border bg-white p-6 shadow-sm">
        <div className="flex items-start gap-4">
          <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-emerald-50 text-emerald-700"><Wrench className="size-5" /></div>
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">{tool.name}</h2><code className="rounded bg-muted px-2 py-0.5 text-xs">{tool.tool_id}</code><span className="text-xs text-muted-foreground">v{tool.version}</span></div><p className="mt-2 text-sm text-muted-foreground">实现：{tool.implementation_key}</p></div>
          <label className="flex items-center gap-2 text-sm"><Checkbox checked={tool.is_enabled} onCheckedChange={(checked) => updateLocal(tool.tool_id, { is_enabled: checked === true })} />启用</label>
        </div>
        <div className="mt-5 space-y-4"><label className="block text-sm font-medium">名称<Input className="mt-2" value={tool.name} onChange={(event) => updateLocal(tool.tool_id, { name: event.target.value })} /></label><label className="block text-sm font-medium">描述<Textarea className="mt-2 min-h-20" value={tool.description} onChange={(event) => updateLocal(tool.tool_id, { description: event.target.value })} /></label><label className="block text-sm font-medium">所需角色<Input className="mt-2" value={tool.required_roles.join(", ")} onChange={(event) => updateLocal(tool.tool_id, { required_roles: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} placeholder="employee, agent_user" /></label><label className="block text-sm font-medium">风险级别<select className="mt-2 h-10 w-full rounded-md border bg-white px-3 text-sm" value={tool.risk_level} onChange={(event) => updateLocal(tool.tool_id, { risk_level: event.target.value as ToolCatalogEntry["risk_level"] })}><option value="low">低</option><option value="medium">中</option><option value="high">高（必须人工审批）</option></select></label></div>
        <Button className="mt-5" onClick={() => void save(tool)} disabled={busyTool !== null}><Save />保存工具配置</Button>
      </section>)}
      {tools.length === 0 && <Alert><AlertDescription>当前租户暂无可管理工具。</AlertDescription></Alert>}
    </div>}
  </>;
}
