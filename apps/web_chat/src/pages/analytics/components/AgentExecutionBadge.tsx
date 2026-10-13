import { StatusBadge } from "@/components/StatusBadge";

const labels: Record<string, string> = {
  idle: "空闲", queued: "排队", running: "执行中", cancelling: "取消中", needs_attention: "待恢复/人工处理",
};

/** 执行态与健康态分开；待恢复不能显示为正在执行模型。 */
export function AgentExecutionBadge({ status }: { status: string }) {
  return <StatusBadge value={status === "needs_attention" ? "degraded" : status} label={labels[status] ?? status} />;
}
