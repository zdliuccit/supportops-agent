import type { ReactNode } from "react";
import { X } from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type AppDrawerProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  onConfirm?: () => void | Promise<void>;
  onCancel?: () => void | Promise<void>;
  confirmText?: string;
  cancelText?: string;
  confirmVariant?: "default" | "destructive" | "outline";
  loading?: boolean;
  showHeader?: boolean;
  showFooter?: boolean;
  showCancel?: boolean;
  showConfirm?: boolean;
  footer?: ReactNode;
  side?: "right" | "left";
  className?: string;
  contentClassName?: string;
  footerClassName?: string;
};

/** 统一右侧业务抽屉：固定头部/底部，中间内容独立滚动。 */
export function AppDrawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  onConfirm,
  onCancel,
  confirmText = "确认",
  cancelText = "取消",
  confirmVariant = "default",
  loading = false,
  showHeader = true,
  showFooter = true,
  showCancel = true,
  showConfirm = true,
  footer,
  side = "right",
  className,
  contentClassName,
  footerClassName,
}: AppDrawerProps) {
  async function handleCancel() {
    await onCancel?.();
    if (!loading) onOpenChange(false);
  }

  async function handleConfirm() {
    await onConfirm?.();
  }

  return (
    <Sheet open={open} onOpenChange={(nextOpen) => { if (!loading) onOpenChange(nextOpen); }}>
      <SheetContent side={side} showClose={false} className={cn("flex h-full flex-col gap-0 p-0", className)}>
        {showHeader && (
          <header className="flex shrink-0 items-start justify-between gap-4 px-6 py-5">
            <div className="min-w-0">
              <SheetTitle>{title}</SheetTitle>
              {description ? <SheetDescription className="mt-1">{description}</SheetDescription> : null}
            </div>
            <Button type="button" variant="ghost" size="icon" onClick={() => onOpenChange(false)} disabled={loading} aria-label={`关闭${title}`} title={`关闭${title}`}>
              <X className="size-4" />
            </Button>
          </header>
        )}
        <main className={cn("min-h-0 flex-1 overflow-y-auto px-6 py-5", contentClassName)}>{children}</main>
        {showFooter && (footer ?? (
          <footer className={cn("flex shrink-0 justify-end gap-3 px-6 py-4", footerClassName)}>
            {showCancel && <Button type="button" variant="outline" onClick={() => void handleCancel()} disabled={loading}>{cancelText}</Button>}
            {showConfirm && <Button type="button" variant={confirmVariant} onClick={() => void handleConfirm()} disabled={loading}>{loading ? `${confirmText}中…` : confirmText}</Button>}
          </footer>
        ))}
      </SheetContent>
    </Sheet>
  );
}
