import { useEffect, useRef, useState } from "react";
import { getActionEscalation, listActionEscalations } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { AppDrawer } from "@/components/AppDrawer";
import { GlobalLoading } from "@/components/GlobalLoading";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { EscalationPackageSummary, EscalationPackageDetail } from "@/types";

/** 升级历史和来源详情分开读取；已观察字段不冒充已确认根因。 */
export function EscalationPackagePanel({ actionId, runId }: { actionId: string; runId: string }) {
  const [items, setItems] = useState<EscalationPackageSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<EscalationPackageDetail | null>(null);
  const sequence = useRef(0);
  const detailSequence = useRef(0);
  async function load(pagination = page) {
    const current = ++sequence.current;
    setLoading(true); setError(null); setItems([]); setTotal(0);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => listActionEscalations(token, actionId, runId, pagination.current, pagination.pageSize)));
      if (current === sequence.current) { setItems(response.value.items); setTotal(response.value.total); }
    } catch (cause) { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "读取升级历史失败"); }
    finally { if (current === sequence.current) setLoading(false); }
  }
  async function read(id: string) {
    const current = ++detailSequence.current;
    setSelectedId(id); setDetail(null); setDetailError(null); setDetailLoading(true);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => getActionEscalation(token, actionId, id)));
      if (current === detailSequence.current) setDetail(response.value);
    } catch (cause) { if (current === detailSequence.current) setDetailError(cause instanceof Error ? cause.message : "读取升级详情失败"); }
    finally { if (current === detailSequence.current) setDetailLoading(false); }
  }
  useEffect(() => { const first = { current: 1, pageSize: 20 }; setPage(first); setSelectedId(null); setDetail(null); void load(first);
    return () => { sequence.current += 1; detailSequence.current += 1; }; }, [actionId, runId]);
  const columns: AppTableColumn<EscalationPackageSummary>[] = [
    { title: "保存时间", key: "time", render: (_, item) => new Date(item.created_at).toLocaleString() },
    { title: "操作", key: "actions", render: (_, item) => <Button size="sm" variant="outline" onClick={() => void read(item.id)}>查看排查升级包</Button> },
  ];
  return <section aria-label="排查升级历史" className="space-y-2"><ListToolbar title="升级包列表" loading={loading} onRefresh={() => void load()} />
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <div className="relative min-h-24"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="排查升级包"
      emptyText="尚无升级包" pagination={{ ...page, total, onChange: (current, pageSize) => { const next = { current, pageSize }; setPage(next); void load(next); } }} />
      {loading && <ListLoadingOverlay label="正在读取升级包列表…" />}</div>
    <AppDrawer open={selectedId !== null} onOpenChange={(open) => { if (!open) { detailSequence.current += 1; setSelectedId(null); setDetail(null); } }}
      title="排查升级包" description="隔离 Mock 来源；观察事实与根因确认分开，详情按当前权限显示。" showConfirm={false} cancelText="关闭">
      {detailLoading ? <GlobalLoading fullScreen={false} size="sm" label="正在校验升级来源…" /> : <div className="space-y-4 text-sm">
        {detailError && <p role="alert" className="text-red-600">{detailError}</p>}
        {detail && <><h3 className="font-medium">问题</h3><p className="whitespace-pre-wrap break-words">{detail.payload.question}</p>
          <p>形成时间：{new Date(detail.payload.captured_at).toLocaleString()}</p>
          <h3 className="font-medium">已确认模拟原因</h3>{detail.payload.confirmed_causes.length === 0 ? <p className="text-muted-foreground">尚未确认根因。</p> : detail.payload.confirmed_causes.map((candidate) => <p key={candidate.code}>{candidate.description}</p>)}
          <h3 className="font-medium">未确认候选</h3>{detail.payload.unconfirmed_candidates.map((candidate) => <p key={candidate.code}>{candidate.description}</p>)}
          <h3 className="font-medium">待处理事项</h3>{detail.payload.unresolved_items.map((item, index) => <p key={index}>{item}</p>)}
          <h3 className="font-medium">查询观察事实（不等于确认根因）</h3>{detail.payload.observed_facts.map((fact, index) => <p key={index} className="break-words">{fact.field}：{String(fact.value ?? "未知")} · {new Date(fact.observed_at).toLocaleString()}</p>)}
          <p>记录排查步骤 {detail.payload.steps.length} 项 · 当前有效引用 {detail.payload.evidence_ids.length} 项</p>
          <p className="break-all text-xs text-muted-foreground">Run：{detail.run_id} · Trace：{detail.payload.trace_id}</p>
        </>}
        {selectedId && <Button variant="outline" onClick={() => void read(selectedId)}>重新校验来源与权限</Button>}
      </div>}
    </AppDrawer>
  </section>;
}
