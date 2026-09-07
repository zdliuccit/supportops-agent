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
import { FormField } from "@/components/FormField";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
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
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});

  function hydrateVersion(version: ModelEndpointVersion) {
    setProvider(version.provider_kind);
    setRemoteModel(version.remote_model_name);
    setBaseUrl(version.base_url);
    setStreaming(Boolean(version.capabilities.streaming));
    setToolCalling(Boolean(version.capabilities.tool_calling));
    setStructuredOutput(Boolean(version.capabilities.structured_output));
    setParallelToolCalls(Boolean(version.capabilities.parallel_tool_calls));
  }

  function resetCreateForm() {
    setName("");
    setLogoUrl("");
    setProvider("openai_official");
    setRemoteModel("gpt-5.2");
    setBaseUrl(officialUrl);
    setApiKey("");
    setStreaming(true);
    setToolCalling(true);
    setStructuredOutput(true);
    setParallelToolCalls(true);
    setFormErrors({});
  }

  function closeCreateDialog() {
    if (busy) return;
    setCreateOpen(false);
    resetCreateForm();
  }

  function closeRotateDialog() {
    if (busy) return;
    setRotateTarget(null);
    setReplacementKey("");
    setFormErrors({});
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

  function validateModelField(field: "name" | "apiKey", value: string) {
    const message = value.trim() ? undefined : field === "name" ? "请输入模型显示名称" : "请输入 API Key";
    setFormErrors((current) => {
      const next = { ...current };
      if (message) next[field] = message;
      else delete next[field];
      return next;
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = "请输入模型显示名称";
    if (!remoteModel.trim()) errors.remoteModel = "请输入模型名称";
    if (!baseUrl.trim()) errors.baseUrl = "请输入 API Base URL";
    if (!apiKey.trim()) errors.apiKey = "请输入 API Key";
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    setBusy(true);
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
      setFormErrors({});
      setCreateOpen(false);
      await load();
      notify.success("模型端点已创建。完成连接测试后，它才能绑定到 Agent。");
    } catch (cause) {
      notify.error(cause, "创建模型端点失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveProfile() {
    if (!modelId || !name.trim()) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) =>
        updateModelEndpointProfile(token, modelId, {
          name: name.trim(),
          logo_url: logoUrl.trim() || null,
        }),
      );
      await load();
      notify.success("模型基础信息已保存，不改变已固定的端点版本。");
    } catch (cause) {
      notify.error(cause, "保存模型基础信息失败");
    } finally {
      setBusy(false);
    }
  }

  async function addVersion(event: FormEvent) {
    event.preventDefault();
    if (!modelId) return;
    const errors: Record<string, string> = {};
    if (!remoteModel.trim()) errors.remoteModel = "请输入模型名称";
    if (!baseUrl.trim()) errors.baseUrl = "请输入 API Base URL";
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    setBusy(true);
    try {
      const result = await withRefreshedToken((token) =>
        createModelEndpointVersion(token, modelId, versionPayload()),
      );
      await load();
      notify.success(`v${result.value.version_number} 已保存，请重新执行连接测试。`);
    } catch (cause) {
      notify.error(cause, "保存模型版本失败");
    } finally {
      setBusy(false);
    }
  }

  async function test(id: string) {
    setBusy(true);
    try {
      const result = await withRefreshedToken((token) => testModelEndpoint(token, id));
      const passed = Object.entries(result.value.checks)
        .filter(([, status]) => status === "passed")
        .map(([name]) => name)
        .join("、");
      if (result.value.status === "verified") {
        notify.success(`连接测试通过：${passed || "基础模型调用"}。`);
      } else {
        notify.warning("连接测试未通过，请检查域名、协议、模型名称和密钥。");
      }
      await load();
    } catch (cause) {
      notify.error(cause, "连接测试失败");
    } finally {
      setBusy(false);
    }
  }

  async function rotate(event: FormEvent) {
    event.preventDefault();
    if (!replacementKey.trim()) {
      setFormErrors({ replacementKey: "请输入新的 API Key" });
      return;
    }
    if (!rotateTarget) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) =>
        rotateModelCredential(token, rotateTarget, replacementKey.trim()),
      );
      setReplacementKey("");
      setFormErrors({});
      setRotateTarget(null);
      await load();
      notify.success("密钥已轮换，credential revision 已递增；已创建的 Run 仍使用原 revision。");
    } catch (cause) {
      notify.error(cause, "密钥轮换失败");
    } finally {
      setBusy(false);
    }
  }

  async function turnOff(id: string) {
    if (!window.confirm("停用模型端点后，引用它的 Agent 不能发布新版本。确认停用？")) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => disableModelEndpoint(token, id));
      await load();
      notify.warning("模型端点已停用；既有 AgentVersion 和 Run 的固定引用未改变。");
    } catch (cause) {
      notify.error(cause, "停用模型端点失败");
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
      <PageHeader
        title={selected ? selected.name : "模型配置"}
        description="统一管理官方模型与 OpenAI-compatible 中转站配置。"
        actions={!modelId ? <Button onClick={() => { resetCreateForm(); setCreateOpen(true); }}><Plus />新增模型</Button> : undefined}
      />
      <div aria-live="polite">
        {error && <Alert variant="destructive" className="mt-5"><CircleAlert className="size-4" /><AlertDescription>{error}</AlertDescription></Alert>}
      </div>
      {modelId ? (
        loading || !selected ? <RefreshCw className="mt-12 size-5 animate-spin" aria-label="加载模型端点" /> : (
          <fieldset disabled={selected.read_only} className="mt-8 space-y-6 disabled:opacity-75">
            {selected.read_only && <Alert variant="warning" role="status"><AlertDescription>这是升级时生成的历史模型占位记录，仅用于保留旧 Run 引用，不能修改、测试、轮换密钥或创建版本。</AlertDescription></Alert>}
            <section className="rounded-3xl border bg-white p-6">
              <div className="flex items-center justify-between gap-4"><div><h2 className="font-semibold">基础信息</h2><p className="mt-1 text-xs text-muted-foreground">名称和 Logo 不改变不可变调用配置。</p></div><Button variant="outline" onClick={() => void saveProfile()} disabled={busy || !name.trim()}><Save />保存信息</Button></div>
              <div className="mt-5 grid gap-4 md:grid-cols-2"><Label className="text-sm">显示名称<Input className="mt-2" value={name} onChange={(event) => setName(event.target.value)} placeholder="请输入模型显示名称" /></Label><Label className="text-sm">Logo URL<Input className="mt-2" type="url" value={logoUrl} onChange={(event) => setLogoUrl(event.target.value)} placeholder="请输入 Logo URL" /></Label></div>
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
                <form onSubmit={addVersion} noValidate className="rounded-3xl border bg-white p-6">
                  <h2 className="font-semibold">保存新版本</h2><p className="mt-1 text-xs text-muted-foreground">保存后先测试，通过后才可绑定 Agent。</p>
                  <VersionFields provider={provider} setProvider={setProvider} remoteModel={remoteModel} setRemoteModel={(value) => { setRemoteModel(value); setFormErrors((current) => { const next = { ...current }; delete next.remoteModel; return next; }); }} baseUrl={baseUrl} setBaseUrl={(value) => { setBaseUrl(value); setFormErrors((current) => { const next = { ...current }; delete next.baseUrl; return next; }); }} options={capabilityOptions} errors={formErrors} />
                  <Button type="submit" className="mt-5 w-full" disabled={busy}>保存不可变版本</Button>
                </form>
              </div>
            </div>
          </fieldset>
        )
      ) : (
        <section className="mt-8 rounded-3xl border bg-white p-6"><div><h2 className="font-semibold">模型端点</h2><p className="mt-1 text-xs text-muted-foreground">官方模型与 OpenAI-compatible 中转站统一管理。</p></div>{loading ? <RefreshCw className="mt-8 size-5 animate-spin" aria-label="加载模型端点列表" /> : models.length === 0 ? <p className="mt-6 text-sm text-muted-foreground">还没有模型配置。</p> : <div className="mt-4 divide-y">{models.map((model) => <div key={model.id} className="flex items-center gap-4 py-4"><div className="grid size-10 place-items-center overflow-hidden rounded-xl bg-muted">{model.logo_url ? <img src={model.logo_url} alt="" className="size-full object-cover" /> : <Server className="size-4" />}</div><div className="min-w-0 flex-1"><div className="truncate font-medium">{model.name}</div><div className="text-xs text-muted-foreground">{model.status} · Key {model.credential_masked_hint}</div></div><Link className={buttonVariants({ variant: "outline", size: "sm" })} to={`/admin/models/${model.id}`}>详情</Link></div>)}</div>}</section>
      )}
      <Dialog open={createOpen} onOpenChange={(open) => { if (open) setCreateOpen(true); else closeCreateDialog(); }}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <form onSubmit={submit} noValidate>
            <DialogHeader><DialogTitle>新增模型端点</DialogTitle><DialogDescription>配置官方模型或 OpenAI-compatible 中转站，密钥只会加密保存到服务端。</DialogDescription></DialogHeader>
            <FormField label="显示名称" htmlFor="create-model-name" required error={formErrors.name} className="mt-5"><Input id="create-model-name" value={name} onChange={(event) => { setName(event.target.value); setFormErrors((current) => { const next = { ...current }; delete next.name; return next; }); }} onBlur={(event) => validateModelField("name", event.currentTarget.value)} placeholder="请输入模型显示名称" aria-invalid={Boolean(formErrors.name)} aria-describedby={formErrors.name ? "create-model-name-error" : undefined} /></FormField>
            <Label className="mt-4 block text-sm">Logo URL<Input className="mt-2" type="url" value={logoUrl} onChange={(event) => setLogoUrl(event.target.value)} placeholder="请输入 Logo URL" /></Label>
            <VersionFields provider={provider} setProvider={setProvider} remoteModel={remoteModel} setRemoteModel={(value) => { setRemoteModel(value); setFormErrors((current) => { const next = { ...current }; delete next.remoteModel; return next; }); }} baseUrl={baseUrl} setBaseUrl={(value) => { setBaseUrl(value); setFormErrors((current) => { const next = { ...current }; delete next.baseUrl; return next; }); }} options={capabilityOptions} errors={formErrors} />
            <FormField label="API Key（只写）" htmlFor="create-model-api-key" required error={formErrors.apiKey} className="mt-4"><Input id="create-model-api-key" type="password" value={apiKey} onChange={(event) => { setApiKey(event.target.value); setFormErrors((current) => { const next = { ...current }; delete next.apiKey; return next; }); }} onBlur={(event) => validateModelField("apiKey", event.currentTarget.value)} placeholder="请输入 API Key" autoComplete="new-password" aria-describedby="new-key-help" aria-invalid={Boolean(formErrors.apiKey)} /></FormField>
            <p id="new-key-help" className="mt-2 text-xs text-muted-foreground">密钥只会提交到服务端加密保存，页面不会读取或再次显示明文。</p>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={closeCreateDialog} disabled={busy}>取消</Button><Button type="submit" disabled={busy}>{busy ? "保存中…" : "保存配置"}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={rotateTarget !== null} onOpenChange={(open) => { if (open) return; closeRotateDialog(); }}>
        <DialogContent>
          <form onSubmit={rotate} noValidate>
            <DialogHeader>
              <DialogTitle>轮换模型 API Key</DialogTitle>
              <DialogDescription>新密钥只写入服务端并生成新的 credential revision，不会在页面中回显。已经排队的 Run 继续使用其固定 revision。</DialogDescription>
            </DialogHeader>
            <FormField label="新的 API Key" htmlFor="replacement-api-key" required error={formErrors.replacementKey} className="mt-5"><Input id="replacement-api-key" type="password" value={replacementKey} onChange={(event) => { setReplacementKey(event.target.value); setFormErrors((current) => { const next = { ...current }; delete next.replacementKey; return next; }); }} placeholder="请输入新的 API Key" autoComplete="new-password" autoFocus aria-invalid={Boolean(formErrors.replacementKey)} aria-describedby={formErrors.replacementKey ? "replacement-api-key-error" : undefined} /></FormField>
            <DialogFooter className="mt-6">
              <Button type="button" variant="outline" onClick={closeRotateDialog} disabled={busy}>取消</Button>
              <Button type="submit" disabled={busy}>{busy ? "轮换中…" : "确认轮换"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

function VersionFields({ provider, setProvider, remoteModel, setRemoteModel, baseUrl, setBaseUrl, options, errors = {} }: {
  provider: "openai_official" | "openai_compatible";
  setProvider: (value: "openai_official" | "openai_compatible") => void;
  remoteModel: string;
  setRemoteModel: (value: string) => void;
  baseUrl: string;
  setBaseUrl: (value: string) => void;
  options: readonly (readonly [string, boolean, (value: boolean) => void])[];
  errors?: Record<string, string>;
}) {
  return <><FormField label="供应商" htmlFor="model-provider" required className="mt-4"><Select value={provider} onValueChange={(value: typeof provider) => { setProvider(value); if (value === "openai_official") setBaseUrl(officialUrl); }}><SelectTrigger id="model-provider" className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="openai_official">OpenAI 官方</SelectItem><SelectItem value="openai_compatible">OpenAI-compatible 中转站</SelectItem></SelectContent></Select></FormField><FormField label="模型名称" htmlFor="model-remote-name" required error={errors.remoteModel} className="mt-4"><Input id="model-remote-name" value={remoteModel} onChange={(event) => setRemoteModel(event.target.value)} placeholder="请输入模型名称" aria-invalid={Boolean(errors.remoteModel)} aria-describedby={errors.remoteModel ? "model-remote-name-error" : undefined} /></FormField><FormField label="API Base URL" htmlFor="model-base-url" required error={errors.baseUrl} className="mt-4"><Input id="model-base-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="请输入 API Base URL" disabled={provider === "openai_official"} aria-invalid={Boolean(errors.baseUrl)} aria-describedby={errors.baseUrl ? "model-base-url-error" : undefined} /></FormField><fieldset className="mt-4"><legend className="text-sm">声明能力</legend><div className="mt-2 grid grid-cols-2 gap-2">{options.map(([label, checked, update]) => <Label key={label} className="flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-xs font-normal"><Checkbox checked={checked} onCheckedChange={(value) => update(value === true)} />{label}</Label>)}</div></fieldset></>;
}
