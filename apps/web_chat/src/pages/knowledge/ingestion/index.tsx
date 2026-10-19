import { useCallback, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { useIdentity } from "@/components/IdentityContext";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { KnowledgeIngestionJob } from "@/types";
import { IngestionCommandDialog } from "./components/IngestionCommandDialog";
import { IngestionCreateDialog } from "./components/IngestionCreateDialog";
import { IngestionCreationStopDialog } from "./components/IngestionCreationStopDialog";
import { IngestionRequestLookupDialog } from "./components/IngestionRequestLookupDialog";
import { IngestionJobDrawer } from "./components/IngestionJobDrawer";
import { IngestionWorkerExecution } from "./components/IngestionWorkerExecution";
import { IngestionDrafts } from "./components/IngestionDrafts";
import { IngestionProvenancePanel } from "./components/IngestionProvenancePanel";
import { useIngestionProvenance } from "./hooks/useIngestionProvenance";
import { useIngestionDrafts } from "./hooks/useIngestionDrafts";
import { useIngestionJobs } from "./hooks/useIngestionJobs";
import { useIngestionCreation } from "./hooks/useIngestionCreation";
import { useIngestionRequestLookup } from "./hooks/useIngestionRequestLookup";
import { useIngestionGovernance } from "./hooks/useIngestionGovernance";
import { IngestionGovernanceDialog } from "./components/IngestionGovernanceDialog";
import { IngestionGovernancePanel } from "./components/IngestionGovernancePanel";
import { IngestionWithdrawalDialog } from "./components/IngestionWithdrawalDialog";
import { canExecuteIngestion, ingestionContractSupported, ingestionStatusLabel, ingestionStatusLabels,
  ingestionTime, isIngestionStatus, sourceReady } from "./lib/presentation";

export function KnowledgeIngestionPage() {
  const identity = useIdentity();
  const administrator = identity?.roles.includes("platform_admin") ?? false;
  return <KnowledgeIngestionWorkspace key={`${identity?.tenant_id}:${identity?.user_id}:${administrator}`}
    tenantId={identity?.tenant_id} subjectId={identity?.user_id} administrator={administrator} />;
}

function KnowledgeIngestionWorkspace({ tenantId, subjectId, administrator }: {
  tenantId: string | undefined; subjectId: string | undefined; administrator: boolean;
}) {
  const jobs = useIngestionJobs(subjectId, administrator);
  const [governanceBusy, setGovernanceBusy] = useState(false);
  const [workerBusy, setWorkerBusy] = useState(false);
  const [workerIntentJobs, setWorkerIntentJobs] = useState<Set<string>>(() => new Set());
  const rememberWorkerIntent = useCallback((jobId: string) => {
    setWorkerIntentJobs((previous) => previous.has(jobId) ? previous : new Set([...previous, jobId]));
  }, []);
  const creation = useIngestionCreation(tenantId, subjectId, administrator, (job) => {
    jobs.selectDetail(job.id); void jobs.load();
  }, jobs.busy || governanceBusy || workerBusy);
  const busy = jobs.busy || governanceBusy || creation.busy || workerBusy;
  const lookup = useIngestionRequestLookup(busy, (jobId) => {
    if (jobId === jobs.selectedId) void jobs.loadDetail();
    else jobs.selectDetail(jobId);
  });
  const columns: AppTableColumn<KnowledgeIngestionJob>[] = [
    { title: "任务", key: "job", width: 220, render: (_, row) => <button type="button"
      className="max-w-56 break-all text-left text-xs font-medium hover:text-emerald-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
      onClick={() => jobs.selectDetail(row.id)} disabled={busy}>{row.id}</button> },
    { title: "知识来源", key: "source", width: 180, render: (_, row) => <span className="break-all">{ingestionContractSupported(row) ? row.source_name ?? row.source_id ?? "—" : "不可确认"}</span> },
    { title: "状态", key: "status", width: 150, render: (_, row) => <StatusBadge
      value={ingestionContractSupported(row) ? row.status === "retry_wait" ? "pending" : row.status : "unknown"}
      label={ingestionStatusLabel(row)} /> },
    { title: "尝试次数", key: "attempts", width: 100, render: (_, row) => ingestionContractSupported(row) ? `${row.attempts ?? "—"} / ${row.max_attempts ?? "—"}` : "—" },
    { title: "已提交草稿 / 复用", key: "counts", width: 155,
      render: (_, row) => ingestionContractSupported(row) ? `${row.counts?.draft_created ?? "—"} / ${row.counts?.unchanged ?? "—"}` : "—" },
    { title: "创建时间", key: "created", width: 180, render: (_, row) => ingestionContractSupported(row) ? ingestionTime(row.created_at) : "—" },
    { title: "固定截止时间", key: "deadline", width: 180, render: (_, row) => ingestionContractSupported(row) ? ingestionTime(row.deadline_at) : "—" },
    { title: "操作", key: "actions", align: "right", width: 190, render: (_, row) => <div className="flex justify-end gap-2">
      <Button variant="outline" size="sm" disabled={busy} onClick={() => jobs.selectDetail(row.id)}>详情</Button>
      <Button variant="outline" size="sm" disabled={busy || !ingestionContractSupported(row) || row.cancel_available !== true}
        title={row.cancel_available && ingestionContractSupported(row) ? undefined : "终态或不可确认的任务不能取消"}
        onClick={() => jobs.requestCommand(row, "cancel")}>取消任务</Button>
    </div> },
  ];
  const detail = jobs.detail?.id === jobs.selectedId ? jobs.detail : null;
  return <div className="space-y-6">
    <PageHeader title="知识导入任务" description="管理本人获准来源的单次处理记录。已有草稿仍按知识生命周期独立审核和发布。"
      actions={<div className="flex flex-wrap gap-3"><Button variant="outline" asChild><Link to="/knowledge"><ArrowLeft className="size-4" aria-hidden="true" />返回知识管理</Link></Button>
        {administrator && <Button variant="outline" disabled={busy}
          title={busy ? "请等待当前操作完成后查询" : undefined}
          onClick={() => lookup.begin(creation.pending?.requestKey ?? "")}>按请求标识查询</Button>}
        {administrator && <Button disabled={busy || (!creation.pending && creation.loading) || !creation.canBegin}
          title={creation.canBegin ? undefined : "需要可恢复的原请求，或本人获准来源及可用会话存储"} onClick={creation.begin}>
          {creation.pending ? "查询待确认创建" : "创建导入任务"}</Button>}</div>} />
    {!administrator ? <p role="alert" className="text-sm text-red-600">需要当前平台管理员身份才能查看本人导入任务。</p>
      : <>
        <div className="space-y-2 text-sm leading-6 text-muted-foreground">
          {creation.pending && <p role="status">本标签页保留一笔待确认创建，请先查询原请求。关闭弹窗或刷新页面不会自动重新创建。</p>}
          {creation.storageError && <p role="alert" className="text-red-600">{creation.storageError}</p>}
          {creation.loading ? <p role="status">正在确认获准来源…</p> : creation.error ? <p role="alert" className="text-red-600">{creation.error}</p>
            : sourceReady(creation.source) ? <p>当前获准来源：{creation.source.source_name ?? creation.source.source_id}，精确清单 {creation.source.object_count} 个文件。批准截止 {ingestionTime(creation.source.valid_until)}。</p>
              : <p>当前没有可用的本人获准来源，请由维护人员确认来源范围和配置。任务历史仍可查询。</p>}
          {sourceReady(creation.source) && !creation.source.create_available && <p>来源剩余批准时间不足以创建新任务，请先完成已有任务或联系维护人员重新确认范围。</p>}
          <p>创建任务和读取来源分别确认；只生成待复核草稿。查询和空列表不读取知识文件，也不会启动后台自动导入。</p>
        </div>
        {jobs.error && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-red-600"><span>{jobs.error}</span>
          <Button variant="outline" size="sm" disabled={jobs.loading || busy} onClick={() => void jobs.load()}>重试读取</Button></div>}
        <section className="rounded-2xl bg-white">
          <ListToolbar onRefresh={() => { void jobs.load(); void creation.load(); }} loading={jobs.loading || creation.loading || busy} filters={
            <Select value={jobs.status} disabled={busy} onValueChange={(value) => {
              if (value !== "all" && !isIngestionStatus(value)) return;
              jobs.changeStatus(value);
            }}><SelectTrigger className="w-48" aria-label="筛选导入任务状态"><SelectValue /></SelectTrigger><SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              {Object.entries(ingestionStatusLabels).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
            </SelectContent></Select>
          } />
          <div className="relative min-h-48"><AppTable columns={columns} dataSource={jobs.result?.items ?? []} rowKey="id"
            ariaLabel="本人知识导入任务列表" scroll={{ x: 1350 }}
            emptyText={jobs.error ? "导入任务暂时无法读取" : "当前条件下暂无本人导入任务"}
            pagination={{ ...jobs.page, total: jobs.result?.total ?? 0, onChange: (current, pageSize) => {
              if (!busy) jobs.changePage(current, pageSize);
            } }} />{jobs.loading && <ListLoadingOverlay label="正在读取本人导入任务…" />}</div>
        </section>
      </>}
    <IngestionJobDrawer open={administrator && jobs.selectedId !== null} job={detail}
      executeAvailable={detail !== null && !workerIntentJobs.has(detail.id) && canExecuteIngestion(detail, creation.source)}
      executeBlockedReason={detail && workerIntentJobs.has(detail.id) ? "已提交或观察到Worker执行意图，请只查询原意图和任务结果，不要切换执行方式" : undefined}
      workerExecution={detail && ingestionContractSupported(detail) ? <IngestionWorkerExecution key={detail.id}
        job={detail} canSubmit={canExecuteIngestion(detail, creation.source) && !jobs.detailLoading}
        busy={jobs.busy || governanceBusy || creation.busy} onBusyChange={setWorkerBusy}
        onIntentObserved={rememberWorkerIntent} /> : null}
      drafts={detail && ingestionContractSupported(detail) ? <IngestionDraftWorkspace key={detail.id} jobId={detail.id}
        busy={busy} onBusyChange={setGovernanceBusy} /> : null}
      loading={jobs.detailLoading || (jobs.selectedId !== null && !detail && !jobs.detailError)}
      error={jobs.detailError} busy={busy} onClose={() => { if (!busy) jobs.selectDetail(null); }}
      onReload={() => void jobs.loadDetail()} onLookupRequest={lookup.begin}
      onCommand={(job, kind) => { if (!governanceBusy) jobs.requestCommand(job, kind); }} />
    <IngestionCommandDialog command={jobs.command} busy={jobs.busy} onClose={jobs.closeCommand}
      onConfirm={() => void jobs.confirmCommand(creation.source)} />
    <IngestionCreateDialog snapshot={creation.snapshot} busy={creation.busy} storageError={creation.storageError} onClose={creation.close}
      onCreate={() => void creation.create()} onQuery={() => void creation.query()} onStop={creation.beginStop} />
    <IngestionCreationStopDialog snapshot={creation.stopConfirmation} busy={creation.busy}
      onClose={creation.closeStop} onConfirm={() => void creation.stop()} />
    <IngestionRequestLookupDialog open={administrator && lookup.open} value={lookup.value} fieldError={lookup.fieldError}
      error={lookup.error} loading={lookup.loading} result={lookup.result} busy={busy} onChange={lookup.changeValue}
      onClose={lookup.close} onQuery={() => void lookup.query()} onOpenJob={lookup.openJob} />
  </div>;
}

function IngestionDraftWorkspace({ jobId, busy, onBusyChange }: {
  jobId: string; busy: boolean; onBusyChange: (busy: boolean) => void;
}) {
  const drafts = useIngestionDrafts(jobId, busy);
  const provenance = useIngestionProvenance(jobId, busy);
  const clearContent = useCallback(() => { drafts.clearComparison(); provenance.clear(); },
    [drafts.clearComparison, provenance.clear]);
  const governance = useIngestionGovernance(jobId, busy, onBusyChange, clearContent);
  return <><IngestionDrafts result={drafts.result} page={drafts.page} loading={drafts.loading} error={drafts.error}
    busy={busy} onReload={() => { governance.select(null); provenance.clear(); void drafts.load(); }}
    onPage={(current, pageSize) => { if (!busy) { governance.select(null); provenance.clear(); drafts.changePage(current, pageSize); } }}
    onSelect={(id) => { if (!busy) { provenance.clear(); drafts.selectComparison(id); } }}
    onGovernance={(id) => { if (!busy) { provenance.clear(); governance.select(id); } }}
    onProvenance={(id) => { if (!busy) { governance.select(null); drafts.clearComparison(); provenance.select(id); } }}
    provenance={provenance.selectedId && <IngestionProvenancePanel view={provenance.view}
      loading={provenance.loading} error={provenance.error} busy={busy}
      onReload={() => void provenance.load()} onClose={provenance.clear} />}
    governance={governance.selectedId && <IngestionGovernancePanel view={governance.view} loading={governance.loading}
      error={governance.error} busy={busy} onReload={() => void governance.load()} onClose={() => governance.select(null)}
      onEdit={governance.beginEdit} onWithdraw={governance.beginWithdrawal} />}
    selectedId={drafts.selectedId} comparison={drafts.comparison} comparisonLoading={drafts.comparisonLoading}
    comparisonError={drafts.comparisonError} onReloadComparison={() => void drafts.loadComparison()} />
    <IngestionGovernanceDialog snapshot={governance.editing} owner={governance.owner} due={governance.due}
      errors={governance.errors} busy={governance.saving} onOwner={governance.changeOwner} onDue={governance.changeDue}
      onClose={governance.closeEdit} onSave={() => void governance.save()} loadOwners={governance.loadOwners} />
    <IngestionWithdrawalDialog snapshot={governance.withdrawal} reason={governance.withdrawalReason}
      error={governance.withdrawalError} busy={governance.saving} onReason={governance.changeWithdrawalReason}
      onClose={governance.closeWithdrawal} onConfirm={() => void governance.confirmWithdrawal()} /></>;
}
