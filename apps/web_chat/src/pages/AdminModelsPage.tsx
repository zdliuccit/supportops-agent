import { type FormEvent, useEffect, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  KeyRound,
  Plus,
  RefreshCw,
  Save,
  Server,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  createModelEndpoint,
  createModelEndpointVersion,
  disableModelEndpoint,
  listModelEndpoints,
  listModelEndpointVersions,
  rotateModelCredential,
  testModelEndpoint,
  updateModelEndpointProfile,
} from "@/api";
import { PageHeader } from "@/components/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { withRefreshedToken } from "@/lib/auth";
import type { ModelEndpoint, ModelEndpointVersion } from "@/types";

const officialUrl = "https://api.openai.com/v1";

function verificationChecks(version: ModelEndpointVersion): Array<[string, string]> {
  const value = version.verification_result.checks;
  if (typeof value !== "object" || value === null || Array.isArray(value)) return [];
  return Object.entries(value).map(([name, status]) => [name, String(status)]);
}

export function AdminModelsPage() {
  const { modelId } = useParams();
  const [models, setModels] = useState<ModelEndpoint[]>([]);
  const [versions, setVersions] = useState<ModelEndpointVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [provider, setProvider] = useState<"openai_official" | "openai_compatible">("openai_official");
  const [name, setName] = useState("");
  const [logoUrl, setLogoUrl] = useState("");
  const [remoteModel, setRemoteModel] = useState("gpt-5.2");
  const [baseUrl, setBaseUrl] = useState(officialUrl);
  const [apiKey, setApiKey] = useState("");
  const [streaming, setStreaming] = useState(true);
  const [toolCalling, setToolCalling] = useState(true);
  const [structuredOutput, setStructuredOutput] = useState(true);
  const [parallelToolCalls, setParallelToolCalls] = useState(true);
  const [rotateTarget, setRotateTarget] = useState<string | null>(null);
  const [replacementKey, setReplacementKey] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  function hydrateVersion(version: ModelEndpointVersion) {
    setProvider(version.provider_kind);
    setRemoteModel(version.remote_model_name);
    setBaseUrl(version.base_url);
    setStreaming(Boolean(version.capabilities.streaming));
    setToolCalling(Boolean(version.capabilities.tool_calling));
    setStructuredOutput(Boolean(version.capabilities.structured_output));
    setParallelToolCalls(Boolean(version.capabilities.parallel_tool_calls));
  }

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken((token) => listModelEndpoints(token));
      setModels(result.value.items);
      if (modelId) {
        const selected = result.value.items.find((item) => item.id === modelId);
        if (selected) {
          setName(selected.name);
          setLogoUrl(selected.logo_url ?? "");
        }
        const history = await listModelEndpointVersions(result.token, modelId);
        setVersions(history.items);
        if (history.items[0]) hydrateVersion(history.items[0]);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取模型配置失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), [modelId]);

  function versionPayload(): Record<string, unknown> {
    return {
      provider_kind: provider,
      api_protocol: provider === "openai_official" ? "responses" : "chat_completions",
      base_url: provider === "openai_official" ? officialUrl : baseUrl,
      remote_model_name: remoteModel,
      capabilities: {
        streaming,
        tool_calling: toolCalling,
        structured_output: structuredOutput,
        parallel_tool_calls: parallelToolCalls,
        vision: false,
      },
    };
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await withRefreshedToken((token) =>
        createModelEndpoint(token, {
          name,
          logo_url: logoUrl.trim() || null,
          api_key: apiKey,
          version: versionPayload(),
        }),
      );
      setName("");
      setLogoUrl("");
      setApiKey("");
      setCreateOpen(false);
      setMessage("模型端点已创建。完成连接测试后，它才能绑定到 Agent。");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建模型端点失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveProfile() {
    if (!modelId || !name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await withRefreshedToken((token) =>
        updateModelEndpointProfile(token, modelId, {
          name: name.trim(),
          logo_url: logoUrl.trim() || null,
        }),
      );
      setMessage("模型基础信息已保存，不改变已固定的端点版本。");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存模型基础信息失败");
    } finally {
      setBusy(false);
    }
  }

  async function addVersion(event: FormEvent) {
    event.preventDefault();
    if (!modelId) return;
    setBusy(true);
    setError(null);
    try {
      const result = await withRefreshedToken((token) =>
        createModelEndpointVersion(token, modelId, versionPayload()),
      );
      setMessage(`v${result.value.version_number} 已保存，请重新执行连接测试。`);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存模型版本失败");
    } finally {
      setBusy(false);
    }
  }

  async function test(id: string) {
    setBusy(true);
    setError(null);
    try {
      const result = await withRefreshedToken((token) => testModelEndpoint(token, id));
      const passed = Object.entries(result.value.checks)
        .filter(([, status]) => status === "passed")
        .map(([name]) => name)
        .join("、");
      setMessage(
        result.value.status === "verified"
          ? `连接测试通过：${passed || "基础模型调用"}。`
          : "连接测试失败，请检查域名、协议、模型名称和密钥。",
      );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "连接测试失败");
    } finally {
      setBusy(false);
    }
  }

  async function rotate(event: FormEvent) {
    event.preventDefault();
    if (!rotateTarget || !replacementKey.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await withRefreshedToken((token) =>
        rotateModelCredential(token, rotateTarget, replacementKey.trim()),
      );
      setReplacementKey("");
      setRotateTarget(null);
      setMessage("密钥已轮换，credential revision 已递增；已创建的 Run 仍使用原 revision。 ");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "密钥轮换失败");
    } finally {
      setBusy(false);
    }
  }

  async function turnOff(id: string) {
    if (!window.confirm("停用模型端点后，引用它的 Agent 不能发布新版本。确认停用？")) return;
    setBusy(true);
    setError(null);
    try {
      await withRefreshedToken((token) => disableModelEndpoint(token, id));
      setMessage("模型端点已停用；既有 AgentVersion 和 Run 的固定引用未改变。");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "停用模型端点失败");
    } finally {
      setBusy(false);
    }
  }

  const selected = models.find((item) => item.id === modelId);
  const capabilityOptions = [
    ["Streaming", streaming, setStreaming],
    ["Tool calling", toolCalling, setToolCalling],
    ["Structured output", structuredOutput, setStructuredOutput],
    ["Parallel tools", parallelToolCalls, setParallelToolCalls],
  ] as const;

  return (
    <>
      <PageHeader title={selected ? selected.name : "模型配置"} description="统一管理官方模型与 OpenAI-compatible 中转站配置。" />
      <div aria-live="polite">
        {error && <div className="mt-5 flex gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert"><CircleAlert className="mt-0.5 size-4 shrink-0" />{error}</div>}
        {message && <div className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">{message}</div>}
      </div>
      {modelId ? (
        loading || !selected ? <RefreshCw className="mt-12 size-5 animate-spin" aria-label="加载模型端点" /> : (
          <fieldset disabled={selected.read_only} className="mt-8 space-y-6 disabled:opacity-75">
            {selected.read_only && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">这是升级时生成的历史模型占位记录，仅用于保留旧 Run 引用，不能修改、测试、轮换密钥或创建版本。</div>}
            <section className="rounded-3xl border bg-white p-6">
              <div className="flex items-center justify-between gap-4"><div><h2 className="font-semibold">基础信息</h2><p className="mt-1 text-xs text-muted-foreground">名称和 Logo 不改变不可变调用配置。</p></div><Button variant="outline" onClick={() => void saveProfile()} disabled={busy || !name.trim()}><Save />保存信息</Button></div>
              <div className="mt-5 grid gap-4 md:grid-cols-2"><label className="text-sm">显示名称<Input className="mt-2" value={name} onChange={(event) => setName(event.target.value)} /></label><label className="text-sm">Logo URL<Input className="mt-2" type="url" value={logoUrl} onChange={(event) => setLogoUrl(event.target.value)} /></label></div>
            </section>
            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
              <section className="rounded-3xl border bg-white p-6">
                <h2 className="font-semibold">不可变配置版本</h2>
                <div className="mt-4 space-y-3">{versions.map((version) => <div key={version.id} className="rounded-2xl border p-4 text-sm"><div className="flex items-center justify-between gap-3"><strong>v{version.version_number} · {version.remote_model_name}</strong><span className="text-xs text-muted-foreground">{version.verification_status}</span></div><div className="mt-2 break-all text-xs text-muted-foreground">{version.base_url}</div><div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(version.capabilities).filter(([, enabled]) => enabled).map(([capability]) => <span key={capability} className="rounded-full bg-muted px-2 py-1 text-[11px]">{capability}</span>)}</div>{verificationChecks(version).length > 0 && <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 border-t pt-3 text-xs">{verificationChecks(version).map(([check, status]) => <div key={check} className="contents"><dt className="text-muted-foreground">{check}</dt><dd className={status === "passed" ? "text-emerald-700" : status === "disabled" ? "text-muted-foreground" : "text-amber-700"}>{status}</dd></div>)}</dl>}</div>)}</div>
              </section>
              <div className="space-y-6">
                <aside className="rounded-3xl border bg-white p-6">
                  <div className="text-sm text-muted-foreground">状态</div><div className="mt-1 flex items-center gap-2 font-medium"><CheckCircle2 className="size-4" />{selected.status}</div>
                  <div className="mt-5 text-sm text-muted-foreground">密钥</div><div className="mt-1 font-mono text-sm">{selected.credential_masked_hint}</div><div className="mt-1 text-xs text-muted-foreground">credential revision {selected.credential_revision}</div>
                  <Button className="mt-6 w-full" onClick={() => void test(modelId)} disabled={busy}>测试连接</Button><Button variant="outline" className="mt-2 w-full" onClick={() => setRotateTarget(modelId)} disabled={busy}><KeyRound />轮换密钥</Button>{selected.status !== "disabled" && <Button variant="ghost" className="mt-2 w-full text-destructive hover:text-destructive" onClick={() => void turnOff(modelId)} disabled={busy}>停用端点</Button>}
                </aside>
                <form onSubmit={addVersion} className="rounded-3xl border bg-white p-6">
                  <h2 className="font-semibold">保存新版本</h2><p className="mt-1 text-xs text-muted-foreground">保存后先测试，通过后才可绑定 Agent。</p>
                  <VersionFields provider={provider} setProvider={setProvider} remoteModel={remoteModel} setRemoteModel={setRemoteModel} baseUrl={baseUrl} setBaseUrl={setBaseUrl} options={capabilityOptions} />
                  <Button type="submit" className="mt-5 w-full" disabled={busy || !remoteModel.trim() || !baseUrl.trim()}>保存不可变版本</Button>
                </form>
              </div>
            </div>
          </fieldset>
        )
      ) : (
        <section className="mt-8 rounded-3xl border bg-white p-6"><div className="flex items-center justify-between gap-4"><div><h2 className="font-semibold">模型端点</h2><p className="mt-1 text-xs text-muted-foreground">官方模型与 OpenAI-compatible 中转站统一管理。</p></div><Button onClick={() => { setCreateOpen(true); setError(null); setMessage(null); }}><Plus />新增模型</Button></div>{loading ? <RefreshCw className="mt-8 size-5 animate-spin" aria-label="加载模型端点列表" /> : models.length === 0 ? <p className="mt-6 text-sm text-muted-foreground">还没有模型配置。</p> : <div className="mt-4 divide-y">{models.map((model) => <div key={model.id} className="flex items-center gap-4 py-4"><div className="grid size-10 place-items-center overflow-hidden rounded-xl bg-muted">{model.logo_url ? <img src={model.logo_url} alt="" className="size-full object-cover" /> : <Server className="size-4" />}</div><div className="min-w-0 flex-1"><div className="truncate font-medium">{model.name}</div><div className="text-xs text-muted-foreground">{model.status} · Key {model.credential_masked_hint}</div></div><Link className={buttonVariants({ variant: "outline", size: "sm" })} to={`/admin/models/${model.id}`}>详情</Link></div>)}</div>}</section>
      )}
      <Dialog open={createOpen} onOpenChange={(open) => { if (!busy) setCreateOpen(open); }}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <form onSubmit={submit}>
            <DialogHeader><DialogTitle>新增模型端点</DialogTitle><DialogDescription>配置官方模型或 OpenAI-compatible 中转站，密钥只会加密保存到服务端。</DialogDescription></DialogHeader>
            <label className="mt-5 block text-sm">显示名称<Input className="mt-2" value={name} onChange={(event) => setName(event.target.value)} required /></label>
            <label className="mt-4 block text-sm">Logo URL<Input className="mt-2" type="url" value={logoUrl} onChange={(event) => setLogoUrl(event.target.value)} /></label>
            <VersionFields provider={provider} setProvider={setProvider} remoteModel={remoteModel} setRemoteModel={setRemoteModel} baseUrl={baseUrl} setBaseUrl={setBaseUrl} options={capabilityOptions} />
            <label className="mt-4 block text-sm">API Key（只写）<Input className="mt-2" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" aria-describedby="new-key-help" required /></label>
            <p id="new-key-help" className="mt-2 text-xs text-muted-foreground">密钥只会提交到服务端加密保存，页面不会读取或再次显示明文。</p>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={busy}>取消</Button><Button type="submit" disabled={busy || !name || !apiKey}>{busy ? "保存中…" : "保存配置"}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={rotateTarget !== null} onOpenChange={(open) => { if (!open && !busy) { setRotateTarget(null); setReplacementKey(""); } }}>
        <DialogContent>
          <form onSubmit={rotate}>
            <DialogHeader>
              <DialogTitle>轮换模型 API Key</DialogTitle>
              <DialogDescription>新密钥只写入服务端并生成新的 credential revision，不会在页面中回显。已经排队的 Run 继续使用其固定 revision。</DialogDescription>
            </DialogHeader>
            <label className="mt-5 block text-sm" htmlFor="replacement-api-key">新的 API Key</label>
            <Input id="replacement-api-key" className="mt-2" type="password" value={replacementKey} onChange={(event) => setReplacementKey(event.target.value)} autoComplete="new-password" autoFocus required />
            <DialogFooter className="mt-6">
              <Button type="button" variant="outline" onClick={() => { setRotateTarget(null); setReplacementKey(""); }} disabled={busy}>取消</Button>
              <Button type="submit" disabled={busy || !replacementKey.trim()}>{busy ? "轮换中…" : "确认轮换"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

function VersionFields({ provider, setProvider, remoteModel, setRemoteModel, baseUrl, setBaseUrl, options }: {
  provider: "openai_official" | "openai_compatible";
  setProvider: (value: "openai_official" | "openai_compatible") => void;
  remoteModel: string;
  setRemoteModel: (value: string) => void;
  baseUrl: string;
  setBaseUrl: (value: string) => void;
  options: readonly (readonly [string, boolean, (value: boolean) => void])[];
}) {
  return <><label className="mt-4 block text-sm">供应商<select className="mt-2 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={provider} onChange={(event) => { const value = event.target.value as typeof provider; setProvider(value); if (value === "openai_official") setBaseUrl(officialUrl); }}><option value="openai_official">OpenAI 官方</option><option value="openai_compatible">OpenAI-compatible 中转站</option></select></label><label className="mt-4 block text-sm">模型名称<Input className="mt-2" value={remoteModel} onChange={(event) => setRemoteModel(event.target.value)} required /></label><label className="mt-4 block text-sm">API Base URL<Input className="mt-2" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} disabled={provider === "openai_official"} required /></label><fieldset className="mt-4"><legend className="text-sm">声明能力</legend><div className="mt-2 grid grid-cols-2 gap-2">{options.map(([label, checked, update]) => <label key={label} className="flex items-center gap-2 rounded-xl border px-3 py-2 text-xs"><input type="checkbox" checked={checked} onChange={(event) => update(event.target.checked)} className="size-4 accent-black" />{label}</label>)}</div></fieldset></>;
}
