import { useEffect, useRef, useState } from "react";

import { getRunActionAudit } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { ActionAuditSummary, BusinessActionStatus } from "@/types";

const labels: Record<BusinessActionStatus, string> = { proposed: "未执行提案", waiting_approval: "等待审批", queued: "待执行",
  running: "执行中", unknown: "结果未知", reconciling: "对账中", succeeded: "已独立复核", failed: "未完成", needs_attention: "待人工处理", cancelled: "已取消" };

/** 管理员只读控制面审计，不展示用户业务参数或回执正文。 */
export function RunActionAudit({ runId }: { runId: string }) {
  const [items, setItems] = useState<ActionAuditSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  async function load(page = pagination) {
    const current = ++generation.current;
    setLoading(true); setError(null);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => getRunActionAudit(token, runId,
        { page: page.current, pageSize: page.pageSize })));
      if (current === generation.current) { setItems(response.value.items); setTotal(response.value.total); }
    } catch (cause) { if (current === generation.current) { setItems([]); setError(cause instanceof Error ? cause.message : "读取动作审计失败"); } }
    finally { if (current === generation.current) setLoading(false); }
  }
  useEffect(() => { const first = { current: 1, pageSize: 20 }; setPagination(first); setItems([]); setTotal(0); void load(first);
    return () => { generation.current += 1; }; }, [runId]);
  const columns: AppTableColumn<ActionAuditSummary>[] = [
    { title: "工具 / 风险", key: "tool", render: (_, item) => <span>{item.tool_id} · {item.risk_level}</span> },
    { title: "状态", key: "status", render: (_, item) => labels[item.status] },
    { title: "对账次数", dataIndex: "reconciliation_count", key: "count" },
    { title: "原因", key: "error", render: (_, item) => item.error_code ?? "—" },
  ];
  return <section aria-label="关联动作审计" className="space-y-2 rounded-xl bg-white p-3">
    <h3 className="font-medium">关联动作审计</h3><p className="text-xs text-muted-foreground">仅控制面元数据，运行完成不等于业务动作已复核。</p>
    <ListToolbar title="动作审计列表" loading={loading} onRefresh={() => void load()} />
    {error && <div role="alert" className="space-y-2 text-sm text-red-600"><p>{error}</p><Button variant="outline" size="sm" onClick={() => void load()}>重试读取</Button></div>}
    <div className="relative min-h-32"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="关联动作审计"
      pagination={{ ...pagination, total, onChange: (current, pageSize) => { const next = { current, pageSize }; setPagination(next); void load(next); } }} />
      {loading && <ListLoadingOverlay label="正在读取动作审计…" />}</div>
  </section>;
}
