import { useEffect, useRef, useState } from "react";
import { getRunDetail } from "@/api";
import { GlobalLoading } from "@/components/GlobalLoading";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import type { RunDetail } from "@/types";
import { MessageBusinessActions } from "./MessageBusinessActions";

const budgetErrors = new Set(["AGENT_CONTEXT_BUDGET_EXCEEDED", "AGENT_MODEL_CALL_BUDGET_EXCEEDED",
  "AGENT_COST_BUDGET_EXCEEDED", "AGENT_RUN_TIME_BUDGET_EXCEEDED"]);

/** 失败 Run 没有完整回答结果，独立提供升级入口，不构造虚假的 SupportResult。 */
export function BudgetEscalationEntry({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sequence = useRef(0);
  async function load() {
    const current = ++sequence.current;
    setLoading(true); setRun(null); setError(null);
    try {
      const response = await withRefreshedToken((token) => getRunDetail(token, runId));
      if (sequence.current === current) setRun(response.value);
    } catch (cause) { if (sequence.current === current) setError(cause instanceof Error ? cause.message : "读取升级上下文失败"); }
    finally { if (sequence.current === current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [runId]);
  if (!loading && !error && (!run || run.status !== "failed" || !run.error_code || !budgetErrors.has(run.error_code))) return null;
  return <section aria-label="预算耗尽后的升级" className="space-y-3 rounded-2xl bg-muted/40 p-4 text-sm">
    {loading ? <GlobalLoading fullScreen={false} size="sm" label="正在读取升级上下文…" /> : error
      ? <div role="alert" className="space-y-2"><p className="text-red-600">{error}</p><Button size="sm" variant="outline" onClick={() => void load()}>重新读取</Button></div>
      : run && <><p className="font-medium">本轮预算已耗尽，可保存排查升级包。</p>
        <p className="text-muted-foreground">只保存隔离 Mock 提案和当前获准证据，不代表已经建单。重复查看复用同一意图；失败 Run 不能直接执行、申请审批或更改写动作。后续需新的有效授权上下文，再明确确认。</p>
        <MessageBusinessActions failureContext={{ run_id: run.id }} />
      </>}
  </section>;
}
