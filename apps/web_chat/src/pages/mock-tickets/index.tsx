import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { listMockTickets } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { PageHeader } from "@/components/PageHeader";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/StatusBadge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { MockTicket } from "@/types";

export const ticketStatusLabels: Record<MockTicket["status"], string> = { open: "待处理", in_progress: "人工处理中", escalated: "已升级", closed: "已关闭" };

/** 本人工单与显式获准的客服队列，权限过滤由服务端完成。 */
export function MockTicketWorkbenchPage() {
  const [items, setItems] = useState<MockTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [state, setState] = useState("all");
  const [keywords, setKeywords] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sequence = useRef(0);
  async function load(pagination = page, status = state) {
    const current = ++sequence.current;
    setLoading(true); setError(null); setItems([]);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => listMockTickets(token, { page: pagination.current, pageSize: pagination.pageSize,
        state: status === "all" ? undefined : status, keywords })));
      if (current === sequence.current) { setItems(response.value.items); setTotal(response.value.total); }
    } catch (cause) { if (current === sequence.current) { setTotal(0); setError(cause instanceof Error ? cause.message : "读取工单失败"); } }
    finally { if (current === sequence.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, []);
  const columns: AppTableColumn<MockTicket>[] = [
    { title: "工单", key: "ticket", render: (_, ticket) => <Link className="inline-block max-w-xs [overflow-wrap:anywhere] text-emerald-700 underline underline-offset-4" to={`/mock-tickets/${encodeURIComponent(ticket.external_id)}`}>{ticket.title}</Link> },
    { title: "状态", key: "status", render: (_, ticket) => <StatusBadge value={ticket.status === "closed" ? "completed" : "pending"} label={ticketStatusLabels[ticket.status]} /> },
    { title: "资源主体", key: "owner", render: (_, ticket) => ticket.owner_name ?? "历史用户" },
    { title: "处理人", key: "handler", render: (_, ticket) => ticket.handler_name ?? "尚未认领" },
    { title: "更新时间", key: "updated", render: (_, ticket) => new Date(ticket.updated_at).toLocaleString() },
  ];
  return <div className="space-y-4"><PageHeader title="Mock 工单工作台" description="本人工单与明确授权的隔离项目；认领后冻结关联问题的 Agent 写动作。未连接真实工单系统。" />
    <ListToolbar loading={loading} onRefresh={() => void load()} filters={<>
      <Input className="w-56 max-w-full" aria-label="搜索工单标题" value={keywords} onChange={(event) => setKeywords(event.target.value)} placeholder="请输入工单标题" onKeyDown={(event) => { if (event.key === "Enter" && !loading) { const first = { ...page, current: 1 }; setPage(first); void load(first); } }} />
      <Button variant="outline" disabled={loading} onClick={() => { const first = { ...page, current: 1 }; setPage(first); void load(first); }}>查询</Button>
      <Select value={state} onValueChange={(value) => { setState(value); const first = { ...page, current: 1 }; setPage(first); void load(first, value); }}>
        <SelectTrigger className="w-44" aria-label="工单状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem>
          {(Object.keys(ticketStatusLabels) as MockTicket["status"][]).map((status) => <SelectItem key={status} value={status}>{ticketStatusLabels[status]}</SelectItem>)}</SelectContent></Select>
    </>} />
    {error && <p role="alert" className="text-sm text-red-600 [overflow-wrap:anywhere]">{error}</p>}
    <div className="relative min-h-36"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="当前权限 Mock 工单列表" emptyText="暂无当前权限工单"
      pagination={{ ...page, total, onChange: (current, pageSize) => { const next = { current, pageSize }; setPage(next); void load(next); } }} />
      {loading && <ListLoadingOverlay label="正在读取当前权限工单…" />}</div>
  </div>;
}
