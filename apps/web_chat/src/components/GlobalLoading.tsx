import { cn } from "@/lib/utils";

interface GlobalLoadingProps {
  /** 屏幕阅读器可感知的当前加载状态说明。 */
  label?: string;
  /** 是否占满当前视口；全局路由加载默认占满视口。 */
  fullScreen?: boolean;
  /** 紧凑场景使用更小的视觉尺寸，例如下拉菜单和列表遮罩。 */
  size?: "default" | "sm";
  className?: string;
}

/** 全局页面加载状态：中心展示平台 Logo，外层双层圆角菱形线圈以相反方向旋转。 */
export function GlobalLoading({
  label = "正在加载…",
  fullScreen = true,
  size = "default",
  className,
}: GlobalLoadingProps) {
  return (
    <div
      className={cn("global-loading", fullScreen && "global-loading--fullscreen", size === "sm" && "global-loading--sm", className)}
      role="status"
      aria-live="polite"
    >
      <div className="global-loading__visual" aria-hidden="true">
        <span className="global-loading__ring global-loading__ring--outer" />
        <span className="global-loading__ring global-loading__ring--inner" />
        <img className="global-loading__logo" src="/supportops-mark.svg" alt="" />
      </div>
      <span className="sr-only">{label}</span>
    </div>
  );
}
