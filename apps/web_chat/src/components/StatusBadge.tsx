import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type StatusBadgeProps = {
  value: string;
  label?: string;
  className?: string;
};

/** 列表状态统一使用无边框纯色标签，颜色仅表达状态语义。 */
export function StatusBadge({ value, label, className }: StatusBadgeProps) {
  const tone =
    ["failed", "degraded", "unhealthy", "unavailable", "error", "critical", "disabled"].includes(value)
      ? "bg-[#ff5630] text-white hover:bg-[#e5482f]"
      : ["healthy", "active", "completed", "resolved", "passed", "enabled"].includes(value)
        ? "bg-[#00a76f] text-white hover:bg-[#008f63]"
        : ["running", "queued", "pending", "pending_publish"].includes(value)
          ? "bg-[#f59e0b] text-white hover:bg-[#d97706]"
          : "bg-[#637381] text-white hover:bg-[#4b5863]";

  return <Badge className={cn("border-0 shadow-none", tone, className)}>{label ?? value}</Badge>;
}
