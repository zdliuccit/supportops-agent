import { useEffect, useRef, useState } from "react";
import { commandAgentReleaseRollout, createAgentReleaseRollout, getAgentReleaseRollout, listAgentReleaseRolloutEvents, listAgentReleaseRollouts } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { AgentVersion, ReleaseRolloutCommand, ReleaseRolloutCreate, ReleaseRolloutDetail, ReleaseRolloutEvent, ReleaseRolloutSummary, RolloutCohortMetrics, RolloutObservationCheck } from "@/types";
import { ReleaseRolloutCreateDialog } from "./ReleaseRolloutCreateDialog";
import { ReleaseRolloutCommandDialog, rolloutCommandLabels } from "./ReleaseRolloutCommandDialog";

const statusLabels = { draft: "草稿", active: "运行中", paused: "已暂停", stopped: "已停止" };
const checkLabels = { passed: "通过", failed: "失败", missing: "缺失", waiting: "等待", stale: "不再适用" };
function date(value: string | null): string { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未记录"; }
function short(value: string): string { return `${value.slice(0, 8)}…`; }
function percentage(bps: number): string { return `${(bps / 100).toFixed(2)}%`; }
function badge(status: ReleaseRolloutSummary["status"]) { return <StatusBadge value={status === "stopped" ? "disabled" : status === "paused" ? "pending" : status} label={statusLabels[status]} />; }

/** 灰度不会自动激活或扩大；所有提交使用当前修订且服务器重新判定。 */
export function ReleaseRolloutDrawer({ version, activeVersionId, onClose }: {
  version: AgentVersion; activeVersionId: string | null; onClose: () => void;
}) {
  const [items, setItems] = useState<ReleaseRolloutSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReleaseRolloutSummary | null>(null);
  const [detail, setDetail] = useState<ReleaseRolloutDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [creating, setCreating] = useState(false);
  const [operation, setOperation] = useState<ReleaseRolloutCommand["operation"] | null>(null);
  const [busy, setBusy] = useState(false);
  const sequence = useRef({ list: 0, detail: 0, scope: 0 });
  useEffect(() => { sequence.current.scope += 1; return () => { sequence.current.scope += 1; }; }, []);
  useEffect(() => {
    const current = ++sequence.current.list;
    setLoading(true); setError(null); setItems([]); setTotal(0);
    void delayRequest(() => withAuthenticatedToken((token) => listAgentReleaseRollouts(token, version.agent_id, { page: page.current, pageSize: page.pageSize })))
      .then(({ value }) => { if (current === sequence.current.list) {
        setItems(value.items); setTotal(value.total);
        setSelected((old) => old ? value.items.find((item) => item.id === old.id) ?? old : value.items[0] ?? null);
      } })
      .catch((cause: unknown) => { if (current === sequence.current.list) setError(cause instanceof Error ? cause.message : "灰度列表读取失败"); })
      .finally(() => { if (current === sequence.current.list) setLoading(false); });
    return () => { sequence.current.list += 1; };
  }, [page.current, page.pageSize, revision, version.agent_id]);
  useEffect(() => {
    const current = ++sequence.current.detail;
    setDetail(null); setDetailError(null); setDetailLoading(selected !== null);
    if (!selected) return;
    void delayRequest(() => withAuthenticatedToken((token) => getAgentReleaseRollout(token, version.agent_id, selected.id)))
      .then(({ value }) => { if (current === sequence.current.detail) setDetail(value); })
      .catch((cause: unknown) => { if (current === sequence.current.detail) setDetailError(cause instanceof Error ? cause.message : "灰度详情读取失败"); })
      .finally(() => { if (current === sequence.current.detail) setDetailLoading(false); });
    return () => { sequence.current.detail += 1; };
  }, [selected?.id, revision, version.agent_id]);
  function refresh() { setRevision((value) => value + 1); }
  async function create(payload: ReleaseRolloutCreate) {
    const scope = sequence.current.scope; setBusy(true);
    try {
      const { value } = await withAuthenticatedToken((token) => createAgentReleaseRollout(token, version.agent_id, payload));
      if (scope !== sequence.current.scope) return;
      setCreating(false); setSelected(value); setPage((old) => ({ ...old, current: 1 })); refresh();
      notify.success("已保存灰度草稿；尚未启动或分配候选流量");
    } catch (cause) { if (scope === sequence.current.scope) notify.error(cause, "保存灰度未确认，请刷新列表检查"); }
    finally { if (scope === sequence.current.scope) setBusy(false); }
  }
  const visibleDetail = detail && selected && detail.rollout.id === selected.id && detail.rollout.revision >= selected.revision ? detail : null;
  const current = visibleDetail?.rollout ?? selected;
  async function command(payload: ReleaseRolloutCommand) {
    if (!current) return;
    const scope = sequence.current.scope; setBusy(true);
    try {
      const { value } = await withAuthenticatedToken((token) => commandAgentReleaseRollout(token, version.agent_id, current.id, payload));
      if (scope !== sequence.current.scope) return;
      setSelected(value); setOperation(null); refresh(); notify.success("灰度控制状态已保存；既有会话保留固定版本");
    } catch (cause) {
      if (scope === sequence.current.scope) { notify.error(cause, "灰度操作未确认，请刷新同一记录后检查"); setOperation(null); refresh(); }
    } finally { if (scope === sequence.current.scope) setBusy(false); }
  }
  const columns: AppTableColumn<ReleaseRolloutSummary>[] = [
    { title: "灰度 / 版本", key: "id", render: (_, row) => <span className="text-xs" title={row.id}>{short(row.id)}<span className="block font-mono">{short(row.baseline_version_id)} → {short(row.candidate_version_id)}</span></span> },
    { title: "状态 / 比例", key: "status", render: (_, row) => <div className="space-y-1">{badge(row.status)}<p>{percentage(row.candidate_bps)}</p></div> },
    { title: "轮次 / 修订", key: "epoch", render: (_, row) => `${row.epoch} / ${row.revision}` },
    { title: "最近控制", key: "updated_at", render: (_, row) => date(row.updated_at) },
    { title: "操作", key: "actions", align: "right", render: (_, row) => <Button size="sm" variant="outline" disabled={busy} onClick={() => { setSelected(row); if (row.id === selected?.id) refresh(); }}>查看与控制</Button> },
  ];
  const canCreate = activeVersionId !== null && version.id !== activeVersionId && !version.withdrawn_at && !items.some((item) => item.status !== "stopped") && !error && !loading;
  return <AppDrawer open onOpenChange={(open) => { if (!open && !busy) onClose(); }} title={`v${version.version_number} · 灰度发布`} loading={busy}
    description="新会话按固定主体分组；基线和候选均需完整适用实测，不自动启动、扩大或激活。" className="sm:max-w-6xl" showFooter={false}>
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-sm">当前活动基线：{activeVersionId ? short(activeVersionId) : "暂无"}；拟创建候选：v{version.version_number}</p>
        <Button disabled={!canCreate || busy} onClick={() => setCreating(true)}>保存此候选的灰度草稿</Button></div>
      <p className="text-xs text-muted-foreground">一个Agent只允许一个未停止灰度，候选须比活动基线更新。初始最多10%，每次最多增加10个百分点，灰度最高90%；全量发布需通过“检查并激活”。策略未获服务器批准时只可保存草稿。请先处理已有灰度或加载错误，再创建新草稿。</p>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      <ListToolbar title="灰度记录列表" loading={loading || busy} onRefresh={refresh} />
      <div className="relative min-h-32"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="Agent灰度记录" emptyText={error ? "灰度列表暂不可用" : "尚无灰度记录"}
        pagination={{ ...page, total, onChange: (next, size) => { if (!busy) setPage({ current: size === page.pageSize ? next : 1, pageSize: size }); } }} />
        {loading && <ListLoadingOverlay label="正在读取灰度记录…" />}</div>
      {current && <section className="space-y-4" aria-label="灰度详情与控制"><h3 className="text-sm font-semibold">选中灰度 {short(current.id)} · 修订{current.revision}</h3>
        <ListToolbar title="灰度详情" loading={detailLoading || busy} onRefresh={refresh} />
        {detailError && <p role="alert" className="text-sm text-red-600">{detailError}。可刷新重试；暂停或停止仍以服务器当前修订为准。</p>}
        {detailLoading && <div className="relative min-h-32"><ListLoadingOverlay label="正在读取灰度门禁和观测…" /></div>}
        {visibleDetail && !detailLoading && <RolloutDetails detail={visibleDetail} />}
        <div className="flex flex-wrap justify-end gap-2">{(["start", "pause", "resume", "expand", "stop"] as const).filter((action) => action === "start" ? current.status === "draft" : action === "resume" ? current.status === "paused" : action === "stop" ? current.status !== "stopped" : current.status === "active").map((action) => {
          const allowed = action === "pause" || action === "stop" || (action === "start" ? visibleDetail?.start_allowed : action === "resume" ? visibleDetail?.resume_allowed : visibleDetail?.expand_allowed);
          return <Button key={action} variant={action === "stop" ? "destructive" : action === "pause" ? "outline" : "default"} disabled={busy || detailLoading || !allowed} onClick={() => setOperation(action)}>{rolloutCommandLabels[action]}</Button>;
        })}</div>
        <RolloutEvents key={`${current.id}:${current.revision}:${revision}`} agentId={version.agent_id} rolloutId={current.id} />
      </section>}
    </div>
    {creating && <ReleaseRolloutCreateDialog candidateVersionId={version.id} busy={busy} onClose={() => setCreating(false)} onCreate={create} />}
    {operation && current && <ReleaseRolloutCommandDialog key={`${current.id}:${operation}`} operation={operation} rollout={current} busy={busy} onClose={() => setOperation(null)} onConfirm={command} />}
  </AppDrawer>;
}

function RolloutDetails({ detail }: { detail: ReleaseRolloutDetail }) {
  const metricColumns: AppTableColumn<RolloutCohortMetrics>[] = [
    { title: "组别", key: "selection", render: (_, row) => row.selection === "baseline" ? "基线" : "候选" },
    { title: "运行 / 完成 / 失败", key: "run_count", render: (_, row) => `${row.run_count} / ${row.completed} / ${row.failed}` },
    { title: "等待 / 取消", key: "pending", render: (_, row) => `${row.pending} / ${row.cancelled}` },
    { title: "失败率 / 平均费用", key: "cost", render: (_, row) => `${row.failure_rate === null ? "未知" : `${(row.failure_rate * 100).toFixed(2)}%`} / ${row.average_cost_usd === null ? "未知" : `$${row.average_cost_usd.toFixed(6)}`}` },
    { title: "P95首正文 / 完整", key: "latency", render: (_, row) => `${row.p95_api_first_text_ms ?? "未知"} / ${row.p95_complete_ms ?? "未知"} ms` },
    { title: "缺费用 / 首正文 / 完整 / 结果", key: "missing", render: (_, row) => `${row.missing_cost_count} / ${row.missing_first_text_count} / ${row.missing_complete_count} / ${row.missing_result_count}` },
    { title: "授权拒绝 / 超限", key: "denied", render: (_, row) => `${row.authorization_denial_count} / ${row.truncated ? "是" : "否"}` },
  ];
  const checks: RolloutObservationCheck[] = [...detail.observation.checks];
  for (const [label, readiness] of [["基线", detail.baseline_readiness], ["候选", detail.candidate_readiness]] as const) {
    for (const check of readiness.checks) checks.push({ code: `${label}:${check.code}`, status: check.status === "stale" ? "missing" : check.status, message: `${label}发布门禁：${check.message}${check.status === "stale" ? "（已不再适用）" : ""}` });
  }
  const checkColumns: AppTableColumn<RolloutObservationCheck>[] = [
    { title: "检查", key: "code", render: (_, row) => <span className="break-all font-mono text-xs">{row.code}</span> },
    { title: "状态", key: "status", render: (_, row) => <StatusBadge value={row.status === "waiting" ? "pending" : row.status === "missing" ? "unknown" : row.status} label={checkLabels[row.status]} /> },
    { title: "说明", key: "message", render: (_, row) => <p className="max-w-xl text-xs">{row.message}</p> },
  ];
  return <div className="space-y-4"><p className="text-sm">{statusLabels[detail.rollout.status]} · 候选{percentage(detail.rollout.candidate_bps)} · 轮次{detail.rollout.epoch} · 策略{detail.policy_approved ? "已批准" : "未批准"} · 巡检{detail.supervisor_fresh ? "有效" : "未开始或已过期"}</p>
    <p className="break-all font-mono text-xs text-muted-foreground">策略摘要：{detail.rollout.policy_digest}<br />定义摘要：{detail.rollout.definition_digest}</p>
    <p className="text-xs">最近巡检：{date(detail.rollout.last_checked_at)}；原因：{detail.rollout.reason_code ?? "无"}；最早可考虑扩量：{date(detail.observation.earliest_expansion_at)}</p>
    <p className="break-all text-xs">固定主体：{detail.subject_ids.length ? detail.subject_ids.join("，") : "当前获准使用该Agent的租户主体"}</p>
    {detail.disabled_reasons.length > 0 && <ul className="list-disc space-y-1 pl-5 text-xs text-amber-700">{detail.disabled_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
    <p className="text-xs text-muted-foreground">只统计本轮新会话，缺失不填零。完成状态和结构化结果不证明业务解决；API首正文是服务器可见上界，客户端上报不能批准扩量。Worker只会暂停，所有扩大均需人工操作。</p>
    <AppTable columns={metricColumns} dataSource={[detail.observation.baseline, detail.observation.candidate]} rowKey="selection" ariaLabel="灰度两组观测" scroll={{ x: 1000 }} />
    <AppTable columns={checkColumns} dataSource={checks} rowKey="code" ariaLabel="灰度及两版本发布门禁" scroll={{ x: 700 }} />
    <details><summary className="cursor-pointer text-sm">查看固定观测策略</summary><pre className="overflow-x-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(detail.policy, null, 2)}</pre></details>
  </div>;
}

function RolloutEvents({ agentId, rolloutId }: { agentId: string; rolloutId: string }) {
  const [items, setItems] = useState<ReleaseRolloutEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);
  useEffect(() => {
    const current = ++sequence.current; setLoading(true); setError(null); setItems([]); setTotal(0);
    void delayRequest(() => withAuthenticatedToken((token) => listAgentReleaseRolloutEvents(token, agentId, rolloutId, { page: page.current, pageSize: page.pageSize })))
      .then(({ value }) => { if (current === sequence.current) { setItems(value.items); setTotal(value.total); } })
      .catch((cause: unknown) => { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "灰度审计读取失败"); })
      .finally(() => { if (current === sequence.current) setLoading(false); });
    return () => { sequence.current += 1; };
  }, [agentId, rolloutId, page.current, page.pageSize, revision]);
  const columns: AppTableColumn<ReleaseRolloutEvent>[] = [
    { title: "时间", key: "created_at", render: (_, row) => date(row.created_at) },
    { title: "事件 / 修订", key: "event_type", render: (_, row) => `${row.event_type} / ${row.revision}` },
    { title: "操作者", key: "actor", render: (_, row) => row.actor_kind === "system" ? "系统巡检" : row.actor_user_id ? short(row.actor_user_id) : "未知" },
    { title: "状态 / 比例 / 轮次", key: "state", render: (_, row) => <div>{badge(row.status)}<span className="ml-2">{percentage(row.candidate_bps)} / {row.epoch}</span></div> },
    { title: "原因", key: "reason_code", render: (_, row) => row.reason_code ?? "无" },
  ];
  return <section className="space-y-2"><ListToolbar title="灰度审计列表" loading={loading} onRefresh={() => setRevision((value) => value + 1)} />
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <div className="relative min-h-32"><AppTable columns={columns} dataSource={items} rowKey="id" ariaLabel="追加式灰度审计" emptyText={error ? "审计暂不可用" : "尚无事件"}
      pagination={{ ...page, total, onChange: (current, pageSize) => setPage({ current: pageSize === page.pageSize ? current : 1, pageSize }) }} />
      {loading && <ListLoadingOverlay label="正在读取灰度审计…" />}</div></section>;
}
