import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getRunRoutingOutcome, listRunAttempts } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { useIdentity } from "@/components/IdentityContext";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { getStoredAccessToken, subscribeAuthChanges, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { RoutingExecutionOutcomeView } from "@/types";
import { requireRoutingAttempts, requireRoutingOutcome, type RoutingAttemptSummary } from "./routingOutcomeContract";

const attemptLabels: Record<RoutingAttemptSummary["status"], string> = { running: "运行中", completed: "已完成",
  failed: "失败", cancelled: "已取消", expired: "已过期", quarantined: "已隔离" };
const outcomeLabels: Record<RoutingExecutionOutcomeView["status"], string> = { completed: "完成记录已核对",
  failed: "失败记录已核对", cancelled: "取消记录已核对", quarantined: "隔离记录已核对", unconfirmed: "尚不可确认" };

function message(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 404) return "未找到原路由记录，或当前无权访问。普通固定模型运行不适用此查询。";
    if (cause.status === 401 || cause.status === 403) return "登录会话或管理员权限已变化，请重新打开运行详情。";
    if (cause.status === 503) return "查证服务暂不可用，请稍后重新查询原记录。";
    return "读取失败，请重新查询原记录。";
  }
  return cause instanceof Error ? cause.message : "查证失败，请重试读取。";
}

export function RunRoutingOutcome({ runId }: { runId: string }) {
  const identity = useIdentity(), token = getStoredAccessToken();
  if (!identity?.roles.includes("platform_admin")) return <p role="alert">需要平台管理员权限才能查证路由结果。</p>;
  return token ? <RoutingOutcomePanel key={`${runId}:${token}`} runId={runId} token={token} /> : <p role="alert">请重新登录。</p>;
}

function RoutingOutcomePanel({ runId, token }: { runId: string; token: string }) {
  const [items, setItems] = useState<RoutingAttemptSummary[]>([]), [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loaded, setLoaded] = useState(false), [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null), [sessionValid, setSessionValid] = useState(true);
  const [selected, setSelected] = useState<RoutingAttemptSummary | null>(null);
  const [outcome, setOutcome] = useState<RoutingExecutionOutcomeView | null>(null);
  const [queryLoading, setQueryLoading] = useState(false), [queryError, setQueryError] = useState<string | null>(null);
  const live = useRef(true), listSequence = useRef(0), querySequence = useRef(0);
  const listBusy = useRef(false), queryBusy = useRef(false);
  const active = () => live.current && getStoredAccessToken() === token;
  const invalidateSession = useCallback(() => {
    live.current = false; listSequence.current += 1; querySequence.current += 1;
    setSessionValid(false); setItems([]); setOutcome(null); setSelected(null);
  }, []);

  useEffect(() => {
    live.current = true;
    const unsubscribe = subscribeAuthChanges(invalidateSession);
    return () => { live.current = false; listSequence.current += 1; querySequence.current += 1; unsubscribe(); };
  }, [invalidateSession]);

  async function request<T>(operation: (currentToken: string) => Promise<T>): Promise<T> {
    const response = await withAuthenticatedToken((currentToken) => {
      if (!active() || currentToken !== token) throw new Error("登录会话已变化，请重新打开运行详情。");
      return operation(currentToken);
    });
    if (!active() || response.token !== token) throw new Error("登录会话已变化。");
    return response.value;
  }

  async function load(next = page) {
    if (!active() || listBusy.current) return;
    listBusy.current = true;
    const sequence = ++listSequence.current;
    querySequence.current += 1; queryBusy.current = false;
    setPage(next); setLoading(true); setLoaded(true); setError(null); setItems([]); setTotal(0);
    setSelected(null); setOutcome(null); setQueryError(null); setQueryLoading(false);
    try {
      const raw = await delayRequest(() => {
        if (!active() || sequence !== listSequence.current) throw new Error("读取已取消。");
        return request((currentToken) => listRunAttempts(currentToken, runId, { page: next.current, pageSize: next.pageSize }));
      });
      if (!active() || sequence !== listSequence.current) return;
      const value = requireRoutingAttempts(raw, runId, next.current, next.pageSize);
      setItems(value.items); setTotal(value.total);
    } catch (cause) {
      if (active() && sequence === listSequence.current) {
        if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) invalidateSession();
        else setError(message(cause));
      }
    }
    finally { if (active() && sequence === listSequence.current) { listBusy.current = false; setLoading(false); } }
  }

  async function query(attempt: RoutingAttemptSummary) {
    if (!active() || listBusy.current || queryBusy.current) return;
    queryBusy.current = true;
    const sequence = ++querySequence.current;
    setSelected(attempt); setOutcome(null); setQueryError(null); setQueryLoading(true);
    try {
      const raw = await request((currentToken) => getRunRoutingOutcome(currentToken, runId, attempt.id));
      if (!active() || sequence !== querySequence.current) return;
      setOutcome(requireRoutingOutcome(raw, attempt));
    } catch (cause) {
      if (active() && sequence === querySequence.current) {
        if (cause instanceof ApiError && (cause.status === 401 || cause.status === 403)) invalidateSession();
        else setQueryError(message(cause));
      }
    }
    finally { if (active() && sequence === querySequence.current) { queryBusy.current = false; setQueryLoading(false); } }
  }

  const columns: AppTableColumn<RoutingAttemptSummary>[] = [
    { title: "代次", dataIndex: "generation" },
    { title: "原执行尝试", key: "attempt", render: (_, item) => <span className="break-all font-mono text-xs">{item.id}</span> },
    { title: "记录状态", key: "status", render: (_, item) => <StatusBadge value={item.status} label={attemptLabels[item.status]} /> },
    { title: "操作", key: "action", align: "right", render: (_, item) => <Button variant="outline" size="sm" disabled={loading || queryLoading}
      onClick={() => void query(item)} aria-label={`查证第${item.generation}代路由结果`}>只读查证</Button> },
  ];
  if (!sessionValid || !active()) return <p role="alert">登录会话或管理员权限已变化，请重新打开运行详情。</p>;
  return <section aria-label="路由结果只读查证" className="space-y-3 rounded-xl bg-white p-3 text-sm">
    <h3 className="font-medium">路由结果查证</h3>
    <p className="text-xs text-muted-foreground">适用于采用路由策略的运行。选择原执行尝试读取持久记录，不会停止、恢复或重新执行。</p>
    {!loaded ? <Button variant="outline" size="sm" onClick={() => void load()}>加载执行尝试</Button> : <>
      <ListToolbar title="执行尝试列表" loading={loading} onRefresh={() => void load()} />
      {error && <div role="alert" className="space-y-2 text-red-600"><p>{error}</p><Button variant="outline" size="sm" disabled={loading} onClick={() => void load()}>重试读取</Button></div>}
      <div className="relative min-h-32"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="路由查证执行尝试"
        emptyText={error ? "执行尝试暂不可用" : "原Run暂无执行尝试"}
        pagination={{ ...page, total, onChange: (current, pageSize) => { void load({ current, pageSize }); } }} />
        {loading && <ListLoadingOverlay label="正在读取执行尝试…" />}</div>
    </>}
    {selected && <section className="space-y-2" aria-label={`第${selected.generation}代查证结果`} aria-busy={queryLoading}>
      <h4 className="font-medium">第 {selected.generation} 代查证结果</h4>
      {queryLoading && <p role="status">正在读取原路由结果…</p>}
      {queryError && <div role="alert" className="space-y-2 text-red-600"><p>{queryError}</p><Button variant="outline" size="sm" disabled={queryLoading} onClick={() => void query(selected)}>重新查证</Button></div>}
      {outcome && <div className="space-y-2" role="status">
        <StatusBadge value={outcome.status} label={outcomeLabels[outcome.status]} />
        <p>{outcome.status === "unconfirmed" ? "当前记录不足以确认结果，请稍后重新查询。" : "已核对原持久记录；此查询不授予执行或恢复许可。"}</p>
        <p className="break-all text-xs">原尝试：{outcome.attempt_id} · 原根：{outcome.root_call_id ?? "尚无已核对的模型执行根"}</p>
        <p className="text-xs">观察时间：{new Date(outcome.observed_at).toLocaleString("zh-CN")}</p>
        {outcome.code && <p className="break-all font-mono text-xs">原因代码：{outcome.code}</p>}
        <Button variant="outline" size="sm" disabled={queryLoading} onClick={() => void query(selected)}>重新查证</Button>
      </div>}
    </section>}
  </section>;
}
