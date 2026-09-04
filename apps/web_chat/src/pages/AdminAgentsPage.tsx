import { type FormEvent, useEffect, useMemo, useState } from "react";
import {
  Bot,
  CheckCircle2,
  CircleAlert,
  History,
  Plus,
  RefreshCw,
  Rocket,
  Save,
  ShieldCheck,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  activateAgentVersion,
  ApiError,
  createAdminAgent,
  disableAgent,
  getAdminAgent,
  getAgentDraft,
  listAdminAgents,
  listAgentAuditEvents,
  listAgentGrants,
  listAgentVersions,
  listModelEndpointVersions,
  listModelEndpoints,
  publishAgent,
  replaceAgentAccessGrants,
  updateAgentDraft,
  updateAgentProfile,
  validateAgentDraft,
} from "@/api";
import { PageHeader } from "@/components/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import type {
  AdminAgent,
  AgentAuditEvent,
  AgentConfig,
  AgentDraft,
  AgentGrant,
  AgentVersion,
  ModelEndpoint,
  ModelEndpointVersion,
} from "@/types";

const toolCatalog = [
  {
    id: "support_ticket_lookup",
    name: "查询支持工单",
    description: "按工单编号读取当前租户内的工单摘要。",
  },
];

function errorMessage(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError && cause.status === 409) {
    const revision = cause.details.current_revision;
    return `草稿已被其他管理员更新${typeof revision === "number" ? `（服务端 revision ${revision}）` : ""}。当前输入已保留，请复制内容后刷新再合并。`;
  }
  if (cause instanceof ApiError && Array.isArray(cause.details.issues)) {
    const issues = cause.details.issues
      .map((issue) => {
        if (typeof issue !== "object" || issue === null) return null;
        const field = "field" in issue ? String(issue.field) : "配置";
        const message = "message" in issue ? String(issue.message) : "不合法";
        return `${field}：${message}`;
      })
      .filter(Boolean);
    if (issues.length) return issues.join("；");
  }
  return cause instanceof Error ? cause.message : fallback;
}

function fieldIssueMap(cause: unknown): Record<string, string> {
  if (!(cause instanceof ApiError) || !Array.isArray(cause.details.issues)) return {};
  const entries = cause.details.issues.flatMap((issue) => {
    if (typeof issue !== "object" || issue === null || !("field" in issue)) return [];
    const field = String(issue.field);
    const message = "message" in issue ? String(issue.message) : "配置不合法";
    return [[field, message] as const];
  });
  return Object.fromEntries(entries);
}

export function AdminAgentsPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [totalAgents, setTotalAgents] = useState(0);
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState<"" | AdminAgent["status"]>("");
  const [models, setModels] = useState<ModelEndpoint[]>([]);
  const [selectedModelVersions, setSelectedModelVersions] = useState<ModelEndpointVersion[]>([]);
  const [selected, setSelected] = useState<AdminAgent | null>(null);
  const [draft, setDraft] = useState<AgentDraft | null>(null);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [grants, setGrants] = useState<AgentGrant[]>([]);
  const [auditEvents, setAuditEvents] = useState<AgentAuditEvent[]>([]);
  const [prompt, setPrompt] = useState("");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [logoUrl, setLogoUrl] = useState("");
  const [description, setDescription] = useState("");
  const [welcomeMessage, setWelcomeMessage] = useState("");
  const [suggestedPrompts, setSuggestedPrompts] = useState("");
  const [modelEndpointId, setModelEndpointId] = useState("");
  const [ticketLookupEnabled, setTicketLookupEnabled] = useState(false);
  const [temperature, setTemperature] = useState(0.2);
  const [maxOutputTokens, setMaxOutputTokens] = useState(4096);
  const [runTimeoutSeconds, setRunTimeoutSeconds] = useState(120);
  const [modelCallLimit, setModelCallLimit] = useState(6);
  const [toolCallLimit, setToolCallLimit] = useState(8);
  const [releaseNotes, setReleaseNotes] = useState("");
  const [grantRoles, setGrantRoles] = useState<string[]>([]);
  const [grantUserIds, setGrantUserIds] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [fieldIssues, setFieldIssues] = useState<Record<string, string>>({});
  const [conflictRevision, setConflictRevision] = useState<number | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const activeModels = useMemo(
    () => models.filter((model) => model.status === "active" && model.active_version_id),
    [models],
  );
  const selectedModel = models.find((model) => model.id === modelEndpointId);
  const selectedModelVersion = selectedModelVersions.find(
    (version) => version.id === selectedModel?.active_version_id,
  );
  const publishBlockers = useMemo(() => {
    const blockers: string[] = [];
    if (!prompt.trim()) blockers.push("System Prompt 不能为空");
    if (!selectedModel) blockers.push("必须选择模型端点");
    if (selectedModel && selectedModel.status !== "active") blockers.push("模型端点未启用");
    if (selectedModel && !selectedModel.active_version_id) blockers.push("模型端点没有活动版本");
    if (selectedModel && !selectedModelVersion) blockers.push("尚未读取到模型活动版本");
    if (selectedModelVersion && selectedModelVersion.verification_status !== "verified") {
      blockers.push("模型活动版本未完整通过能力验证");
    }
    if (selectedModelVersion && !selectedModelVersion.capabilities.tool_calling) {
      blockers.push("ToolStrategy 需要模型支持 tool calling");
    }
    const modelOutputLimit = selectedModelVersion?.defaults.max_output_tokens;
    if (typeof modelOutputLimit === "number" && maxOutputTokens > modelOutputLimit) {
      blockers.push(`最大输出 Token 超过模型上限 ${modelOutputLimit}`);
    }
    return blockers;
  }, [maxOutputTokens, prompt, selectedModel, selectedModelVersion]);

  function issueFor(...fields: string[]): string | undefined {
    return fields.map((field) => fieldIssues[field]).find(Boolean);
  }

  function resetActionFeedback() {
    setError(null);
    setMessage(null);
    setFieldIssues({});
    setConflictRevision(null);
  }

  function reportActionError(cause: unknown, fallback: string) {
    setError(errorMessage(cause, fallback));
    setFieldIssues(fieldIssueMap(cause));
    const revision = cause instanceof ApiError ? cause.details.current_revision : null;
    setConflictRevision(typeof revision === "number" ? revision : null);
  }

  async function copyCurrentConfig() {
    await navigator.clipboard.writeText(JSON.stringify(buildConfig(), null, 2));
    setMessage("当前未保存配置已复制，可以加载服务端草稿后手动合并。");
  }

  function hydrateProfile(agent: AdminAgent) {
    setSelected(agent);
    setName(agent.name);
    setSlug(agent.slug);
    setLogoUrl(agent.logo_url ?? "");
    setDescription(agent.description);
    setWelcomeMessage(agent.welcome_message);
    setSuggestedPrompts(agent.suggested_prompts.join("\n"));
  }

  function hydrateDraft(value: AgentDraft) {
    const config = value.config;
    setDraft(value);
    setPrompt(config.prompt.system_prompt);
    setModelEndpointId(config.model.model_endpoint_id);
    setTicketLookupEnabled(
      config.tools.some((tool) => tool.tool_id === "support_ticket_lookup" && tool.enabled),
    );
    setTemperature(config.model.generation?.temperature ?? 0.2);
    setMaxOutputTokens(config.model.generation?.max_output_tokens ?? 4096);
    setRunTimeoutSeconds(config.runtime.run_timeout_seconds ?? 120);
    setModelCallLimit(config.runtime.model_call_limit ?? 6);
    setToolCallLimit(config.runtime.tool_call_limit ?? 8);
  }

  async function load() {
    setLoading(true);
    setError(null);
    setFieldIssues({});
    setConflictRevision(null);
    try {
      const result = await withRefreshedToken(async (token) => {
        const [agentList, modelList] = await Promise.all([
          listAdminAgents(token, {
            limit: 20,
            offset: page * 20,
            status: statusFilter || undefined,
          }),
          listModelEndpoints(token),
        ]);
        if (!agentId) return { agentList, modelList };
        const [agent, agentDraft, history, access, audit] = await Promise.all([
          getAdminAgent(token, agentId),
          getAgentDraft(token, agentId),
          listAgentVersions(token, agentId),
          listAgentGrants(token, agentId),
          listAgentAuditEvents(token, agentId),
        ]);
        return { agentList, modelList, agent, agentDraft, history, access, audit };
      });
      setAgents(result.value.agentList.items);
      setTotalAgents(result.value.agentList.total);
      setModels(result.value.modelList.items);
      if ("agent" in result.value && result.value.agent && result.value.agentDraft) {
        hydrateProfile(result.value.agent);
        hydrateDraft(result.value.agentDraft);
        setVersions(result.value.history?.items ?? []);
        setGrants(result.value.access?.items ?? []);
        setGrantRoles(
          (result.value.access?.items ?? [])
            .filter((grant) => grant.subject_type === "role")
            .map((grant) => grant.subject_id),
        );
        setGrantUserIds(
          (result.value.access?.items ?? [])
            .filter((grant) => grant.subject_type === "user")
            .map((grant) => grant.subject_id)
            .join("\n"),
        );
        setAuditEvents(result.value.audit?.items ?? []);
      } else {
        const firstActive = result.value.modelList.items.find(
          (model) => model.status === "active" && model.active_version_id,
        );
        if (firstActive) setModelEndpointId(firstActive.id);
      }
    } catch (cause) {
      setError(errorMessage(cause, "读取 Agent 配置失败"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), [agentId, page, statusFilter]);

  useEffect(() => {
    let cancelled = false;
    if (!modelEndpointId) {
      setSelectedModelVersions([]);
      return;
    }
    void withRefreshedToken((token) => listModelEndpointVersions(token, modelEndpointId))
      .then((result) => {
        if (!cancelled) setSelectedModelVersions(result.value.items);
      })
      .catch((cause) => {
        if (!cancelled) setError(errorMessage(cause, "读取模型能力失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [modelEndpointId]);

  function buildConfig(): AgentConfig {
    return {
      schema_version: "2",
      prompt: { system_prompt: prompt.trim() },
      model: {
        model_endpoint_id: modelEndpointId,
        fallback_model_endpoint_ids: [],
        generation: {
          temperature,
          max_output_tokens: maxOutputTokens,
          timeout_seconds: 60,
          max_retries: 2,
        },
      },
      tools: ticketLookupEnabled
        ? [{ tool_id: "support_ticket_lookup", enabled: true, max_calls_per_run: 4, approval_policy: "none" }]
        : [],
      runtime: {
        engine: "langchain_create_agent_v1",
        context_schema: "support_context_v1",
        response_schema: "support_answer_v1",
        response_strategy: "tool",
        checkpointer: "postgres",
        model_call_limit: modelCallLimit,
        tool_call_limit: toolCallLimit,
        run_timeout_seconds: runTimeoutSeconds,
        max_parallel_tools: 2,
        max_input_tokens: null,
        max_cost_usd: null,
      },
      knowledge: { knowledge_base_ids: [] },
      memory: { long_term_policy_id: null },
    };
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (!modelEndpointId) return;
    setBusy(true);
    resetActionFeedback();
    try {
      const result = await withRefreshedToken((token) =>
        createAdminAgent(token, {
          slug,
          name,
          logo_url: logoUrl.trim() || null,
          description,
          welcome_message: welcomeMessage || `你好，我是 ${name}。请描述你遇到的技术问题。`,
          suggested_prompts: suggestedPrompts.split("\n").map((item) => item.trim()).filter(Boolean),
          config: buildConfig(),
        }),
      );
      setCreateOpen(false);
      navigate(`/admin/agents/${result.value.id}`);
    } catch (cause) {
      reportActionError(cause, "创建 Agent 失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveProfile() {
    if (!agentId) return;
    setBusy(true);
    resetActionFeedback();
    try {
      const result = await withRefreshedToken((token) =>
        updateAgentProfile(token, agentId, {
          name: name.trim(),
          logo_url: logoUrl.trim() || null,
          description: description.trim(),
          welcome_message: welcomeMessage.trim(),
          suggested_prompts: suggestedPrompts.split("\n").map((item) => item.trim()).filter(Boolean),
        }),
      );
      hydrateProfile(result.value);
      setMessage("基础信息已保存，不影响已发布运行版本。");
    } catch (cause) {
      reportActionError(cause, "保存基础信息失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft() {
    if (!agentId || !draft) return;
    setBusy(true);
    resetActionFeedback();
    try {
      const result = await withRefreshedToken((token) =>
        updateAgentDraft(token, agentId, draft.revision, buildConfig()),
      );
      hydrateDraft(result.value);
      setMessage(`Agent 配置草稿已保存，revision ${result.value.revision}。`);
    } catch (cause) {
      reportActionError(cause, "保存草稿失败");
    } finally {
      setBusy(false);
    }
  }

  async function validate() {
    if (!agentId) return;
    setBusy(true);
    resetActionFeedback();
    try {
      const result = await withRefreshedToken((token) =>
        validateAgentDraft(token, agentId, buildConfig()),
      );
      setMessage(
        `配置校验通过，将固定模型版本 ${result.value.model_endpoint_version_id.slice(0, 8)}…，凭据 revision ${result.value.credential_revision}。`,
      );
    } catch (cause) {
      reportActionError(cause, "配置校验失败");
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (!agentId || !draft) return;
    setBusy(true);
    resetActionFeedback();
    try {
      const result = await withRefreshedToken(async (token) => {
        await validateAgentDraft(token, agentId, buildConfig());
        const currentDraft = await updateAgentDraft(token, agentId, draft.revision, buildConfig());
        return publishAgent(token, agentId, currentDraft.revision, true, releaseNotes.trim());
      });
      setMessage(`v${result.value.version_number} 已发布并启动。`);
      setReleaseNotes("");
      await load();
    } catch (cause) {
      reportActionError(cause, "发布 Agent 失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveAccess() {
    if (!agentId) return;
    setBusy(true);
    resetActionFeedback();
    try {
      const userIds = grantUserIds.split("\n").map((item) => item.trim()).filter(Boolean);
      const result = await withRefreshedToken((token) =>
        replaceAgentAccessGrants(token, agentId, [
          ...grantRoles.map((subjectId) => ({ subject_type: "role" as const, subject_id: subjectId })),
          ...userIds.map((subjectId) => ({ subject_type: "user" as const, subject_id: subjectId })),
        ]),
      );
      setGrants(result.value.items);
      setMessage("使用授权已保存。撤权后，历史会话仍可读，但不能继续发送消息。");
      await load();
    } catch (cause) {
      reportActionError(cause, "保存使用授权失败");
    } finally {
      setBusy(false);
    }
  }

  function toggleGrantRole(role: string, checked: boolean) {
    setGrantRoles((current) => checked ? [...new Set([...current, role])] : current.filter((item) => item !== role));
  }

  async function activate(version: AgentVersion) {
    if (!agentId) return;
    setBusy(true);
    resetActionFeedback();
    try {
      await withRefreshedToken((token) => activateAgentVersion(token, agentId, version.id));
      setMessage(`已切换到 v${version.version_number}；已有会话仍使用原固定版本。`);
      await load();
    } catch (cause) {
      reportActionError(cause, "切换版本失败");
    } finally {
      setBusy(false);
    }
  }

  async function turnOff() {
    if (!agentId || !window.confirm("停用后将阻止新会话和新消息，历史会话仍可只读。确认停用？")) return;
    setBusy(true);
    resetActionFeedback();
    try {
      await withRefreshedToken((token) => disableAgent(token, agentId));
      setMessage("Agent 已停用，历史会话仍保留。重新激活任一版本即可恢复使用。");
      await load();
    } catch (cause) {
      reportActionError(cause, "停用 Agent 失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title={selected?.name ?? "Agent 管理"} description="配置 Agent 基础信息、模型、提示词、工具、权限与版本。" />
      <div aria-live="polite">
        {error && <div className="mt-5 flex flex-wrap items-center gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert"><CircleAlert className="size-4 shrink-0" /><span className="min-w-0 flex-1">{error}</span>{conflictRevision !== null ? <><Button size="sm" variant="outline" onClick={() => void copyCurrentConfig()}>复制当前配置</Button><Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>加载服务端 r{conflictRevision}</Button></> : <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>重试</Button>}</div>}
        {message && <div className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">{message}</div>}
      </div>
      {agentId ? (
        loading || !selected || !draft ? <RefreshCw className="mt-12 size-5 animate-spin" aria-label="加载 Agent" /> : (
          <fieldset disabled={selected.read_only} className="mt-8 space-y-6 disabled:opacity-75">
            {selected.read_only && <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">这是升级时生成的历史数据占位 Agent，仅用于读取旧会话，不能修改、发布、授权或激活。</div>}
            <section className="rounded-3xl border bg-white p-6">
              <div className="flex items-center justify-between gap-4">
                <div><h2 className="font-semibold">基础信息</h2><p className="mt-1 text-xs text-muted-foreground">用于员工目录和聊天工作台，可独立于运行版本更新。</p></div>
                <Button variant="outline" onClick={() => void saveProfile()} disabled={busy || !name.trim()}><Save />保存信息</Button>
              </div>
              <div className="mt-6 grid gap-4 md:grid-cols-2">
                <label className="text-sm">名称<Input className="mt-2" value={name} onChange={(event) => setName(event.target.value)} /></label>
                <label className="text-sm">Slug<Input className="mt-2" value={slug} disabled /></label>
                <label className="text-sm md:col-span-2">Logo URL<Input className="mt-2" type="url" value={logoUrl} onChange={(event) => setLogoUrl(event.target.value)} placeholder="https://…" /></label>
                <label className="text-sm md:col-span-2">描述<Textarea className="mt-2 min-h-20" value={description} onChange={(event) => setDescription(event.target.value)} /></label>
                <label className="text-sm md:col-span-2">欢迎语<Textarea className="mt-2 min-h-20" value={welcomeMessage} onChange={(event) => setWelcomeMessage(event.target.value)} /></label>
                <label className="text-sm md:col-span-2">建议问题（每行一个）<Textarea className="mt-2 min-h-24" value={suggestedPrompts} onChange={(event) => setSuggestedPrompts(event.target.value)} /></label>
              </div>
            </section>

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
              <section className="rounded-3xl border bg-white p-6">
                <div className="flex items-center justify-between gap-4">
                  <div><h2 className="font-semibold">Agent 配置</h2><p className="mt-1 text-xs text-muted-foreground">草稿 revision {draft.revision} · 发布时固定模型与工具版本</p></div>
                  <Button variant="outline" onClick={() => void saveDraft()} disabled={busy || !prompt.trim() || !modelEndpointId}><Save />保存草稿</Button>
                </div>
                <label className="mt-6 block text-sm font-medium" htmlFor="system-prompt">System Prompt</label>
                <Textarea id="system-prompt" className="mt-2 min-h-56" value={prompt} onChange={(event) => setPrompt(event.target.value)} aria-describedby={`prompt-help${issueFor("prompt.system_prompt") ? " prompt-error" : ""}`} aria-invalid={Boolean(issueFor("prompt.system_prompt"))} />
                <p id="prompt-help" className="mt-2 text-xs text-muted-foreground">禁止写入 API Key、Authorization 或其他秘密；发布时服务端会再次检查。</p>
                {issueFor("prompt.system_prompt") && <p id="prompt-error" className="mt-1 text-xs text-destructive">{issueFor("prompt.system_prompt")}</p>}

                <div className="mt-7 border-t pt-6">
                  <h3 className="text-sm font-semibold">模型绑定</h3>
                  <select aria-label="Agent 使用的模型端点" className="mt-3 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={modelEndpointId} onChange={(event) => setModelEndpointId(event.target.value)}>
                    <option value="">选择已验证模型端点</option>
                    {activeModels.map((model) => <option key={model.id} value={model.id}>{model.name} · revision {model.credential_revision}</option>)}
                  </select>
                  {selectedModel && <p className="mt-2 text-xs text-muted-foreground">将固定当前活动版本 {selectedModel.active_version_id?.slice(0, 8)}…，后续端点编辑不会改变已发布 Agent。</p>}
                  {selectedModelVersion && (
                    <div className="mt-3 rounded-2xl border bg-muted/30 p-4 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{selectedModelVersion.remote_model_name}</span>
                        <span className="rounded-full border bg-white px-2 py-0.5">{selectedModelVersion.api_protocol}</span>
                        <span className="rounded-full border bg-white px-2 py-0.5">{selectedModelVersion.verification_status}</span>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2" aria-label="已验证模型能力">
                        {Object.entries(selectedModelVersion.capabilities).map(([capability, supported]) => (
                          <span key={capability} className={`rounded-full px-2 py-1 ${supported ? "bg-emerald-100 text-emerald-800" : "bg-muted text-muted-foreground"}`}>
                            {capability}: {supported ? "支持" : "不支持"}
                          </span>
                        ))}
                      </div>
                      {typeof selectedModelVersion.defaults.max_output_tokens === "number" && <p className="mt-3 text-muted-foreground">模型最大输出：{selectedModelVersion.defaults.max_output_tokens} tokens</p>}
                    </div>
                  )}
                  {issueFor("model.model_endpoint_id", "runtime.response_strategy") && <p className="mt-2 text-xs text-destructive">{issueFor("model.model_endpoint_id", "runtime.response_strategy")}</p>}
                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <label className="text-sm">Temperature<Input className="mt-2" type="number" min="0" max="2" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.valueAsNumber)} aria-invalid={Boolean(issueFor("model.generation.temperature"))} />{issueFor("model.generation.temperature") && <span className="mt-1 block text-xs text-destructive">{issueFor("model.generation.temperature")}</span>}</label>
                    <label className="text-sm">最大输出 Token<Input className="mt-2" type="number" min="1" max="128000" value={maxOutputTokens} onChange={(event) => setMaxOutputTokens(event.target.valueAsNumber)} aria-invalid={Boolean(issueFor("model.generation.max_output_tokens"))} />{issueFor("model.generation.max_output_tokens") && <span className="mt-1 block text-xs text-destructive">{issueFor("model.generation.max_output_tokens")}</span>}</label>
                  </div>
                </div>

                <div className="mt-7 border-t pt-6">
                  <h3 className="text-sm font-semibold">工具绑定</h3>
                  {toolCatalog.map((tool) => <label key={tool.id} className="mt-3 flex cursor-pointer items-start gap-3 rounded-2xl border p-4"><input type="checkbox" className="mt-1 size-4 accent-black" checked={ticketLookupEnabled} onChange={(event) => setTicketLookupEnabled(event.target.checked)} /><span><span className="block text-sm font-medium">{tool.name}</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">{tool.description}</span></span></label>)}
                  {issueFor("tools", "tools.0.tool_id", "tools.0.approval_policy") && <p className="mt-2 text-xs text-destructive">{issueFor("tools", "tools.0.tool_id", "tools.0.approval_policy")}</p>}
                </div>

                <div className="mt-7 border-t pt-6">
                  <h3 className="text-sm font-semibold">运行上限</h3>
                  <div className="mt-3 grid gap-4 sm:grid-cols-3">
                    <label className="text-sm">模型调用<Input className="mt-2" type="number" min="1" max="12" value={modelCallLimit} onChange={(event) => setModelCallLimit(event.target.valueAsNumber)} /></label>
                    <label className="text-sm">工具调用<Input className="mt-2" type="number" min="1" max="20" value={toolCallLimit} onChange={(event) => setToolCallLimit(event.target.valueAsNumber)} /></label>
                    <label className="text-sm">总超时（秒）<Input className="mt-2" type="number" min="10" max="600" value={runTimeoutSeconds} onChange={(event) => setRunTimeoutSeconds(event.target.valueAsNumber)} /></label>
                  </div>
                  {issueFor("runtime.model_call_limit", "runtime.tool_call_limit", "runtime.run_timeout_seconds") && <p className="mt-2 text-xs text-destructive">{issueFor("runtime.model_call_limit", "runtime.tool_call_limit", "runtime.run_timeout_seconds")}</p>}
                </div>
              </section>

              <aside className="space-y-6">
                <section className="rounded-3xl border bg-white p-6">
                  <div className="text-sm text-muted-foreground">运行状态</div>
                  <div className="mt-1 flex items-center gap-2 font-medium"><CheckCircle2 className="size-4" />{selected.status}</div>
                  <div className="mt-5 text-sm text-muted-foreground">当前活动版本</div>
                  <div className="mt-1 break-all text-xs">{selected.active_version_id ?? "尚未发布"}</div>
                  <Button variant="outline" className="mt-6 w-full" onClick={() => void validate()} disabled={busy}>校验配置</Button>
                  {publishBlockers.length > 0 && <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900" role="status"><div className="font-medium">发布前需要处理</div><ul className="mt-2 list-disc space-y-1 pl-4">{publishBlockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></div>}
                  <label className="mt-4 block text-sm">版本说明<Input className="mt-2" value={releaseNotes} onChange={(event) => setReleaseNotes(event.target.value)} placeholder="本次变更内容" /></label>
                  <Button className="mt-3 w-full" onClick={() => void publish()} disabled={busy || publishBlockers.length > 0}><Rocket />发布并启动</Button>
                  {selected.status === "active" && <Link className={buttonVariants({ variant: "outline", className: "mt-2 w-full" })} to={`/agents/${selected.id}/chat`}>打开工作台</Link>}
                  {selected.status !== "disabled" && <Button variant="ghost" className="mt-2 w-full text-destructive hover:text-destructive" onClick={() => void turnOff()} disabled={busy}>停用 Agent</Button>}
                </section>
                <section className="rounded-3xl border bg-white p-6">
                  <h3 className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="size-4" />使用授权</h3>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">默认拒绝。选择角色，或逐行填写当前租户用户 UUID。</p>
                  <div className="mt-3 space-y-2">{["employee", "agent_user", "customer"].map((role) => <label key={role} className="flex items-center gap-2 rounded-xl border px-3 py-2 text-xs"><input type="checkbox" checked={grantRoles.includes(role)} onChange={(event) => toggleGrantRole(role, event.target.checked)} className="size-4 accent-black" />角色：{role}</label>)}</div>
                  <label className="mt-3 block text-xs">用户 UUID（每行一个）<Textarea className="mt-2 min-h-20 font-mono text-xs" value={grantUserIds} onChange={(event) => setGrantUserIds(event.target.value)} /></label>
                  <Button variant="outline" className="mt-3 w-full" onClick={() => void saveAccess()} disabled={busy}>保存授权</Button>
                  <div className="mt-3 text-[11px] text-muted-foreground">当前共 {grants.length} 条授权。</div>
                </section>
              </aside>
            </div>

            <section className="grid gap-6 lg:grid-cols-2">
              <div className="rounded-3xl border bg-white p-6">
                <h2 className="flex items-center gap-2 font-semibold"><History className="size-4" />版本历史</h2>
                <div className="mt-4 space-y-3">{versions.length === 0 ? <p className="text-sm text-muted-foreground">尚未发布版本。</p> : versions.map((version) => <div key={version.id} className="flex items-center gap-3 rounded-2xl border p-4 text-sm"><div className="min-w-0 flex-1"><div className="font-medium">v{version.version_number}{selected.active_version_id === version.id ? " · 当前" : ""}</div><div className="mt-1 truncate text-xs text-muted-foreground">{version.release_notes || version.config_digest}</div></div>{selected.active_version_id !== version.id && <Button size="sm" variant="outline" onClick={() => void activate(version)} disabled={busy}>激活</Button>}</div>)}</div>
              </div>
              <div className="rounded-3xl border bg-white p-6">
                <h2 className="font-semibold">最近审计</h2>
                <div className="mt-4 space-y-3">{auditEvents.slice(0, 8).map((event) => <div key={event.id} className="text-sm"><div className="font-medium">{event.action}</div><div className="mt-1 text-xs text-muted-foreground">{new Date(event.created_at).toLocaleString()} · {event.correlation_id.slice(0, 8)}…</div></div>)}</div>
              </div>
            </section>
          </fieldset>
        )
      ) : (
        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_400px]">
          <section className="rounded-3xl border bg-white p-6">
            <div className="flex items-center justify-between gap-3"><h2 className="font-semibold">全部 Agent</h2><select className="h-9 rounded-lg border bg-white px-3 text-sm" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value as typeof statusFilter); setPage(0); }} aria-label="按状态筛选 Agent"><option value="">全部状态</option><option value="draft">draft</option><option value="active">active</option><option value="disabled">disabled</option></select></div>
            {loading ? <RefreshCw className="mt-8 size-5 animate-spin" /> : agents.length === 0 ? <p className="mt-6 text-sm text-muted-foreground">还没有 Agent。请先配置并验证模型端点。</p> : <div className="mt-4 divide-y">{agents.map((agent) => <div key={agent.id} className="flex items-center gap-4 py-4"><div className="brand-mark grid size-10 place-items-center overflow-hidden rounded-xl">{agent.logo_url ? <img src={agent.logo_url} alt="" className="size-full object-cover" /> : <Bot className="size-4" />}</div><div className="min-w-0 flex-1"><div className="truncate font-medium">{agent.name}</div><div className="mt-0.5 text-xs text-muted-foreground">{agent.slug} · {agent.status} · draft r{agent.draft_revision ?? "-"}</div><div className="mt-0.5 truncate text-[11px] text-muted-foreground">active {agent.active_version_id?.slice(0, 8) ?? "-"} · {new Date(agent.updated_at).toLocaleString()}</div></div><Link className={buttonVariants({ variant: "outline", size: "sm" })} to={`/admin/agents/${agent.id}`}>配置</Link></div>)}</div>}
            {!loading && totalAgents > 0 && <div className="mt-4 flex items-center justify-between border-t pt-4 text-xs text-muted-foreground"><span>第 {page * 20 + 1}–{Math.min((page + 1) * 20, totalAgents)} 条，共 {totalAgents} 条</span><div className="flex gap-2"><Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一页</Button><Button size="sm" variant="outline" disabled={(page + 1) * 20 >= totalAgents} onClick={() => setPage((value) => value + 1)}>下一页</Button></div></div>}
          </section>
          <section className="rounded-3xl border bg-white p-6"><h2 className="font-semibold">Agent 操作</h2><p className="mt-1 text-xs text-muted-foreground">创建草稿后，再进入详情配置版本和权限。</p><Button type="button" className="mt-6 w-full" onClick={() => { setCreateOpen(true); setError(null); setMessage(null); }} disabled={activeModels.length === 0}><Plus />新建 Agent 草稿</Button>{activeModels.length === 0 && <p className="mt-3 text-xs text-amber-700">请先配置并验证模型端点。</p>}</section>
        </div>
      )}
      <Dialog open={createOpen} onOpenChange={(open) => { if (!busy) setCreateOpen(open); }}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <form onSubmit={create}>
            <DialogHeader><DialogTitle>新建 Agent 草稿</DialogTitle><DialogDescription>先创建基础草稿，之后可继续配置模型、工具、权限和发布版本。</DialogDescription></DialogHeader>
            <label className="mt-5 block text-sm">名称<Input className="mt-2" value={name} onChange={(event) => setName(event.target.value)} required /></label>
            <label className="mt-4 block text-sm">Slug<Input className="mt-2" value={slug} onChange={(event) => setSlug(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} placeholder="technical-support" required /></label>
            <label className="mt-4 block text-sm">描述<Input className="mt-2" value={description} onChange={(event) => setDescription(event.target.value)} /></label>
            <label className="mt-4 block text-sm">模型端点<select className="mt-2 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={modelEndpointId} onChange={(event) => setModelEndpointId(event.target.value)} required><option value="">选择已验证模型</option>{activeModels.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
            <label className="mt-4 block text-sm">System Prompt<Textarea className="mt-2 min-h-32" value={prompt} onChange={(event) => setPrompt(event.target.value)} required /></label>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={busy}>取消</Button><Button type="submit" disabled={busy || activeModels.length === 0}>{busy ? "创建中…" : "创建 Agent 草稿"}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
