import { type FormEvent } from "react";
import { ChevronLeft, Eye, EyeOff, LoaderCircle, RefreshCw, Trash2, Wifi } from "lucide-react";

import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import type { ModelEndpoint, ModelProviderPreset } from "@/types";

import { type EditableModel, extensionOptionsError, testStatusMeta } from "./modelTypes";

interface ModelConnectionFormDialogProps {
  /** 弹窗是否打开。 */
  open: boolean;
  /** 关闭弹窗。 */
  onOpenChange: (open: boolean) => void;
  /** 当前是否编辑已有连接。 */
  editing: ModelEndpoint | null;
  /** 当前步骤是否允许返回预设选择。 */
  canGoBack: boolean;
  /** 返回预设选择回调。 */
  onBack: () => void;
  /** 当前连接名称。 */
  name: string;
  /** 更新连接名称。 */
  onNameChange: (value: string) => void;
  /** 当前 Base URL。 */
  baseUrl: string;
  /** 更新 Base URL。 */
  onBaseUrlChange: (value: string) => void;
  /** 当前 API Key。 */
  apiKey: string;
  /** 更新 API Key。 */
  onApiKeyChange: (value: string) => void;
  /** 用户准备编辑 API Key 时清除安全占位值。 */
  onApiKeyFocus: () => void;
  /** 是否展示 API Key。 */
  showKey: boolean;
  /** 切换 API Key 可见性。 */
  onShowKeyChange: () => void;
  /** 当前模型行集合。 */
  editableModels: EditableModel[];
  /** 更新模型行字段。 */
  onUpdateModel: (index: number, patch: Partial<EditableModel>) => void;
  /** 删除模型行。 */
  onRemoveModel: (index: number) => void;
  /** 保存当前配置并测试指定模型。 */
  onTestModel: (index: number) => void;
  /** 当前手工模型输入。 */
  modelInput: string;
  /** 更新手工模型输入。 */
  onModelInputChange: (value: string) => void;
  /** 回车添加单个模型。 */
  onAddModel: () => void;
  /** 获取远端模型列表。 */
  onDiscover: () => void;
  /** 远端发现请求是否执行中。 */
  discoveryBusy: boolean;
  /** 远端发现下拉是否打开。 */
  discoveryOpen: boolean;
  /** 远端发现浮层状态变更；点击外部时由 Popover 自动关闭。 */
  onDiscoveryOpenChange: (open: boolean) => void;
  /** 远端发现模型列表。 */
  discoveredModels: string[];
  /** 选择或取消远端模型。 */
  onToggleDiscovered: (modelId: string, checked: boolean) => void;
  /** 表单字段错误。 */
  errors: Record<string, string>;
  /** 普通保存。 */
  onSave: (event: FormEvent) => void;
  /** 保存并使用。 */
  onSaveAndUse: (event: FormEvent) => void;
  /** 保存过程是否繁忙。 */
  busy: boolean;
  /** 必填内容是否已完整填写。 */
  canSubmit: boolean;
  /** 供应商类型决定官方地址是否只读。 */
  providerKind: "openai_official" | "openai_compatible";
}

/** 模型配置创建/编辑的第二步表单。 */
export function ModelConnectionFormDialog({
  open,
  onOpenChange,
  editing,
  canGoBack,
  onBack,
  name,
  onNameChange,
  baseUrl,
  onBaseUrlChange,
  apiKey,
  onApiKeyChange,
  onApiKeyFocus,
  showKey,
  onShowKeyChange,
  editableModels,
  onUpdateModel,
  onRemoveModel,
  onTestModel,
  modelInput,
  onModelInputChange,
  onAddModel,
  onDiscover,
  discoveryBusy,
  discoveryOpen,
  onDiscoveryOpenChange,
  discoveredModels,
  onToggleDiscovered,
  errors,
  onSave,
  onSaveAndUse,
  busy,
  canSubmit,
  providerKind,
}: ModelConnectionFormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[880px]">
        <form onSubmit={onSave} noValidate>
          <DialogHeader>
            <DialogTitle>{editing ? "编辑模型" : "添加模型"}</DialogTitle>
            <DialogDescription>
              保存只写入配置并保持未启用；保存并使用要求当前所有模型测试通过。
            </DialogDescription>
          </DialogHeader>
          <div className="mt-6 grid gap-4 md:grid-cols-[240px_minmax(0,1fr)]">
            <FormField label="名称" htmlFor="model-name" required error={errors.name}>
              <Input
                id="model-name"
                value={name}
                onChange={(event) => onNameChange(event.target.value)}
                placeholder="请输入名称"
                aria-invalid={Boolean(errors.name)}
              />
            </FormField>
            <FormField label="Base URL" htmlFor="model-base-url" required error={errors.baseUrl}>
              <Input
                id="model-base-url"
                value={baseUrl}
                onChange={(event) => onBaseUrlChange(event.target.value)}
                placeholder="请输入 Base URL"
                aria-invalid={Boolean(errors.baseUrl)}
              />
            </FormField>
          </div>
          <FormField
            label="API Key"
            htmlFor="model-api-key"
            required={!editing}
            error={errors.apiKey}
            className="mt-4"
          >
            <div className="relative">
              <Input
                id="model-api-key"
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(event) => onApiKeyChange(event.target.value)}
                onFocus={onApiKeyFocus}
                placeholder={
                  editing
                    ? `已配置 ${editing.credential_masked_hint ?? "API Key"}；保存可留空，获取模型列表需重新输入`
                    : "请输入 API Key"
                }
                autoComplete="new-password"
                className="pr-11"
                aria-invalid={Boolean(errors.apiKey)}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-1 top-1/2 -translate-y-1/2 rounded-full"
                onClick={onShowKeyChange}
                aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
              >
                {showKey ? <EyeOff /> : <Eye />}
              </Button>
            </div>
          </FormField>
          <FormField label="模型" htmlFor="model-input" required error={errors.models} className="mt-5">
            <Popover open={discoveryOpen} onOpenChange={onDiscoveryOpenChange}>
              <PopoverAnchor asChild>
                <div className="relative flex gap-3">
                  <Input
                    id="model-input"
                    value={modelInput}
                    onChange={(event) => onModelInputChange(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        onAddModel();
                      }
                    }}
                    placeholder="模型 ID 或模型名称"
                  />
                  <Button type="button" variant="outline" className="shrink-0" onClick={onDiscover} disabled={discoveryBusy}>
                    {discoveryBusy ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
                    获取模型列表
                  </Button>
                </div>
              </PopoverAnchor>
              <PopoverContent
                align="start"
                sideOffset={8}
                className="z-[100] max-h-[320px] w-[min(560px,calc(100vw-48px))] overflow-y-auto overscroll-contain p-2"
              >
                {discoveredModels.length === 0 ? (
                  <div className="px-3 py-8 text-center text-sm text-muted-foreground">暂无可选模型</div>
                ) : (
                  discoveredModels.map((item) => {
                    const checked = editableModels.some((model) => model.upstream_model_id === item);
                    return (
                      <Label
                        key={item}
                        className={cn(
                          "flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 font-normal transition-colors hover:bg-muted",
                          checked && "bg-violet-50 text-violet-700 hover:bg-violet-100",
                        )}
                      >
                        <Checkbox checked={checked} onCheckedChange={(value) => onToggleDiscovered(item, value === true)} />
                        <span className="min-w-0 flex-1 truncate font-mono text-sm">{item}</span>
                        {checked && <span className="shrink-0 text-xs text-violet-600">已添加</span>}
                      </Label>
                    );
                  })
                )}
              </PopoverContent>
            </Popover>
            <p className="mt-2 text-xs text-muted-foreground">
              回车只在本地添加；获取模型列表始终使用当前 Base URL 和 API Key 请求 /models。
            </p>
          </FormField>
          {editableModels.length > 0 && (
            <div className="mt-5 overflow-x-auto rounded-lg border border-slate-200 bg-white">
              <div className="grid min-w-[760px] grid-cols-[220px_minmax(300px,1fr)_112px_76px] gap-4 bg-[#f4f6f8] px-4 py-3 text-xs font-medium text-[#637381]">
                <span>模型名称</span><span>扩展对象（JSON）</span><span>测试结果</span><span />
              </div>
              <div className="divide-y">
                {editableModels.map((model, index) => {
                  const extensionError = extensionOptionsError(model.extension_options);
                  return (
                    <div
                      key={`${model.id ?? "new"}-${index}`}
                      className="grid min-w-[760px] grid-cols-[220px_minmax(300px,1fr)_112px_76px] items-start gap-4 bg-white px-4 py-4 transition-colors hover:bg-slate-50/70"
                    >
                      <div>
                        <Input
                          value={model.upstream_model_id}
                          onChange={(event) => onUpdateModel(index, { upstream_model_id: event.target.value })}
                          placeholder="请输入模型名称"
                          className="border-slate-200 bg-white font-mono text-sm"
                        />
                      </div>
                      <div>
                        <Textarea
                          value={model.extension_options}
                          onChange={(event) => onUpdateModel(index, { extension_options: event.target.value })}
                          placeholder="请输入 JSON 扩展对象，例如 {}"
                          className="min-h-20 resize-y border-slate-700 bg-[#111827] font-mono text-sm leading-6 text-emerald-200 shadow-inner placeholder:text-slate-500 focus-visible:border-violet-500 focus-visible:ring-violet-500/20 aria-invalid:border-red-500! aria-invalid:focus-visible:border-red-500! aria-invalid:focus-visible:ring-red-200!"
                          rows={3}
                          spellCheck={false}
                          aria-invalid={Boolean(extensionError)}
                          aria-describedby={extensionError ? `model-extension-error-${index}` : undefined}
                        />
                        {extensionError && <p id={`model-extension-error-${index}`} className="mt-1 text-xs text-red-600">{extensionError}</p>}
                      </div>
                      <div className="flex min-h-10 items-center">
                        {model.test_status && (
                          <span
                            className={cn(
                              "inline-flex rounded-md border border-current/20 bg-white px-2 py-1 text-xs font-medium",
                              testStatusMeta[model.test_status].className,
                            )}
                          >
                            {testStatusMeta[model.test_status].label}
                          </span>
                        )}
                      </div>
                      <div className="flex min-h-10 items-center justify-end gap-0">
                        <Button type="button" variant="ghost" size="icon" title="测试" className="text-violet-600 hover:bg-violet-50 hover:text-violet-700" onClick={() => onTestModel(index)} disabled={busy || !canSubmit} aria-label={`测试 ${model.upstream_model_id}`}><Wifi /></Button>
                        <Button type="button" variant="ghost" size="icon" title="移除" className="text-destructive" onClick={() => onRemoveModel(index)} disabled={busy} aria-label={`移除 ${model.upstream_model_id}`}><Trash2 /></Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          <DialogFooter className="mt-6 flex-row justify-between sm:justify-between">
            <div>{canGoBack && <Button type="button" variant="ghost" onClick={onBack} disabled={busy}><ChevronLeft />返回选择</Button>}</div>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>取消</Button>
              <Button type="submit" variant="outline" disabled={busy || !canSubmit}>{busy ? "保存中…" : "保存"}</Button>
              <Button type="button" onClick={onSaveAndUse} disabled={busy || !canSubmit}>{busy ? "处理中…" : "保存并使用"}</Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
