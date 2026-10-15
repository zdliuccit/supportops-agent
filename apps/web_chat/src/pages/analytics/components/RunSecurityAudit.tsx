import { useEffect, useRef, useState } from "react";
import { getRunSecurityEvents } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { delayRequest } from "@/lib/delayRequest";
import { withRefreshedToken } from "@/lib/auth";
import type { RunSecurityEvent } from "@/types";

/** 安全审计只读追加事实；权限和稳定分页由服务端先行过滤。 */
export function RunSecurityAudit({ runId }: { runId: string }) {
  const [items, setItems] = useState<RunSecurityEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sequence = useRef(0);
  async function load(pagination = page) {
    const current = ++sequence.current;
    setLoading(true); setError(null); setItems([]); setTotal(0);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => getRunSecurityEvents(token, runId, pagination.current, pagination.pageSize)));
      if (sequence.current === current) { setItems(response.value.items); setTotal(response.value.total); }
    } catch (cause) { if (sequence.current === current) setError(cause instanceof Error ? cause.message : "读取安全审计失败"); }
    finally { if (sequence.current === current) setLoading(false); }
  }
  useEffect(() => { const first = { current: 1, pageSize: 20 }; setPage(first); void load(first);
    return () => { sequence.current += 1; }; }, [runId]);
  const columns: AppTableColumn<RunSecurityEvent>[] = [
    { title: "时间", key: "time", render: (_, item) => new Date(item.occurred_at).toLocaleString() },
    { title: "事件", dataIndex: "event_type", key: "event" },
    { title: "状态 / 原因", key: "status", render: (_, item) => `${item.metadata.status ?? "—"} ${item.metadata.code ?? ""}` },
    { title: "关联事实", key: "links", render: (_, item) => <p className="max-w-md break-all text-xs">{item.metadata.receipt_id ? `回执 ${item.metadata.receipt_id} · ${item.metadata.receipt_status}`
      : item.metadata.approval_id ? `审批 ${item.metadata.approval_id}` : item.metadata.invocation_id ? `调用 ${item.metadata.invocation_id}` : item.metadata.action_id ? `动作 ${item.metadata.action_id}` : "—"}</p> },
  ];
  return <section aria-label="安全审计" className="space-y-2 rounded-2xl bg-white p-4">
    <p className="text-xs text-muted-foreground">安全事实来自持久化追加事件，遥测失败不覆盖产品终态；仅展示脱敏元数据。</p>
    <ListToolbar title="安全审计列表" loading={loading} onRefresh={() => void load()} />
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <div className="relative min-h-24"><AppTable columns={columns} dataSource={items} rowKey="sequence" ariaLabel="安全审计追加事件" emptyText="暂无安全审计事件"
      pagination={{ ...page, total, onChange: (current, pageSize) => { const next = { current, pageSize }; setPage(next); void load(next); } }} />
      {loading && <ListLoadingOverlay label="正在读取安全审计…" />}</div>
  </section>;
}
