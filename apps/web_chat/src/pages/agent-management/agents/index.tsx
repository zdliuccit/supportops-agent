import { type FormEvent, useEffect, useMemo, useState } from "react";
import {
  Bot,
  CheckCircle2,
  CircleAlert,
  History,
  LoaderCircle,
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
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { FormField } from "@/components/FormField";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import { cn } from "@/lib/utils";
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

import { CreateAgentDialog } from "./components/CreateAgentDialog";

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

export function AgentManagementPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AdminAgent[]>([]);
  const [totalAgents, setTotalAgents] = useState(0);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });
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
  const [modelEndpointModelId, setModelEndpointModelId] = useState("");
  const [ticketLookupEnabled, setTicketLookupEnabled] = useState(false);
  const [temperature, setTemperature] = useState(0.2);
  const [maxOutputTokens, setMaxOutputTokens] = useState(4096);
  const [reasoningEffort, setReasoningEffort] = useState<"" | "none" | "low" | "medium" | "high" | "xhigh" | "max">("");
  const [verbosity, setVerbosity] = useState<"" | "low" | "medium" | "high">("");
  const [runTimeoutSeconds, setRunTimeoutSeconds] = useState(120);
  const [modelCallLimit, setModelCallLimit] = useState(6);
  const [toolCallLimit, setToolCallLimit] = useState(8);
  const [releaseNotes, setReleaseNotes] = useState("");
  const [grantRoles, setGrantRoles] = useState<string[]>([]);
  const [grantUserIds, setGrantUserIds] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldIssues, setFieldIssues] = useState<Record<string, string>>({});
  const [createOpen, setCreateOpen] = useState(false);

  const activeModels = useMemo(
    () => models.flatMap((endpoint) => endpoint.is_enabled
      ? endpoint.models
          .filter((model) => model.test_status === "passed" && model.current_version_id)
          .map((model) => ({ endpoint, model }))
      : []),
    [models],
  );
  const selectedModel = models.find((model) => model.id === modelEndpointId);
  const selectedEndpointModel = selectedModel?.models.find(
    (model) => model.id === modelEndpointModelId,
  );
  const selectedModelVersion = selectedModelVersions.find(
    (version) => version.id === selectedEndpointModel?.current_version_id,
  );
  const publishBlockers = useMemo(() => {
    const blockers: string[] = [];
    if (!prompt.trim()) blockers.push("System Prompt 不能为空");
    if (!selectedModel) blockers.push("必须选择模型端点");
    if (!selectedEndpointModel) blockers.push("必须选择具体模型");
    if (selectedModel && !selectedModel.is_enabled) blockers.push("模型端点未启用");
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
  }, [maxOutputTokens, prompt, selectedEndpointModel, selectedModel, selectedModelVersion]);

  function issueFor(...fields: string[]): string | undefined {
    return fields.map((field) => fieldIssues[field]).find(Boolean);
  }

  function resetActionFeedback() {
    setFieldIssues({});
  }

  function clearFieldIssue(field: string) {
    setFieldIssues((current) => {
      const next = { ...current };
      delete next[field];
      return next;
    });
  }

  function validateCreateField(field: "name" | "slug" | "prompt" | "model", value: string) {
    const message = value.trim() ? undefined : field === "name" ? "请输入 Agent 名称" : field === "slug" ? "请输入 Agent Slug" : field === "prompt" ? "请输入 System Prompt" : "请选择已验证模型";
    setFieldIssues((current) => {
      const next = { ...current };
      if (message) next[field] = message;
      else delete next[field];
      return next;
    });
  }

  function reportActionError(cause: unknown, fallback: string) {
    setFieldIssues(fieldIssueMap(cause));
    const revision = cause instanceof ApiError ? cause.details.current_revision : null;
    if (typeof revision === "number") {
      notify.warning(errorMessage(cause, fallback), {
        duration: 10_000,
        action: {
          label: "复制当前配置",
          onClick: () => void copyCurrentConfig(),
        },
      });
      return;
    }
    notify.error(new Error(errorMessage(cause, fallback)), fallback);
  }

  async function copyCurrentConfig() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(buildConfig(), null, 2));
      notify.success("当前未保存配置已复制，可以刷新后手动合并。");
    } catch (cause) {
      notify.error(cause, "复制当前配置失败");
    }
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
    setModelEndpointModelId(config.model.model_endpoint_model_id ?? "");
    setTicketLookupEnabled(
      config.tools.some((tool) => tool.tool_id === "support_ticket_lookup" && tool.enabled),
    );
    setTemperature(config.model.generation?.temperature ?? 0.2);
    setMaxOutputTokens(config.model.generation?.max_output_tokens ?? 4096);
    setReasoningEffort(config.model.generation?.reasoning_effort ?? "");
    setVerbosity(config.model.generation?.verbosity ?? "");
    setRunTimeoutSeconds(config.runtime.run_timeout_seconds ?? 120);
    setModelCallLimit(config.runtime.model_call_limit ?? 6);
    setToolCallLimit(config.runtime.tool_call_limit ?? 8);
  }

  async function load(nextPagination = pagination) {
    setLoading(true);
    setError(null);
    setFieldIssues({});
    try {
      const result = await withRefreshedToken(async (token) => {
        const [agentList, modelList] = await Promise.all([
          listAdminAgents(token, {
            page: nextPagination.current,
            pageSize: nextPagination.pageSize,
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
          (model) => model.is_enabled && model.models.some((item) => item.test_status === "passed"),
        );
        const firstModel = firstActive?.models.find((item) => item.test_status === "passed");
        if (firstActive && firstModel) {
          setModelEndpointId(firstActive.id);
          setModelEndpointModelId(firstModel.id);
        }
      }
    } catch (cause) {
      setError(errorMessage(cause, "读取 Agent 配置失败"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), [agentId, statusFilter]);

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
        model_endpoint_model_id: modelEndpointModelId || null,
        fallback_model_endpoint_ids: [],
        generation: {
          temperature,
          max_output_tokens: maxOutputTokens,
          reasoning_effort: reasoningEffort || null,
          verbosity: verbosity || null,
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
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = "请输入 Agent 名称";
    if (!slug.trim()) errors.slug = "请输入 Agent Slug";
    if (!modelEndpointId || !modelEndpointModelId) errors.model = "请选择已验证模型";
    if (!prompt.trim()) errors.prompt = "请输入 System Prompt";
    setFieldIssues(errors);
    if (Object.keys(errors).length > 0) return;
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
      resetCreateForm();
      navigate(`/agent-management/agents/${result.value.id}`);
      notify.success("Agent 已创建。");
    } catch (cause) {
      reportActionError(cause, "创建 Agent 失败");
    } finally {
      setBusy(false);
    }
  }

  function resetCreateForm() {
    setName("");
    setSlug("");
    setLogoUrl("");
    setDescription("");
    setWelcomeMessage("");
    setSuggestedPrompts("");
    setPrompt("");
    setModelEndpointId("");
    setModelEndpointModelId("");
    setReasoningEffort("");
    setVerbosity("");
    setFieldIssues({});
  }

  function closeCreateDialog() {
    if (busy) return;
    setCreateOpen(false);
    resetCreateForm();
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
      notify.success("基础信息已保存，不影响已发布运行版本。");
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
      notify.success(`Agent 配置草稿已保存，revision ${result.value.revision}。`);
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
      notify.success(
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
      notify.success(`v${result.value.version_number} 已发布并启动。`);
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
      notify.success("使用授权已保存。撤权后，历史会话仍可读，但不能继续发送消息。");
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
      notify.success(`已切换到 v${version.version_number}；已有会话仍使用原固定版本。`);
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
      notify.warning("Agent 已停用，历史会话仍保留。重新激活任一版本即可恢复使用。");
      await load();
    } catch (cause) {
      reportActionError(cause, "停用 Agent 失败");
    } finally {
      setBusy(false);
    }
  }

  const agentColumns: AppTableColumn<AdminAgent>[] = [
    {
      title: "Agent",
      key: "agent",
      width: "30%",
      render: (_, agent) => (
        <div className="flex min-w-0 items-center gap-3">
          <div className="brand-mark grid size-10 shrink-0 place-items-center overflow-hidden rounded-xl">
            {agent.logo_url ? <img src={agent.logo_url} alt="" className="size-full object-cover" /> : <Bot className="size-4" />}
          </div>
          <div className="min-w-0">
            <div className="truncate font-medium text-foreground">{agent.name}</div>
            <div className="mt-0.5 truncate text-xs text-muted-foreground">{agent.slug}</div>
          </div>
        </div>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: "16%",
      render: (_, agent) => {
        const statusLabel = agent.status === "active" ? "已启用" : agent.status === "disabled" ? "已停用" : "草稿";
        return <span className={cn("text-sm font-medium", agent.status === "active" ? "text-emerald-600" : agent.status === "disabled" ? "text-muted-foreground" : "text-amber-600")}>{statusLabel}</span>;
      },
    },
    {
      title: "版本",
      key: "version",
      width: "20%",
      render: (_, agent) => (
        <div className="text-sm">
          <div>草稿 r{agent.draft_revision ?? "-"}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">活动版本 {agent.active_version_id?.slice(0, 8) ?? "-"}</div>
        </div>
      ),
    },
    {
      title: "更新时间",
      key: "updated_at",
      width: "20%",
      render: (_, agent) => <span className="text-sm text-muted-foreground">{new Date(agent.updated_at).toLocaleString("zh-CN", { hour12: false })}</span>,
    },
    {
      title: "操作",
      key: "actions",
      align: "right",
      width: "14%",
      render: (_, agent) => <Link className={buttonVariants({ variant: "outline", size: "sm" })} to={`/agent-management/agents/${agent.id}`}>配置</Link>,
    },
  ];

  return (
    <>
      <PageHeader
        title={selected?.name ?? "Agent 管理"}
        description="配置 Agent 基础信息、模型、提示词、工具、权限与版本。"
        actions={!agentId ? <Button type="button" onClick={() => { resetCreateForm(); setCreateOpen(true); }} disabled={activeModels.length === 0}><Plus />新建 Agent</Button> : undefined}
      />
      <div aria-live="polite">
        {error && <Alert variant="destructive" className="mt-5"><CircleAlert className="size-4" /><AlertDescription className="flex flex-wrap items-center gap-2"><span className="min-w-0 flex-1">{error}</span><Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>重试</Button></AlertDescription></Alert>}
      </div>
      {agentId ? (
        loading || !selected || !draft ? <RefreshCw className="mt-12 size-5 animate-spin" aria-label="加载 Agent" /> : (
          <fieldset disabled={selected.read_only} className="mt-8 space-y-6 disabled:opacity-75">
            {selected.read_only && <Alert variant="warning" role="status"><AlertDescription>这是升级时生成的历史数据占位 Agent，仅用于读取旧会话，不能修改、发布、授权或激活。</AlertDescription></Alert>}
            <section className="rounded-3xl border bg-white p-6">
              <div className="flex items-center justify-between gap-4">
                <div><h2 className="font-semibold">基础信息</h2><p className="mt-1 text-xs text-muted-foreground">用于员工目录和聊天工作台，可独立于运行版本更新。</p></div>
                <Button variant="outline" onClick={() => void saveProfile()} disabled={busy || !name.trim()}><Save />保存信息</Button>
              </div>
              <div className="mt-6 grid gap-4 md:grid-cols-2">
                <FormField label="名称" htmlFor="agent-name" required><Input id="agent-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="请输入 Agent 名称" /></FormField>
                <Label className="text-sm">Slug<Input className="mt-2" value={slug} disabled placeholder="自动生成 Slug" /></Label>
                <Label className="text-sm md:col-span-2">Logo URL<Input className="mt-2" type="url" value={logoUrl} onChange={(event) => setLogoUrl(event.target.value)} placeholder="https://…" /></Label>
                <Label className="text-sm md:col-span-2">描述<Textarea className="mt-2 min-h-20" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="请输入 Agent 描述" /></Label>
                <Label className="text-sm md:col-span-2">欢迎语<Textarea className="mt-2 min-h-20" value={welcomeMessage} onChange={(event) => setWelcomeMessage(event.target.value)} /></Label>
                <Label className="text-sm md:col-span-2">建议问题（每行一个）<Textarea className="mt-2 min-h-24" value={suggestedPrompts} onChange={(event) => setSuggestedPrompts(event.target.value)} /></Label>
              </div>
            </section>

            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
              <section className="rounded-3xl border bg-white p-6">
                <div className="flex items-center justify-between gap-4">
                  <div><h2 className="font-semibold">Agent 配置</h2><p className="mt-1 text-xs text-muted-foreground">草稿 revision {draft.revision} · 发布时固定模型与工具版本</p></div>
                  <Button variant="outline" onClick={() => void saveDraft()} disabled={busy || !prompt.trim() || !modelEndpointModelId}><Save />保存草稿</Button>
                </div>
                <FormField label="System Prompt" htmlFor="system-prompt" required error={issueFor("prompt.system_prompt")} className="mt-6"><Textarea id="system-prompt" className="min-h-56" value={prompt} onChange={(event) => { setPrompt(event.target.value); clearFieldIssue("prompt.system_prompt"); }} placeholder="请输入 System Prompt" aria-describedby={`prompt-help${issueFor("prompt.system_prompt") ? " system-prompt-error" : ""}`} aria-invalid={Boolean(issueFor("prompt.system_prompt"))} /></FormField>
                <p id="prompt-help" className="mt-2 text-xs text-muted-foreground">禁止写入 API Key、Authorization 或其他秘密；发布时服务端会再次检查。</p>
                {issueFor("prompt.system_prompt") && <p id="prompt-error" className="mt-1 text-xs text-destructive">{issueFor("prompt.system_prompt")}</p>}

                <div className="mt-7 border-t pt-6">
                  <h3 className="text-sm font-semibold">模型绑定</h3>
                  <Select value={modelEndpointModelId || undefined} onValueChange={(value) => {
                    const option = activeModels.find((item) => item.model.id === value);
                    if (!option) return;
                    setModelEndpointId(option.endpoint.id);
                    setModelEndpointModelId(option.model.id);
                  }}>
                    <SelectTrigger className="mt-3" aria-label="Agent 使用的模型端点"><SelectValue placeholder="选择已验证模型端点" /></SelectTrigger>
                    <SelectContent>{activeModels.map(({ endpoint, model }) => <SelectItem key={model.id} value={model.id}>{endpoint.name} / {model.display_name || model.upstream_model_id}{model.badge ? ` ${model.badge}` : ""}</SelectItem>)}</SelectContent>
                  </Select>
                  {selectedEndpointModel && <p className="mt-2 text-xs text-muted-foreground">将固定模型版本 {selectedEndpointModel.current_version_id?.slice(0, 8)}…，后续端点编辑不会改变已发布 Agent。</p>}
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
                    <Label className="text-sm">Temperature<Input className="mt-2" type="number" min="0" max="2" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.valueAsNumber)} placeholder="请输入 Temperature" aria-invalid={Boolean(issueFor("model.generation.temperature"))} />{issueFor("model.generation.temperature") && <span className="mt-1 block text-xs text-destructive">{issueFor("model.generation.temperature")}</span>}</Label>
                    <Label className="text-sm">最大输出 Token<Input className="mt-2" type="number" min="1" max="128000" value={maxOutputTokens} onChange={(event) => setMaxOutputTokens(event.target.valueAsNumber)} placeholder="请输入最大输出 Token" aria-invalid={Boolean(issueFor("model.generation.max_output_tokens"))} />{issueFor("model.generation.max_output_tokens") && <span className="mt-1 block text-xs text-destructive">{issueFor("model.generation.max_output_tokens")}</span>}</Label>
                    <Label className="text-sm">推理强度<Select value={reasoningEffort || "__default__"} onValueChange={(value) => setReasoningEffort(value === "__default__" ? "" : value as typeof reasoningEffort)}><SelectTrigger className="mt-2"><SelectValue placeholder="继承模型默认值" /></SelectTrigger><SelectContent><SelectItem value="__default__">继承模型默认值</SelectItem>{["none", "low", "medium", "high", "xhigh", "max"].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></Label>
                    <Label className="text-sm">回答详细程度<Select value={verbosity || "__default__"} onValueChange={(value) => setVerbosity(value === "__default__" ? "" : value as typeof verbosity)}><SelectTrigger className="mt-2"><SelectValue placeholder="继承模型默认值" /></SelectTrigger><SelectContent><SelectItem value="__default__">继承模型默认值</SelectItem>{["low", "medium", "high"].map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></Label>
                  </div>
                </div>

                <div className="mt-7 border-t pt-6">
                  <h3 className="text-sm font-semibold">工具绑定</h3>
                  {toolCatalog.map((tool) => <Label key={tool.id} className="mt-3 flex cursor-pointer items-start gap-3 rounded-2xl border p-4 font-normal"><Checkbox className="mt-1" checked={ticketLookupEnabled} onCheckedChange={(value) => setTicketLookupEnabled(value === true)} /><span><span className="block text-sm font-medium">{tool.name}</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">{tool.description}</span></span></Label>)}
                  {issueFor("tools", "tools.0.tool_id", "tools.0.approval_policy") && <p className="mt-2 text-xs text-destructive">{issueFor("tools", "tools.0.tool_id", "tools.0.approval_policy")}</p>}
                </div>

                <div className="mt-7 border-t pt-6">
                  <h3 className="text-sm font-semibold">运行上限</h3>
                  <div className="mt-3 grid gap-4 sm:grid-cols-3">
                    <Label className="text-sm">模型调用<Input className="mt-2" type="number" min="1" max="12" value={modelCallLimit} onChange={(event) => setModelCallLimit(event.target.valueAsNumber)} placeholder="请输入模型调用上限" /></Label>
                    <Label className="text-sm">工具调用<Input className="mt-2" type="number" min="1" max="20" value={toolCallLimit} onChange={(event) => setToolCallLimit(event.target.valueAsNumber)} placeholder="请输入工具调用上限" /></Label>
                    <Label className="text-sm">总超时（秒）<Input className="mt-2" type="number" min="10" max="600" value={runTimeoutSeconds} onChange={(event) => setRunTimeoutSeconds(event.target.valueAsNumber)} placeholder="请输入总超时时间" /></Label>
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
                  {publishBlockers.length > 0 && <Alert variant="warning" role="status" className="mt-4"><AlertDescription className="text-xs"><div className="font-medium">发布前需要处理</div><ul className="mt-2 list-disc space-y-1 pl-4">{publishBlockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></AlertDescription></Alert>}
                  <Label className="mt-4 block text-sm">版本说明<Input className="mt-2" value={releaseNotes} onChange={(event) => setReleaseNotes(event.target.value)} placeholder="请输入本次变更内容" /></Label>
                  <Button className="mt-3 w-full" onClick={() => void publish()} disabled={busy || publishBlockers.length > 0}><Rocket />发布并启动</Button>
                  {selected.status === "active" && <Link className={buttonVariants({ variant: "outline", className: "mt-2 w-full" })} to={`/agents/${selected.id}/chat`}>打开工作台</Link>}
                  {selected.status !== "disabled" && <Button variant="ghost" className="mt-2 w-full text-destructive hover:text-destructive" onClick={() => void turnOff()} disabled={busy}>停用 Agent</Button>}
                </section>
                <section className="rounded-3xl border bg-white p-6">
                  <h3 className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="size-4" />使用授权</h3>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">默认拒绝。选择角色，或逐行填写当前租户用户 UUID。</p>
                  <div className="mt-3 space-y-2">{["employee", "agent_user", "customer"].map((role) => <Label key={role} className="flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-xs font-normal"><Checkbox checked={grantRoles.includes(role)} onCheckedChange={(value) => toggleGrantRole(role, value === true)} />角色：{role}</Label>)}</div>
                  <Label className="mt-3 block text-xs">用户 UUID（每行一个）<Textarea className="mt-2 min-h-20 font-mono text-xs" value={grantUserIds} onChange={(event) => setGrantUserIds(event.target.value)} /></Label>
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
        <section className="mt-7 overflow-hidden rounded-2xl bg-white">
          <div className="flex items-center justify-between gap-3 px-6 py-3">
            <div className="flex items-center gap-3">
              <h2 className="font-semibold">Agent 列表</h2>
              <Select value={statusFilter || "__all__"} onValueChange={(value) => {
                setStatusFilter(value === "__all__" ? "" : value as typeof statusFilter);
                setPagination((current) => ({ ...current, current: 1 }));
              }}>
                <SelectTrigger className="w-36" aria-label="按状态筛选 Agent"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="__all__">全部状态</SelectItem><SelectItem value="draft">草稿</SelectItem><SelectItem value="active">已启用</SelectItem><SelectItem value="disabled">已停用</SelectItem></SelectContent>
              </Select>
            </div>
            <Button variant="ghost" size="icon" onClick={() => void load()} disabled={loading} aria-label="刷新 Agent 列表"><RefreshCw className={cn("size-4", loading && "animate-spin")} /></Button>
          </div>
          {loading ? <div className="grid h-52 place-items-center"><LoaderCircle className="size-5 animate-spin text-muted-foreground" aria-label="加载 Agent" /></div> : <AppTable
            columns={agentColumns}
            dataSource={agents}
            rowKey="id"
            pagination={{
              current: pagination.current,
              pageSize: pagination.pageSize,
              total: totalAgents,
              onChange: (current, pageSize) => {
                const nextPagination = { current, pageSize };
                setPagination(nextPagination);
                void load(nextPagination);
              },
            }}
            emptyText="还没有 Agent。请先配置并验证模型端点。"
            ariaLabel="Agent 列表"
          />}
        </section>
      )}
      <CreateAgentDialog open={createOpen} busy={busy} name={name} slug={slug} description={description} prompt={prompt} selectedModelId={modelEndpointModelId} models={activeModels} errors={fieldIssues} onOpenChange={(open) => { if (open) setCreateOpen(true); else closeCreateDialog(); }} onSubmit={create} onNameChange={(value) => { setName(value); clearFieldIssue("name"); }} onSlugChange={(value) => { setSlug(value.toLowerCase().replace(/[^a-z0-9-]/g, "-")); clearFieldIssue("slug"); }} onDescriptionChange={setDescription} onPromptChange={(value) => { setPrompt(value); clearFieldIssue("prompt"); }} onModelChange={({ endpoint, model }) => { setModelEndpointId(endpoint.id); setModelEndpointModelId(model.id); clearFieldIssue("model"); }} onValidate={validateCreateField} onCancel={closeCreateDialog} />
    </>
  );
}
