import type { DashboardTrace } from "@/types";

/** 使用账本聚合，不把父子 span 成本相加，不将未知 usage 当零。 */
export function RunBudgetSummary({ budget }: { budget: DashboardTrace["budget"] }) {
  if (!budget) return null;
  return <section className="space-y-2 rounded-2xl bg-white p-4 text-sm" aria-label="运行预算账本">
    <h3 className="font-semibold">模型调用账本</h3>
    <p>物理请求 {budget.model_call_count} 次 · 依赖重试 {budget.retry_count} 次</p>
    <p>输入 {budget.input_tokens ?? "未知"} · 输出 {budget.output_tokens ?? "未知"} Token</p>
    <p>费用：{budget.total_cost_microusd === null ? "未知" : `$${(budget.total_cost_microusd / 1_000_000).toFixed(6)}`}</p>
    {budget.unknown_usage_call_count > 0 && <p className="text-amber-700">{budget.unknown_usage_call_count} 次请求缺少完整 usage，预算保留预留，不按零计费。</p>}
    <p className="text-xs text-muted-foreground">输入含系统指令、历史、工具与证据；重试和恢复共用预算。账本合计独立于追踪 span。</p>
  </section>;
}
