import { GlobalLoading } from "@/components/GlobalLoading";
import { cn } from "@/lib/utils";

interface ListLoadingOverlayProps {
  label?: string;
  className?: string;
}

/** 列表请求统一使用的悬浮加载层，避免内容切换时布局跳动。 */
export function ListLoadingOverlay({
  label = "正在加载列表…",
  className,
}: ListLoadingOverlayProps) {
  return (
    <div
      className={cn(
        "absolute inset-0 z-10 flex items-center justify-center bg-white/80",
        className,
      )}
      aria-busy="true"
    >
      <GlobalLoading fullScreen={false} size="sm" label={label} />
    </div>
  );
}
