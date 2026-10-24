import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { getAdminAgent, getAgentDraft, listAdminAgents, resolveRoutingConfig } from "@/api";
import { FormField } from "@/components/FormField";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { SearchableSelect } from "@/components/SearchableSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { delayRequest } from "@/lib/delayRequest";
import type { AgentDraft, RoutingConfigResolutionView, RoutingConfigResolveRequest, RoutingPolicyVersionView } from "@/types";
import { requireResolution, uuid, type AuthenticatedRead } from "../contracts";
import { RoutingPreviewResult } from "./RoutingPreviewResult";

type Source = { agentId: string; baseVersionId: string; baseDigest: string; draft: AgentDraft };
type Field = "agent" | "input" | "output" | "cost";
const message = (cause: unknown) => cause instanceof Error ? cause.message : "元数据预览失败，请重新读取配置。";
const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);

export function RoutingPreviewForm({ policy, request }: { policy: RoutingPolicyVersionView; request: AuthenticatedRead }) {
  const definition = policy.record.definition;
  const [agentId, setAgentId] = useState(""), [source, setSource] = useState<Source | null>(null);
  const [input, setInput] = useState(""), [output, setOutput] = useState("4096"), [cost, setCost] = useState("");
  const [purpose, setPurpose] = useState(definition.allowed_purposes[0]);
  const [classification, setClassification] = useState(definition.allowed_data_classifications[0]);
  const [touched, setTouched] = useState<Set<Field>>(new Set());
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null), [result, setResult] = useState<RoutingConfigResolutionView | null>(null);
  const [expired, setExpired] = useState(Date.parse(definition.valid_until) <= Date.now());
  const live = useRef(true), sourceSequence = useRef(0), previewSequence = useRef(0), previewLock = useRef(false);

  useEffect(() => {
    live.current = true;
    const timer = window.setInterval(() => {
      if (Date.parse(definition.valid_until) <= Date.now()) {
        window.clearInterval(timer); previewSequence.current += 1; setExpired(true); setResult(null);
      }
    }, 1000);
    return () => { live.current = false; window.clearInterval(timer); sourceSequence.current += 1; previewSequence.current += 1; };
  }, [definition.valid_until]);

  const loadOptions = useCallback(async (keywords: string, pageSize: number) => {
    const page = await request((token) => listAdminAgents(token, { page: 1, pageSize, keywords, status: "active" }));
    if (!live.current) return [];
    if (!Array.isArray(page.items) || page.items.length > pageSize || page.page !== 1 || page.page_size !== pageSize
      || !Number.isSafeInteger(page.total) || page.total < 0 || page.pages !== Math.ceil(page.total / pageSize)
      || page.items.some((row) => !row || !uuid(row.id) || typeof row.name !== "string" || row.status !== "active"
        || typeof row.read_only !== "boolean" || row.active_version_id !== null && !uuid(row.active_version_id))
      || new Set(page.items.map((row) => row.id)).size !== page.items.length) throw new Error("Agent 列表契约无法确认。");
    return page.items.filter((row) => !row.read_only && row.active_version_id).map((row) => ({ value: row.id, label: row.name }));
  }, [request]);

  function edit(operation: () => void, field?: Field) {
    if (previewLock.current) return;
    previewSequence.current += 1; setResult(null); setError(null);
    if (field) setTouched((current) => new Set(current).add(field));
    operation();
  }

  async function loadAgent(id: string) {
    if (previewLock.current) return;
    const sequence = ++sourceSequence.current;
    previewSequence.current += 1;
    setAgentId(id); setSource(null); setResult(null); setError(null); setTouched(new Set());
    setInput(""); setCost(""); setOutput("4096"); setLoading(Boolean(id));
    if (!id) return;
    if (!uuid(id)) { setLoading(false); setError("Agent 标识无法确认，请重新选择。"); return; }
    try {
      const [agent, draft] = await delayRequest(() => {
        if (!live.current || sequence !== sourceSequence.current) throw new Error("读取已取消");
        return Promise.all([request((token) => getAdminAgent(token, id)), request((token) => getAgentDraft(token, id))]);
      });
      if (!live.current || sequence !== sourceSequence.current) return;
      if (agent.id !== id || agent.read_only !== false || agent.status !== "active" || !uuid(agent.active_version_id) || !digest(agent.active_version_config_digest)
        || draft.agent_id !== id || draft.schema_version !== "2" || !draft.config || draft.config.schema_version !== "2"
        || !Number.isSafeInteger(draft.revision) || draft.revision < 1 || !digest(draft.config_digest)
        || !draft.config.model || !draft.config.runtime || !draft.config.prompt || !Array.isArray(draft.config.tools)) {
        throw new Error("需要当前可管理的已发布 v2 Agent 及完整草稿，请刷新后重选。");
      }
      setSource({ agentId: id, baseVersionId: agent.active_version_id, baseDigest: agent.active_version_config_digest, draft });
      setInput(draft.config.runtime.max_input_tokens?.toString() ?? "");
      setCost(draft.config.runtime.max_cost_usd?.toString() ?? "");
      setOutput(String(draft.config.model.generation?.max_output_tokens ?? 4096));
    } catch (cause) { if (live.current && sequence === sourceSequence.current) setError(message(cause)); }
    finally { if (live.current && sequence === sourceSequence.current) setLoading(false); }
  }

  const errors: Partial<Record<Field, string>> = {};
  if (!agentId || !source || source.agentId !== agentId) errors.agent = "请选择并读取已发布的 Agent 配置。";
  if (!input.trim() || !Number.isSafeInteger(Number(input)) || Number(input) < 256 || Number(input) > 2_000_000) errors.input = "请输入 256–2000000 之间的整数。";
  if (!output.trim() || !Number.isSafeInteger(Number(output)) || Number(output) < 1 || Number(output) > 128_000) errors.output = "请输入 1–128000 之间的整数。";
  if (!cost.trim() || !Number.isFinite(Number(cost)) || Number(cost) <= 0 || Number(cost) > 10_000) errors.cost = "请输入大于 0 且不超过 10000 的 USD 金额。";
  const fieldError = (field: Field) => touched.has(field) ? errors[field] : undefined;
  const disabled = busy || loading || expired || policy.status !== "candidate";

  async function preview(event: FormEvent) {
    event.preventDefault();
    if (previewLock.current || loading || !live.current) return;
    setTouched(new Set(["agent", "input", "output", "cost"]));
    if (Object.keys(errors).length || !source) return;
    if (Date.parse(definition.valid_until) <= Date.now() || policy.status !== "candidate") { setExpired(true); setResult(null); return; }
    const command: RoutingConfigResolveRequest = {
      agent_id: source.agentId, base_agent_version_id: source.baseVersionId, expected_base_config_digest: source.baseDigest,
      config: { ...source.draft.config, schema_version: "3", model: { mode: "versioned_routing_v1",
        policy_version_id: policy.record.version_id, policy_digest: policy.policy_digest,
        purpose, allowed_regions: [...definition.allowed_regions], data_classification: classification,
        generation: { ...source.draft.config.model.generation, max_output_tokens: Number(output), max_retries: 0 } },
        runtime: { ...source.draft.config.runtime, max_input_tokens: Number(input), max_cost_usd: Number(cost) } },
    };
    previewLock.current = true;
    const sequence = ++previewSequence.current;
    setBusy(true); setError(null); setResult(null);
    try {
      const raw = await delayRequest(() => {
        if (!live.current || sequence !== previewSequence.current) throw new Error("预览已取消");
        return request((token) => resolveRoutingConfig(token, command));
      });
      if (!live.current || sequence !== previewSequence.current) return;
      if (Date.parse(definition.valid_until) <= Date.now()) { setExpired(true); return; }
      setResult(requireResolution(raw, policy, command));
    } catch (cause) { if (live.current && sequence === previewSequence.current) setError(message(cause)); }
    finally { previewLock.current = false; if (live.current) setBusy(false); }
  }

  return <section className="space-y-4" aria-label="路由配置预览">
    <h3 className="font-medium">费用与兼容性预览</h3>
    <p className="text-sm text-muted-foreground">使用所选 Agent 的当前已保存草稿预览完整候选。只读取元数据，不运行模型、不保存草稿或候选，不预留费用。</p>
    {(expired || policy.status !== "candidate") && <p role="status" className="text-sm">原策略已过期或撤销，预览已禁用；请刷新原版本。</p>}
    <form noValidate onSubmit={(event) => void preview(event)} className="space-y-4">
      <FormField label="Agent" htmlFor="routing-preview-agent" required error={fieldError("agent")}>
        <SearchableSelect id="routing-preview-agent" ariaLabel="选择预览 Agent" ariaInvalid={Boolean(fieldError("agent"))}
          ariaDescribedBy={fieldError("agent") ? "routing-preview-agent-error" : undefined}
          value={agentId} onValueChange={(value) => void loadAgent(value)} loadOptions={loadOptions}
          placeholder="请选择已发布的 Agent" searchPlaceholder="请输入 Agent 名称" disabled={disabled} clearLabel="清除 Agent" />
      </FormField>
      {agentId && <Button variant="outline" size="sm" disabled={busy || loading || expired} onClick={() => void loadAgent(agentId)}>重新读取草稿配置</Button>}
      {loading && <div className="relative min-h-24"><ListLoadingOverlay label="正在读取 Agent 配置…" /></div>}
      {source && <p className="break-all text-xs text-muted-foreground">草稿修订 {source.draft.revision}；固定解析锚点 {source.baseVersionId}。当前显示本次读取的配置，其他管理员修改后需重新读取。</p>}
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="用途" htmlFor="routing-preview-purpose" required><Select value={purpose} disabled={disabled}
          onValueChange={(value) => edit(() => setPurpose(value))}><SelectTrigger id="routing-preview-purpose"><SelectValue /></SelectTrigger>
          <SelectContent>{definition.allowed_purposes.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></FormField>
        <FormField label="数据分类" htmlFor="routing-preview-classification" required><Select value={classification} disabled={disabled}
          onValueChange={(value) => edit(() => setClassification(value))}><SelectTrigger id="routing-preview-classification"><SelectValue /></SelectTrigger>
          <SelectContent>{definition.allowed_data_classifications.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select></FormField>
        <FormField label="完整输入上限（Token）" htmlFor="routing-preview-input" required error={fieldError("input")}><Input id="routing-preview-input" type="number"
          value={input} disabled={disabled} placeholder="请输入完整输入 Token 上限" onChange={(event) => edit(() => setInput(event.target.value), "input")}
          aria-invalid={Boolean(fieldError("input"))} aria-describedby={fieldError("input") ? "routing-preview-input-error" : undefined} /></FormField>
        <FormField label="输出上限（Token）" htmlFor="routing-preview-output" required error={fieldError("output")}><Input id="routing-preview-output" type="number"
          value={output} disabled={disabled} placeholder="请输入输出 Token 上限" onChange={(event) => edit(() => setOutput(event.target.value), "output")}
          aria-invalid={Boolean(fieldError("output"))} aria-describedby={fieldError("output") ? "routing-preview-output-error" : undefined} /></FormField>
        <FormField label="Run 费用上限（USD）" htmlFor="routing-preview-cost" required error={fieldError("cost")}><Input id="routing-preview-cost" type="number" step="any"
          value={cost} disabled={disabled} placeholder="请输入 Run 费用上限" onChange={(event) => edit(() => setCost(event.target.value), "cost")}
          aria-invalid={Boolean(fieldError("cost"))} aria-describedby={fieldError("cost") ? "routing-preview-cost-error" : undefined} /></FormField>
      </div>
      <p className="text-sm">本次允许处理地域：{definition.allowed_regions.join("、")}。数据批准缺失、能力未确认或价格未知都会显示为阻断项。</p>
      {error && <p role="alert" className="text-sm text-red-600">{error} 请重新读取配置后重试。</p>}
      <Button type="submit" disabled={disabled} aria-describedby="routing-preview-limits">{busy ? "正在预览元数据…" : "预览兼容性与费用"}</Button>
      <p id="routing-preview-limits" className="text-xs text-muted-foreground">所有预览结果均不授予发布、激活或执行许可。当前无法启用路由，正式发布仍受独立评测门禁控制。</p>
    </form>
    {result && <RoutingPreviewResult value={result} />}
  </section>;
}
