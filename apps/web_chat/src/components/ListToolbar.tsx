import type { ReactNode } from "react";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type ListToolbarProps = {
  /** 左侧查询条件；为空时展示列表标题。 */
  filters?: ReactNode;
  /** 没有查询条件时显示的列表名称。 */
  title?: string;
  /** 刷新当前列表。 */
  onRefresh: () => void;
  /** 当前是否正在加载。 */
  loading?: boolean;
  className?: string;
};

/** 后台列表统一工具栏：查询条件左侧，刷新按钮右侧。 */
export function ListToolbar({ filters, title = "列表", onRefresh, loading = false, className }: ListToolbarProps) {
  return (
    <div data-list-toolbar="true" className={cn("flex min-h-16 items-center justify-between gap-4 py-3 pl-0 pr-6", className)}>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
        {filters ?? <h2 className="text-sm font-semibold text-[#1c252e]">{title}</h2>}
      </div>
      <Button type="button" variant="ghost" size="icon" onClick={onRefresh} disabled={loading} aria-label="刷新列表" title="刷新列表">
        <RefreshCw className={cn("size-4", loading && "animate-spin")} />
      </Button>
    </div>
  );
}
