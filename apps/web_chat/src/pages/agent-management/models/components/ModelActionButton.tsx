import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface ModelActionButtonProps {
  /** 操作提示文案，同时作为无障碍标签。 */
  label: string;
  /** 启用状态下的不可用操作。 */
  disabled?: boolean;
  /** 图标按钮点击回调。 */
  onClick?: () => void;
  /** 图标颜色及悬浮样式。 */
  className?: string;
  /** 操作图标。 */
  children: ReactNode;
}

/** 模型列表操作图标：统一处理悬浮提示和禁用按钮的可触达区域。 */
export function ModelActionButton({
  label,
  disabled = false,
  onClick,
  className,
  children,
}: ModelActionButtonProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex" tabIndex={disabled ? 0 : -1} aria-disabled={disabled}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={className}
            onClick={onClick}
            disabled={disabled}
            aria-label={label}
          >
            {children}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
