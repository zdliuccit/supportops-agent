import { Plus } from "lucide-react";

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/AppDialog";
import type { ModelProviderPreset } from "@/types";

interface ProviderPresetDialogProps {
  /** 弹窗是否打开。 */
  open: boolean;
  /** 关闭弹窗回调。 */
  onOpenChange: (open: boolean) => void;
  /** 服务端返回的官方/中转预设。 */
  presets: ModelProviderPreset[];
  /** 选择自定义空配置或预设。 */
  onSelect: (preset: ModelProviderPreset | null) => void;
}

/** 模型配置创建的第一步：选择供应商预设。 */
export function ProviderPresetDialog({
  open,
  onOpenChange,
  presets,
  onSelect,
}: ProviderPresetDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[820px]">
        <DialogHeader>
          <DialogTitle>添加模型</DialogTitle>
          <DialogDescription>
            选择常用供应商预设，或从空白配置开始。模型协议会在下一步为每个模型单独设置。
          </DialogDescription>
        </DialogHeader>
        <DialogBody><div className="mt-5 grid gap-3 md:grid-cols-2">
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="flex min-h-24 items-center gap-4 rounded-2xl border border-dashed p-5 text-left transition hover:border-violet-300 hover:bg-violet-50/40"
          >
            <span className="grid size-11 shrink-0 place-items-center rounded-full bg-muted">
              <Plus className="size-5" />
            </span>
            <span>
              <strong className="block">自定义中转模型</strong>
              <span className="mt-1 block truncate text-sm text-muted-foreground">
                名称、Base URL、Key 和模型均从空白开始
              </span>
            </span>
          </button>
          {presets.map((preset) => (
            <button
              key={preset.id}
              type="button"
              onClick={() => onSelect(preset)}
              className="flex min-h-24 items-center gap-4 rounded-2xl border p-5 text-left transition hover:border-violet-300 hover:bg-violet-50/40"
            >
              <span className="grid size-11 shrink-0 place-items-center rounded-full bg-blue-600 font-semibold text-white">
                {preset.name.slice(0, 1)}
              </span>
              <span className="min-w-0">
                <strong className="block truncate">{preset.name}</strong>
                <span className="mt-1 block truncate font-mono text-sm text-muted-foreground">
                  {preset.base_url}
                </span>
              </span>
            </button>
          ))}
        </div></DialogBody>
      </DialogContent>
    </Dialog>
  );
}
