import type { DashboardTrace } from "@/types";

/** 三种计时使用各自时钟，不能相减推算精确网络耗时。 */
export function FirstTextTimingSummary({ observations }: { observations: DashboardTrace["observations"] }) {
  const model = observations.filter((item) => item.kind === "llm" && item.first_output_at)
    .sort((left, right) => Date.parse(left.first_output_at!) - Date.parse(right.first_output_at!))[0];
  const api = observations.find((item) => item.name === "api.first_committed_text_observed");
  const client = observations.find((item) => item.name === "client.first_text_commit");
  const modelElapsed = model?.first_output_at ? Date.parse(model.first_output_at) - Date.parse(model.started_at) : null;
  const clientElapsed = client?.metadata.client_elapsed_ms;
  const show = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${Math.round(value)} ms` : "未观测";
  return <section aria-label="首次输出的独立计时" className="space-y-2 rounded-2xl bg-white p-4 text-sm">
    <h3 className="font-medium">首次输出计时</h3>
    <dl className="grid gap-3 sm:grid-cols-3">
      <div><dt className="text-muted-foreground">模型调用 → 首个非空输出块</dt><dd>{show(modelElapsed)}</dd></div>
      <div><dt className="text-muted-foreground">Run 创建 → API 读到已提交正文</dt><dd>{show(api?.duration_ms)}</dd></div>
      <div><dt className="text-muted-foreground">本页提交 → React 正文提交</dt><dd>{show(clientElapsed)}</dd></div>
    </dl>
    <p className="text-xs text-muted-foreground">API 为提交可见的观测上界，包含轮询或重连等待；客户端为未信任的浏览器单调时钟，仅在本页发起且可见时采集。缺失保留未知，不用于判定业务完成。</p>
  </section>;
}
