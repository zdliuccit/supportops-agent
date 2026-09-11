import type { FormEvent } from "react";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import type { ModelEndpoint, ModelEndpointModel } from "@/types";

export type ActiveModelOption = { endpoint: ModelEndpoint; model: ModelEndpointModel };

/** 新建 Agent 弹窗。 */
export function CreateAgentDialog({ open, busy, name, slug, description, prompt, selectedModelId, models, errors, onOpenChange, onSubmit, onNameChange, onSlugChange, onDescriptionChange, onPromptChange, onModelChange, onCancel }: { open: boolean; busy: boolean; name: string; slug: string; description: string; prompt: string; selectedModelId: string; models: ActiveModelOption[]; errors: Record<string, string>; onOpenChange: (open: boolean) => void; onSubmit: (event: FormEvent) => void; onNameChange: (value: string) => void; onSlugChange: (value: string) => void; onDescriptionChange: (value: string) => void; onPromptChange: (value: string) => void; onModelChange: (option: ActiveModelOption) => void; onCancel: () => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <form onSubmit={onSubmit} noValidate>
          <DialogHeader>
            <DialogTitle>新建 Agent</DialogTitle>
            <DialogDescription>
              先创建基础信息，之后可继续配置模型、工具、权限和发布版本。
            </DialogDescription>
          </DialogHeader>

          <div className="mt-5 grid gap-4 sm:grid-cols-2">
            <FormField label="名称" htmlFor="create-agent-name" required error={errors.name}>
              <Input
                id="create-agent-name"
                value={name}
                onChange={(event) => onNameChange(event.target.value)}
                placeholder="请输入 Agent 名称"
                aria-invalid={Boolean(errors.name)}
                aria-describedby={errors.name ? "create-agent-name-error" : undefined}
              />
            </FormField>

            <FormField label="Slug" htmlFor="create-agent-slug" required error={errors.slug}>
              <Input
                id="create-agent-slug"
                value={slug}
                onChange={(event) => onSlugChange(event.target.value)}
                placeholder="例如 technical-support"
                pattern="[a-z][a-z0-9-]{1,99}"
                aria-invalid={Boolean(errors.slug)}
                aria-describedby={errors.slug ? "create-agent-slug-error" : undefined}
              />
            </FormField>

            <FormField label="模型" htmlFor="create-agent-model" required error={errors.model} className="sm:col-span-2">
              <Select
                value={selectedModelId || undefined}
                onValueChange={(value) => {
                  const option = models.find((item) => item.model.id === value);
                  if (option) onModelChange(option);
                }}
              >
                <SelectTrigger
                  id="create-agent-model"
                  className="w-full"
                  aria-invalid={Boolean(errors.model)}
                  aria-describedby={errors.model ? "create-agent-model-error" : undefined}
                >
                  <SelectValue placeholder="选择已验证模型" />
                </SelectTrigger>
                <SelectContent>
                  {models.map(({ endpoint, model }) => (
                    <SelectItem key={model.id} value={model.id}>
                      {endpoint.name} / {model.display_name || model.upstream_model_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormField>
          </div>

          <FormField
            label="System Prompt"
            htmlFor="create-agent-prompt"
            required
            error={errors.prompt}
            className="mt-4"
          >
            <Textarea
              id="create-agent-prompt"
              className="min-h-32"
              value={prompt}
              onChange={(event) => onPromptChange(event.target.value)}
              placeholder="请输入 System Prompt"
              aria-invalid={Boolean(errors.prompt)}
              aria-describedby={errors.prompt ? "create-agent-prompt-error" : undefined}
            />
          </FormField>

          <Label className="mt-4 block text-sm">
            描述
            <Input
              className="mt-2"
              value={description}
              onChange={(event) => onDescriptionChange(event.target.value)}
              placeholder="请输入 Agent 描述"
            />
          </Label>

          <DialogFooter className="mt-6">
            <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
              取消
            </Button>
            <Button type="submit" disabled={busy || models.length === 0}>
              {busy ? "创建中…" : "创建 Agent"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
