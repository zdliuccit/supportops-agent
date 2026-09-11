import { type FormEvent, useEffect, useState } from "react";
import {
  CircleAlert,
  LoaderCircle,
  Pencil,
  Power,
  PowerOff,
  Plus,
  RefreshCw,
  Server,
  Trash2,
  Wifi,
} from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import {
  createModelEndpoint,
  deleteModelEndpoint,
  disableModelEndpoint,
  discoverModels,
  enableModelEndpoint,
  getModelEndpoint,
  getModelTestRun,
  listModelEndpoints,
  listModelProviderPresets,
  saveModelEndpointConfiguration,
  startModelTest,
} from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import { cn } from "@/lib/utils";
import type { ModelEndpoint, ModelEndpointModel, ModelProviderPreset, ModelTestRun } from "@/types";

import { ModelConnectionFormDialog } from "./components/ModelConnectionFormDialog";
import { ModelTestDialog } from "./components/ModelTestDialog";
import { ProviderPresetDialog } from "./components/ProviderPresetDialog";
import { ModelActionButton } from "./components/ModelActionButton";
import {
  elapsed,
  modelPayload,
  extensionOptionsError,
  terminalTestStatuses,
  testStatusMeta,
  toEditableModel,
  type EditableModel,
} from "./components/modelTypes";

const officialUrl = "https://api.openai.com/v1";
const maskedApiKeyValue = "••••••••••••";

export function ModelManagementPage() {
  const { modelId } = useParams();
  const navigate = useNavigate();
  const [models, setModels] = useState<ModelEndpoint[]>([]);
  const [totalModels, setTotalModels] = useState(0);
  const [presets, setPresets] = useState<ModelProviderPreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });
  const [createOpen, setCreateOpen] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);
  const [editing, setEditing] = useState<ModelEndpoint | null>(null);
  const [providerPreset, setProviderPreset] = useState<string | null>(null);
  const [providerKind, setProviderKind] = useState<"openai_official" | "openai_compatible">("openai_compatible");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyMasked, setApiKeyMasked] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [modelInput, setModelInput] = useState("");
  const [editableModels, setEditableModels] = useState<EditableModel[]>([]);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [discoveryBusy, setDiscoveryBusy] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  const [testEndpoint, setTestEndpoint] = useState<ModelEndpoint | null>(null);
  const [testModelId, setTestModelId] = useState("");
  const [testRun, setTestRun] = useState<ModelTestRun | null>(null);
  const [pendingEnable, setPendingEnable] = useState(false);

  async function load(nextPagination = pagination) {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken(async (token) => {
        const [list, providerPresets] = await Promise.all([
          listModelEndpoints(token, { page: nextPagination.current, pageSize: nextPagination.pageSize }),
          listModelProviderPresets(token),
        ]);
        const selected = modelId ? await getModelEndpoint(token, modelId) : null;
        return { list, providerPresets, selected };
      });
      setModels(result.value.list.items);
      setTotalModels(result.value.list.total);
      setPresets(result.value.providerPresets);
      if (result.value.selected) hydrateEditor(result.value.selected);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取模型配置失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), [modelId]);

  function resetEditor() {
    setEditing(null);
    setStep(1);
    setProviderPreset(null);
    setProviderKind("openai_compatible");
    setName("");
    setBaseUrl("");
    setApiKey("");
    setApiKeyMasked(false);
    setShowKey(false);
    setModelInput("");
    setEditableModels([]);
    setFormErrors({});
    setDiscoveredModels([]);
    setDiscoveryOpen(false);
  }

  function hydrateEditor(endpoint: ModelEndpoint) {
    setEditing(endpoint);
    setStep(2);
    setProviderPreset(endpoint.provider_preset);
    setProviderKind(endpoint.provider_kind ?? "openai_compatible");
    setName(endpoint.name);
    setBaseUrl(endpoint.base_url ?? "");
    setApiKey(endpoint.credential_masked_hint ? maskedApiKeyValue : "");
    setApiKeyMasked(Boolean(endpoint.credential_masked_hint));
    setEditableModels(endpoint.models.map(toEditableModel));
    setFormErrors({});
  }

  function closeEditor(force = false) {
    if (busy && !force) return;
    setCreateOpen(false);
    resetEditor();
    if (modelId) navigate("/agent-management/models", { replace: true });
  }

  function selectPreset(preset: ModelProviderPreset | null) {
    if (preset === null) {
      setProviderPreset(null);
      setProviderKind("openai_compatible");
      setName("");
      setBaseUrl("");
    } else {
      setProviderPreset(preset.id);
      setProviderKind(preset.id === "openai" ? "openai_official" : "openai_compatible");
      setName(preset.name);
      setBaseUrl(preset.base_url);
    }
    setApiKey("");
    setApiKeyMasked(false);
    setEditableModels([]);
    setStep(2);
  }

  function addLocalModel(value = modelInput) {
    const modelIdValue = value.trim();
    if (!modelIdValue) return;
    setEditableModels((current) => current.some((item) => item.upstream_model_id === modelIdValue)
      ? current
      : [...current, {
          upstream_model_id: modelIdValue,
          extension_options: "{}",
          test_status: "untested",
        }]);
    setModelInput("");
    setFormErrors((current) => ({ ...current, models: "" }));
  }

  function updateEditableModel(index: number, patch: Partial<EditableModel>) {
    setEditableModels((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  async function testModelFromForm(index: number) {
    if (!editableModels[index]) return;
    if (!validateConfiguration()) return;
    setBusy(true);
    try {
      const payload = {
        name: name.trim(),
        provider_preset: providerPreset,
        provider_kind: providerKind,
        base_url: baseUrl.trim() || officialUrl,
        api_key: apiKeyMasked ? undefined : apiKey.trim() || undefined,
        models: editableModels.map(modelPayload),
        is_enabled: false,
      };
      const result = await withRefreshedToken((token) => editing
        ? saveModelEndpointConfiguration(token, editing.id, payload)
        : createModelEndpoint(token, payload));
      setEditing(result.value);
      setEditableModels(result.value.models.map(toEditableModel));
      setTestEndpoint(result.value);
      const sourceModel = editableModels[index];
      const target = result.value.models.find((item) => item.upstream_model_id === sourceModel.upstream_model_id)
        ?? result.value.models[index];
      if (!target) return;
      setTestModelId(target.id);
      setTestRun(target.latest_test);
      setTestOpen(true);
      notify.success("配置已保存，可以开始测试该模型。");
    } catch (cause) {
      notify.error(cause, "保存测试配置失败");
    } finally {
      setBusy(false);
    }
  }

  async function fetchModels() {
    const errors: Record<string, string> = {};
    if (!baseUrl.trim()) errors.baseUrl = "请输入 Base URL";
    if (!apiKey.trim() || apiKeyMasked) errors.apiKey = "请输入 API Key 后再获取模型列表";
    setFormErrors((current) => ({ ...current, ...errors }));
    if (Object.keys(errors).length > 0) return;
    setDiscoveryBusy(true);
    try {
      const result = await withRefreshedToken((token) => discoverModels(token, {
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
      }));
      setDiscoveredModels(result.value.items);
      setDiscoveryOpen(true);
      if (result.value.items.length === 0) notify.warning("供应商返回了空模型列表，可以继续手动输入模型 ID。");
    } catch (cause) {
      notify.error(cause, "获取模型列表失败");
    } finally {
      setDiscoveryBusy(false);
    }
  }

  function toggleDiscovered(modelIdValue: string, checked: boolean) {
    if (checked) addLocalModel(modelIdValue);
    else setEditableModels((current) => current.filter((item) => item.upstream_model_id !== modelIdValue));
  }

  function validateConfiguration(): boolean {
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = "请输入名称";
    if (!baseUrl.trim()) errors.baseUrl = "请输入 Base URL";
    if (!editing && !apiKey.trim()) errors.apiKey = "请输入 API Key";
    if (editableModels.length === 0) errors.models = "请至少添加一个模型";
    else if (editableModels.some((item) => !item.upstream_model_id.trim())) errors.models = "请输入完整的模型名称";
    else if (editableModels.some((item) => extensionOptionsError(item.extension_options))) errors.models = "请修正模型扩展对象";
    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  }

  async function saveConfiguration(event: FormEvent, requestEnable: boolean) {
    event.preventDefault();
    if (!validateConfiguration()) return;
    setBusy(true);
    try {
      const payload = {
        name: name.trim(),
        provider_preset: providerPreset,
        provider_kind: providerKind,
        base_url: baseUrl.trim() || officialUrl,
        api_key: apiKeyMasked ? undefined : apiKey.trim() || undefined,
        models: editableModels.map(modelPayload),
        is_enabled: requestEnable,
      };
      const result = await withRefreshedToken((token) => editing
        ? saveModelEndpointConfiguration(token, editing.id, payload)
        : createModelEndpoint(token, payload));
      if (!requestEnable) {
        notify.success("模型配置已保存，当前为未启用状态。");
        const editingRoute = Boolean(modelId);
        closeEditor(true);
        if (!editingRoute) await load();
        return;
      }
      if (result.value.is_enabled) {
        notify.success("模型配置已保存并启用。");
        const editingRoute = Boolean(modelId);
        closeEditor(true);
        if (!editingRoute) await load();
        return;
      }
      setEditing(result.value);
      setPendingEnable(true);
      setTestEndpoint(result.value);
      const nextModel = result.value.models.find((item) => item.test_status !== "passed") ?? result.value.models[0];
      setTestModelId(nextModel?.id ?? "");
      setTestRun(nextModel?.latest_test ?? null);
      setTestOpen(true);
      notify.warning("配置已保存；完成所有模型测试后将自动启用。");
    } catch (cause) {
      notify.error(cause, requestEnable ? "保存并使用失败" : "保存模型配置失败");
    } finally {
      setBusy(false);
    }
  }

  async function turnOn(endpoint: ModelEndpoint) {
    if (endpoint.is_enabled || endpoint.read_only) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => enableModelEndpoint(token, endpoint.id));
      notify.success("模型已启用。");
      await load();
    } catch (cause) {
      notify.error(cause, "启用模型失败");
    } finally {
      setBusy(false);
    }
  }

  async function turnOff(endpoint: ModelEndpoint) {
    if (!endpoint.is_enabled || endpoint.read_only) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => disableModelEndpoint(token, endpoint.id));
      notify.warning("模型已停用。");
      await load();
    } catch (cause) {
      notify.error(cause, "停用模型失败");
    } finally {
      setBusy(false);
    }
  }

  async function removeEndpoint(endpoint: ModelEndpoint) {
    if (endpoint.is_enabled || endpoint.read_only) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => deleteModelEndpoint(token, endpoint.id));
      notify.success("模型已删除。");
      const nextPagination = models.length === 1 && pagination.current > 1
        ? { ...pagination, current: pagination.current - 1 }
        : pagination;
      if (nextPagination.current !== pagination.current) setPagination(nextPagination);
      await load(nextPagination);
    } catch (cause) {
      notify.error(cause, "删除模型失败");
    } finally {
      setBusy(false);
    }
  }

  function openTest(endpoint: ModelEndpoint, endpointModel?: ModelEndpointModel) {
    const target = endpointModel ?? endpoint.models[0];
    if (!target) return;
    setPendingEnable(false);
    setTestEndpoint(endpoint);
    setTestModelId(target.id);
    setTestRun(target.latest_test);
    setTestOpen(true);
  }

  async function refreshTestEndpoint(endpointId: string): Promise<ModelEndpoint> {
    const result = await withRefreshedToken((token) => getModelEndpoint(token, endpointId));
    setTestEndpoint(result.value);
    setEditing((current) => current?.id === result.value.id ? result.value : current);
    setEditableModels(result.value.models.map(toEditableModel));
    setModels((current) => current.map((item) => item.id === result.value.id ? result.value : item));
    return result.value;
  }

  async function pollTest(runId: string): Promise<ModelTestRun> {
    while (true) {
      const result = await withRefreshedToken((token) => getModelTestRun(token, runId));
      setTestRun(result.value);
      if (terminalTestStatuses.has(result.value.status)) return result.value;
      await new Promise((resolve) => window.setTimeout(resolve, 300));
    }
  }

  async function runTest() {
    if (!testEndpoint || !testModelId) return;
    setBusy(true);
    try {
      const started = await withRefreshedToken((token) => startModelTest(token, testEndpoint.id, testModelId));
      setTestRun(started.value);
      const completed = await pollTest(started.value.id);
      const endpoint = await refreshTestEndpoint(testEndpoint.id);
      if (completed.status !== "passed") {
        notify.error(new Error(completed.error_message ?? "模型没有通过连通性测试"), "模型测试失败");
        return;
      }
      notify.success("模型连通性测试通过。");
      if (!pendingEnable) return;
      const nextModel = endpoint.models.find((item) => item.test_status !== "passed");
      if (nextModel) {
        setTestModelId(nextModel.id);
        setTestRun(nextModel.latest_test);
        notify.warning(`请继续测试 ${nextModel.display_name || nextModel.upstream_model_id}。`);
        return;
      }
      const enabled = await withRefreshedToken((token) => enableModelEndpoint(token, endpoint.id));
      setTestEndpoint(enabled.value);
      setPendingEnable(false);
      notify.success("所有模型测试通过，配置已启用。");
      setTestOpen(false);
      const editingRoute = Boolean(modelId);
      closeEditor(true);
      if (!editingRoute) await load();
    } catch (cause) {
      notify.error(cause, "模型测试失败");
    } finally {
      setBusy(false);
    }
  }

  const columns: AppTableColumn<ModelEndpoint>[] = [
    { title: "名称", key: "name", width: "24%", render: (_, endpoint) => <div className="flex min-w-[220px] items-center gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-blue-50 font-semibold text-blue-600">{endpoint.name.slice(0, 1).toUpperCase() || <Server className="size-5" />}</span><div className="min-w-0"><div className="truncate font-medium text-[#1c252e]">{endpoint.name}</div><div className="mt-1 truncate font-mono text-xs text-[#919eab]">{endpoint.base_url ?? "—"}</div></div></div> },
    {
      title: "模型名称",
      key: "models",
      width: "25%",
      render: (_, endpoint) => (
        <div className="flex min-w-[220px] flex-col items-start gap-1.5">
          {endpoint.models.map((model) => {
            const status = testStatusMeta[model.test_status];
            return (
              <button
                key={model.id}
                type="button"
                className="flex max-w-full items-center gap-2 text-left hover:text-violet-600"
                onClick={() => openTest(endpoint, model)}
                title={`测试 ${model.display_name || model.upstream_model_id}`}
              >
                <span className="truncate font-mono text-sm text-[#1c252e]">
                  {model.display_name || model.upstream_model_id}
                </span>
                <span className={cn("shrink-0 text-xs", status.className)}>{status.label}</span>
              </button>
            );
          })}
        </div>
      ),
    },
    {
      title: "使用 Agent",
      key: "usedAgents",
      width: "21%",
      render: (_, endpoint) => endpoint.used_agent_count > 0 ? (
        <div className="min-w-[180px] text-sm">
          <div className="font-medium text-[#1c252e]">{endpoint.used_agent_count} 个 Agent</div>
          <div className="mt-1 break-words text-[#637381]">{endpoint.used_agent_names.join("、")}</div>
        </div>
      ) : <span className="text-sm text-[#919eab]">暂无 Agent 使用</span>,
    },
    { title: "使用状态", key: "enabled", width: "14%", render: (_, endpoint) => <span className={cn("text-sm font-medium", endpoint.is_enabled ? "text-emerald-600" : "text-[#919eab]")}>{endpoint.is_enabled ? "已启用" : "未启用"}</span> },
    { title: "更新时间", dataIndex: "updated_at", width: "16%", render: (value) => <span className="whitespace-nowrap text-sm text-[#637381]">{typeof value === "string" ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—"}</span> },
    {
      title: "操作",
      key: "actions",
      width: 152,
      align: "right",
      render: (_, endpoint) => {
        const editDisabled = endpoint.is_enabled || endpoint.read_only;
        const deleteDisabled = endpoint.is_enabled || endpoint.read_only;
        return (
          <div className="model-row-actions flex justify-end gap-0">
            <ModelActionButton
              label={endpoint.is_enabled ? "禁用" : "启用"}
              disabled={busy || endpoint.read_only}
              onClick={() => void (endpoint.is_enabled ? turnOff(endpoint) : turnOn(endpoint))}
              className={endpoint.is_enabled
                ? "text-amber-600 hover:bg-amber-50 hover:text-amber-700"
                : "text-emerald-600 hover:bg-emerald-50 hover:text-emerald-700"}
            >
              {endpoint.is_enabled ? <PowerOff /> : <Power />}
            </ModelActionButton>
            <ModelActionButton
              label="测试"
              disabled={busy || endpoint.read_only}
              onClick={() => openTest(endpoint)}
              className="text-[#637381] hover:bg-violet-50 hover:text-violet-600"
            >
              <Wifi />
            </ModelActionButton>
            <ModelActionButton
              label={editDisabled ? "编辑（启用状态不可用）" : "编辑"}
              disabled={editDisabled || busy}
              onClick={() => navigate(`/agent-management/models/${endpoint.id}`)}
              className="text-[#637381] hover:bg-[#f4f6f8] hover:text-[#1c252e] disabled:text-[#c4cdd5]"
            >
              <Pencil />
            </ModelActionButton>
            <ModelActionButton
              label={deleteDisabled ? "删除（启用状态不可用）" : "删除"}
              disabled={deleteDisabled || busy}
              onClick={() => void removeEndpoint(endpoint)}
              className="text-red-600 hover:bg-red-50 hover:text-red-700 disabled:text-[#c4cdd5]"
            >
              <Trash2 />
            </ModelActionButton>
          </div>
        );
      },
    },
  ];

  const testRunning = testRun?.status === "queued" || testRun?.status === "running";
  const canSubmit = Boolean(
    name.trim()
      && baseUrl.trim()
      && (editing || (apiKey.trim() && !apiKeyMasked))
      && editableModels.length > 0
      && editableModels.every((item) => item.upstream_model_id.trim())
      && editableModels.every((item) => !extensionOptionsError(item.extension_options)),
  );

  return <>
    <PageHeader title="模型管理" description="统一管理官方模型和中转模型，每个模型独立记录测试状态。" actions={<Button onClick={() => { resetEditor(); setCreateOpen(true); }}><Plus />添加模型</Button>} />
    {error && <Alert variant="destructive" className="mt-5"><CircleAlert /><AlertDescription>{error}</AlertDescription></Alert>}
    <TooltipProvider delayDuration={200}>
      <section className="overflow-hidden rounded-2xl bg-white"><div className="flex items-center justify-between pr-6 py-3"><h2 className="font-semibold">模型列表</h2><Button variant="ghost" size="icon" onClick={() => void load()} aria-label="刷新模型列表"><RefreshCw className={loading ? "animate-spin" : ""} /></Button></div>{loading ? <div className="grid h-52 place-items-center"><LoaderCircle className="animate-spin text-emerald-500" aria-label="加载模型列表" /></div> : <AppTable columns={columns} dataSource={models} rowKey="id" pagination={{ current: pagination.current, pageSize: pagination.pageSize, total: totalModels, onChange: (current, pageSize) => { const next = { current, pageSize }; setPagination(next); void load(next); } }} emptyText="暂无模型" ariaLabel="模型列表" />}</section>
    </TooltipProvider>

    <ProviderPresetDialog
      open={createOpen && step === 1}
      onOpenChange={(open) => { if (!open) closeEditor(); }}
      presets={presets}
      onSelect={selectPreset}
    />

    <ModelConnectionFormDialog
      open={(createOpen || Boolean(modelId)) && step === 2}
      onOpenChange={(open) => { if (!open) closeEditor(); }}
      editing={editing}
      canGoBack={false}
      onBack={() => setStep(1)}
      name={name}
      onNameChange={(value) => { setName(value); setFormErrors((current) => ({ ...current, name: "" })); }}
      baseUrl={baseUrl}
      onBaseUrlChange={(value) => { setBaseUrl(value); setFormErrors((current) => ({ ...current, baseUrl: "" })); }}
      apiKey={apiKey}
      onApiKeyChange={(value) => { setApiKey(value); setFormErrors((current) => ({ ...current, apiKey: "" })); }}
      onApiKeyFocus={() => {
        if (apiKeyMasked) {
          setApiKey("");
          setApiKeyMasked(false);
        }
      }}
      showKey={showKey}
      onShowKeyChange={() => setShowKey((value) => !value)}
      editableModels={editableModels}
      onUpdateModel={updateEditableModel}
      onRemoveModel={(index) => setEditableModels((current) => current.filter((_, itemIndex) => itemIndex !== index))}
      onTestModel={(index) => void testModelFromForm(index)}
      modelInput={modelInput}
      onModelInputChange={setModelInput}
      onAddModel={() => addLocalModel()}
      onDiscover={() => void fetchModels()}
      discoveryBusy={discoveryBusy}
      discoveryOpen={discoveryOpen}
      onDiscoveryOpenChange={setDiscoveryOpen}
      discoveredModels={discoveredModels}
      onToggleDiscovered={toggleDiscovered}
      errors={formErrors}
      onSave={(event) => void saveConfiguration(event, false)}
      onSaveAndUse={(event) => void saveConfiguration(event, true)}
      busy={busy}
      canSubmit={canSubmit}
      providerKind={providerKind}
    />

    <ModelTestDialog
      open={testOpen}
      onOpenChange={(open) => { if (!open && !testRunning) { setTestOpen(false); setPendingEnable(false); } }}
      endpoint={testEndpoint}
      modelId={testModelId}
      onModelChange={(value) => { setTestModelId(value); setTestRun(testEndpoint?.models.find((item) => item.id === value)?.latest_test ?? null); }}
      testRun={testRun}
      onRun={() => void runTest()}
      running={testRunning}
      busy={busy}
    />

  </>;
}
