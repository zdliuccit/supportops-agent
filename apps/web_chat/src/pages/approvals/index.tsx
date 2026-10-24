import { useEffect, useRef, useState } from "react";
import { getIdentity, listApprovals } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { PageHeader } from "@/components/PageHeader";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { ApprovalDetailDrawer, ApprovalStatusBadge, approvalLabels } from "./ApprovalDetailDrawer";
import { MockResourceGrantPanel } from "./MockResourceGrantPanel";
import type { ApprovalStatus, ApprovalSummary } from "@/types";

/** 本人申请与指定资源审批待办；权限由服务端逐次判断。 */
export function ApprovalWorkbenchPage() {
  const [items, setItems] = useState<ApprovalSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 });
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [admin, setAdmin] = useState(false);
  const sequence = useRef(0);
  const contentRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  async function load(page = pagination, state = filter) {
    const current = ++sequence.current;
    setLoading(true); setError(null); setItems([]);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => listApprovals(token, { page: page.current, pageSize: page.pageSize, state: state === "all" ? undefined : state })));
      if (current === sequence.current) { setItems(response.value.items); setTotal(response.value.total); }
    } catch (cause) { if (current === sequence.current) { setTotal(0); setError(cause instanceof Error ? cause.message : "读取审批失败"); } }
    finally { if (current === sequence.current) setLoading(false); }
  }
  useEffect(() => { void load(); let active = true;
    void withRefreshedToken((token) => getIdentity(token)).then((response) => { if (active) setAdmin(response.value.roles.includes("platform_admin")); }).catch(() => { if (active) setAdmin(false); });
    return () => { active = false; sequence.current += 1; }; }, []);
  const columns: AppTableColumn<ApprovalSummary>[] = [
    { title: "工具", dataIndex: "tool_id", key: "tool" },
    { title: "审批状态", key: "status", render: (_, item) => <ApprovalStatusBadge status={item.status} /> },
    { title: "期限", key: "expires", render: (_, item) => new Date(item.expires_at).toLocaleString() },
    { title: "操作", key: "actions", render: (_, item) => <Button size="sm" variant="outline" onClick={(event) => { triggerRef.current = event.currentTarget; setSelectedId(item.id); }}>查看审批</Button> },
  ];
  return <div ref={contentRef} tabIndex={-1} aria-label="审批工作台内容" className="min-w-0 space-y-5 outline-none [overflow-wrap:anywhere]"><PageHeader title="审批工作台" description="隔离 Mock：只显示本人申请或显式指定给你的项目审批。批准与业务复核分别记录。" />
    <ListToolbar loading={loading} onRefresh={() => void load()} filters={<Select value={filter} onValueChange={(value) => { setFilter(value); const page = { ...pagination, current: 1 }; setPagination(page); void load(page, value); }}>
      <SelectTrigger className="w-full sm:w-64" aria-label="筛选审批状态"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部审批状态</SelectItem>
        {(Object.keys(approvalLabels) as ApprovalStatus[]).map((status) => <SelectItem key={status} value={status}>{approvalLabels[status]}</SelectItem>)}</SelectContent></Select>} />
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <div className="relative min-h-36"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="当前权限审批列表"
      pagination={{ ...pagination, total, onChange: (current, pageSize) => { const page = { current, pageSize }; setPagination(page); void load(page); } }} />
      {loading && <ListLoadingOverlay label="正在读取审批待办…" />}</div>
    {admin && <MockResourceGrantPanel />}
    <ApprovalDetailDrawer approvalId={selectedId} onClose={() => setSelectedId(null)} onChanged={() => void load()} onCloseAutoFocus={(event) => {
      event.preventDefault();
      (triggerRef.current?.isConnected ? triggerRef.current : contentRef.current)?.focus({ preventScroll: true });
    }} />
  </div>;
}
