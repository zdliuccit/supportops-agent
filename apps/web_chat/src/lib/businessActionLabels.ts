import type { BusinessActionStatus } from "@/types";

/** 动作执行与审批事实分开表达；排队、运行及消费批准均不代表业务成功。 */
export const businessActionLabels: Record<BusinessActionStatus, string> = {
  proposed: "提案已保存，尚未执行", waiting_approval: "等待独立审批",
  queued: "执行意图已排队，尚未确认结果", running: "动作处理中，尚未确认结果",
  unknown: "结果未知，需要对账", reconciling: "正在独立对账", succeeded: "业务状态已独立复核成功",
  failed: "动作未完成", needs_attention: "需要人工处理未知结果", cancelled: "已取消",
};
