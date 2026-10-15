import { useCallback, useEffect, useRef, useState } from "react";
import { commandErasureRequest, listErasureRequests } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { ErasureRequestSummary, PaginatedListResponse } from "@/types";
import { categoryLabels, requestStatus } from "./lib";
import { CreateRequestDialog } from "./components/CreateRequestDialog";
import { RequestDetailDrawer } from "./components/RequestDetailDrawer";
import { RequestCommandDialog, type RequestCommandTarget } from "./components/RequestCommandDialog";

export function DataRequestsPage() {
  const [result, setResult] = useState<PaginatedListResponse<ErasureRequestSummary> | null>(null);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false), [detailId, setDetailId] = useState<string | null>(null);
  const [target, setTarget] = useState<RequestCommandTarget | null>(null), [busy, setBusy] = useState(false);
  const sequence = useRef(0), mutation = useRef(false), mounted = useRef(false);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true); setError(null); setResult(null);
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => listErasureRequests(token, { page: page.current, pageSize: page.pageSize })));
      if (current === sequence.current) setResult(response.value);
    } catch (cause) { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "本人申请读取失败"); }
    finally { if (current === sequence.current) setLoading(false); }
  }, [page.current, page.pageSize]);
  const latestLoad = useRef(load); latestLoad.current = load;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [load]);
  async function command() {
    if (!target || mutation.current) return;
    mutation.current = true; setBusy(true);
    const selected = target;
    try {
      await withAuthenticatedToken((token) => commandErasureRequest(token, selected.request.id, selected.command, selected.request.revision));
      notify.success(selected.command === "preview" ? "一次影响预览已保存；未批准或擦除数据。" : "本人申请已撤回，原记录保留。");
      if (mounted.current) { setTarget(null); if (selected.command === "preview") setDetailId(selected.request.id); }
    } catch (cause) {
      notify.error(cause, "操作未确认，请刷新申请状态后重试");
      if (mounted.current) setTarget(null);
    } finally {
      mutation.current = false;
      if (mounted.current) { setBusy(false); await latestLoad.current(); }
    }
  }
  const columns: AppTableColumn<ErasureRequestSummary>[] = [
    { title: "申请类别", key: "categories", render: (_, row) => <span className="block max-w-sm">{row.categories.map((value) => categoryLabels[value]).join("、")}</span> },
    { title: "状态", key: "status", render: (_, row) => <StatusBadge value={row.status === "requested" ? "pending" : row.status === "previewed" ? "active" : "unknown"} label={requestStatus[row.status]} /> },
    { title: "申请时间", key: "created", render: (_, row) => new Date(row.created_at).toLocaleString() },
    { title: "操作", key: "actions", align: "right", render: (_, row) => <div className="flex flex-wrap justify-end gap-2">
      <Button variant="ghost" size="sm" disabled={busy || loading} onClick={() => setDetailId(row.id)}>详情</Button>
      {row.status === "requested" && <Button variant="outline" size="sm" disabled={busy || loading} onClick={() => setTarget({ request: row, command: "preview" })}>生成预览</Button>}
      {row.status !== "withdrawn" && <Button variant="outline" size="sm" disabled={busy || loading} onClick={() => setTarget({ request: row, command: "withdraw" })}>撤回申请</Button>}
    </div> },
  ];
  return <div className="space-y-6">
    <PageHeader title="本人数据申请" description="保存本人数据权益意向，查看关联范围和未知依赖；当前仅提供元数据预览。"
      actions={<Button disabled={busy || creating} onClick={() => setCreating(true)}>新建申请</Button>} />
    <p className="text-sm text-muted-foreground">保留规则、法律保留和独立维护流程仍需确认；预览不会停止运行、撤销动作、关闭账户或删除数据。</p>
    {error && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-red-600"><span>{error}</span><Button variant="outline" disabled={loading} onClick={() => void load()}>重试</Button></div>}
    <section className="rounded-2xl bg-white"><ListToolbar title="本人申请列表" loading={loading} onRefresh={() => void load()} />
      <div className="relative min-h-48"><AppTable columns={columns} dataSource={result?.items ?? []} rowKey="id" ariaLabel="本人数据申请列表"
        emptyText={error ? "申请列表暂时无法读取" : "暂无本人数据申请"}
        pagination={{ ...page, total: result?.total ?? 0, onChange: (current, pageSize) => setPage({ current, pageSize }) }} />
        {loading && <ListLoadingOverlay label="正在读取本人数据申请…" />}</div>
    </section>
    {creating && <CreateRequestDialog onClose={() => setCreating(false)} onCreated={() => { setCreating(false); if (page.current === 1) void load(); else setPage({ ...page, current: 1 }); }} />}
    {detailId && <RequestDetailDrawer key={detailId} id={detailId} onClose={() => setDetailId(null)} />}
    <RequestCommandDialog target={target} busy={busy} onClose={() => setTarget(null)} onConfirm={() => void command()} />
  </div>;
}
