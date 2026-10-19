import { useEffect, useRef, useState } from "react";

import { getRunHttpMcpDispatch, listRunHttpMcpDispatches } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { getStoredAccessToken, subscribeAuthChanges, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { HttpMcpDispatchSummary } from "@/types";

const labels: Record<HttpMcpDispatchSummary["status"], string> = {
  authorized: "已授权，尚未领取", claimed: "已领取，取密结果未确认", bound: "凭据已绑定（非业务回执）",
  blocked: "已阻止绑定", revoked: "提供者已确认撤销",
};

function DispatchStatus({ item }: { item: HttpMcpDispatchSummary }) {
  return <StatusBadge value={["authorized", "claimed", "bound"].includes(item.status) ? "pending" : "unknown"}
    label={labels[item.status]} />;
}

/** 原派发查询只在展开时读取；按Run和当前登录会话重新挂载隔离状态。 */
export function RunHttpMcpDispatches({ runId }: { runId: string }) {
  const [token, setToken] = useState(getStoredAccessToken);
  useEffect(() => subscribeAuthChanges(() => setToken(getStoredAccessToken())), []);
  if (!token || getStoredAccessToken() !== token) return null;
  return <DispatchPanel key={runId} runId={runId} token={token} />;
}

function DispatchPanel({ runId, token }: { runId: string; token: string }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<HttpMcpDispatchSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<HttpMcpDispatchSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sequence = useRef(0);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => { live.current = false; sequence.current += 1; };
  }, []);

  function current(id: number) {
    return live.current && sequence.current === id && getStoredAccessToken() === token;
  }

  async function authenticated<T>(operation: (accessToken: string) => Promise<T>) {
    const result = await withAuthenticatedToken((accessToken) => {
      if (accessToken !== token || !live.current) throw new Error("登录会话已变化，请重新打开派发记录。");
      return operation(accessToken);
    });
    return result.value;
  }

  async function loadPage(next = pagination) {
    const id = ++sequence.current;
    setOpen(true); setLoading(true); setError(null); setItems([]); setTotal(0);
    setSelectedId(null); setDetail(null); setPagination(next);
    try {
      const result = await delayRequest(() => {
        if (!current(id)) throw new Error("本次查询已失效。");
        return authenticated((accessToken) => listRunHttpMcpDispatches(accessToken, runId, next.current, next.pageSize));
      });
      if (current(id)) { setItems(result.items); setTotal(result.total); }
    } catch (cause) {
      if (current(id)) setError(cause instanceof Error ? cause.message : "读取原派发记录失败");
    } finally { if (current(id)) setLoading(false); }
  }

  async function loadDetail(dispatchId: string) {
    const id = ++sequence.current;
    setSelectedId(dispatchId); setDetail(null); setError(null); setLoading(true);
    try {
      const result = await authenticated((accessToken) => getRunHttpMcpDispatch(accessToken, runId, dispatchId));
      if (current(id)) setDetail(result);
    } catch (cause) {
      if (current(id)) {
        setItems([]); setTotal(0);
        setError(cause instanceof Error ? cause.message : "读取原派发详情失败");
      }
    } finally { if (current(id)) setLoading(false); }
  }

  function close() {
    sequence.current += 1;
    setOpen(false); setItems([]); setTotal(0); setSelectedId(null); setDetail(null);
    setLoading(false); setError(null);
  }

  const columns: AppTableColumn<HttpMcpDispatchSummary>[] = [
    { title: "原派发", key: "id", render: (_, item) => <Button variant="ghost" size="sm" disabled={loading}
      aria-label={`查看原派发 ${item.id}`} onClick={() => void loadDetail(item.id)}>{item.id.slice(0, 8)}…</Button> },
    { title: "本地状态", key: "status", render: (_, item) => <DispatchStatus item={item} /> },
    { title: "查询时有效期", key: "is_expired", render: (_, item) => item.is_expired ? "派发已过期" : "尚未到派发截止" },
    { title: "保存时间", key: "created_at", render: (_, item) => new Date(item.created_at).toLocaleString() },
  ];

  return <>
    <Button variant="outline" size="sm" onClick={() => void loadPage({ current: 1, pageSize: 20 })}>查看原 HTTP MCP 派发</Button>
    <AppDrawer open={open && getStoredAccessToken() === token} onOpenChange={(value) => { if (!value) close(); }}
      title="原 HTTP MCP 派发记录" description="只读查询本人本轮记录；凭据绑定、过期与撤销分别记录，均不确认远端业务成功。"
      showConfirm={false} cancelText="关闭" className="sm:max-w-3xl">
      <div className="space-y-4 text-sm">
        <p className="break-all text-xs text-muted-foreground">Run：{runId}</p>
        <p className="text-muted-foreground">没有记录也不能证明远端未执行。查询不会取密、重发或撤销原请求；结果未知时保留原派发。</p>
        <ListToolbar title={selectedId ? "原派发详情" : "原派发列表"} loading={loading}
          onRefresh={() => { if (selectedId) void loadDetail(selectedId); else void loadPage(); }} />
        {error && <p role="alert" className="text-red-600">{error}</p>}
        {selectedId && <Button variant="outline" size="sm" disabled={loading} onClick={() => void loadPage()}>返回当前页</Button>}
        <div className="relative min-h-40" aria-busy={loading}>
          {loading && <ListLoadingOverlay label="正在查询原派发…" />}
          {selectedId ? detail && <section aria-label="原派发详情" className="space-y-4">
            <DispatchStatus item={detail} />
            <p role="status">远端业务结果：未由本查询确认；本查询不授予执行许可。</p>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2">
              <dt>原派发</dt><dd className="break-all font-mono">{detail.id}</dd>
              <dt>执行尝试</dt><dd className="break-all font-mono">{detail.attempt_id}</dd>
              <dt>工具许可</dt><dd className="break-all font-mono">{detail.invocation_id}</dd>
              <dt>固定来源修订</dt><dd className="break-all font-mono">{detail.source_record_id}</dd>
              <dt>代次 / 修订</dt><dd>{detail.generation} / {detail.revision}</dd>
              <dt>保存时间</dt><dd>{new Date(detail.created_at).toLocaleString()}</dd>
              <dt>领取时间</dt><dd>{detail.claimed_at ? new Date(detail.claimed_at).toLocaleString() : "尚未领取"}</dd>
              <dt>派发截止</dt><dd>{new Date(detail.expires_at).toLocaleString()}（查询时{detail.is_expired ? "已过期" : "未过期"}）</dd>
              <dt>真实撤销时间</dt><dd>{detail.revoked_at ? new Date(detail.revoked_at).toLocaleString() : "未取得提供者撤销确认"}</dd>
              <dt>状态更新时间</dt><dd>{new Date(detail.updated_at).toLocaleString()}</dd>
              <dt>本次查询时间</dt><dd>{new Date(detail.observed_at).toLocaleString()}</dd>
            </dl>
            <p className="text-muted-foreground">领取记录不证明取密完成；凭据已绑定不证明调用完成；过期不证明令牌已撤销。时间仅表示服务器记录的事实。</p>
          </section> : <AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="本人原 HTTP MCP 派发"
            emptyText={error ? "当前记录不可用，请刷新查询" : loading ? "正在查询" : "本轮暂无可见派发记录，不能据此认定远端未执行"}
            pagination={{ ...pagination, total, onChange: (currentPage, pageSize) => void loadPage({ current: currentPage, pageSize }) }} />}
        </div>
      </div>
    </AppDrawer>
  </>;
}
