import type { DataCategory } from "@/types";

export const categoryLabels: Record<DataCategory, string> = {
  identity: "账户资料", auth_sessions: "登录会话", conversations: "对话", messages: "消息", runs: "运行记录",
  events: "运行事件", observations: "运行观测", knowledge: "知识与证据", credentials: "凭据关联",
  actions: "业务动作与回执", approvals: "审批与项目授权", checkpoints: "执行检查点", memory: "长期记忆",
  backups: "备份", external_copies: "外部副本", governance_audit: "治理审计",
};
export const requestStatus = { requested: "待预览", previewed: "已生成预览", withdrawn: "已撤回" };
const blockerLabels: Record<string, string> = {
  PREVIEW_ONLY: "当前仅支持影响预览", POLICY_NOT_APPROVED: "保留策略尚未批准",
  POLICY_NOT_CONFIGURED: "尚未配置保留策略", LEGAL_HOLD_REVIEW_REQUIRED: "需确认法律保留要求",
  MAINTENANCE_IDENTITY_NOT_CONFIGURED: "尚未配置独立维护身份", DEPENDENCY_INVENTORY_REQUIRED: "依赖与副本尚未完整盘点",
  RETENTION_RULES_UNCONFIRMED: "保留周期或恢复规则尚未确认", INDEPENDENT_REVIEWER_NOT_CONFIGURED: "未配置独立非本人复核候选人",
  ACTIVE_RUNS_PRESENT: "仍有未结束运行", UNRESOLVED_ACTIONS_PRESENT: "仍有未确认终态的业务动作",
  FIXED_REFERENCES_PRESENT: "仍有固定版本或证据引用，需保留并独立审查",
  SHARED_OR_AUDIT_REFERENCE_REVIEW_REQUIRED: "包含共享或审计关联，需独立审查", CATEGORY_ADAPTER_NOT_IMPLEMENTED: "该类别尚无完整盘点适配",
};
export function blockerLabel(code: string) { return blockerLabels[code] ?? "存在未识别的阻塞原因，请联系数据治理负责人"; }
