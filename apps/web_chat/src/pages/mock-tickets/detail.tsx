import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getMockTicket, listMockTicketCommands, submitMockTicketCommand } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { GlobalLoading } from "@/components/GlobalLoading";
import { PageHeader } from "@/components/PageHeader";
import { ListToolbar } from "@/components/ListToolbar";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import { ticketStatusLabels } from "./index";
import { StatusBadge } from "@/components/StatusBadge";
import { commandLabels, TicketCommandDialog } from "./components/TicketCommandDialog";
import { EscalationPackagePanel } from "@/pages/chat/components/EscalationPackagePanel";
import type { MockTicket, MockTicketCommand, MockTicketCommandKind } from "@/types";

type PendingCommand = { kind: MockTicketCommandKind; body: string; expected_version: number; idempotency_key: string };

/** 人工 Mock 命令保持同键重试，读取当前工单，不把历史回执当作当前状态。 */
export function MockTicketDetailPage() {
  const { externalId } = useParams<{ externalId: string }>();
  const [ticket, setTicket] = useState<MockTicket | null>(null);
  const [commands, setCommands] = useState<MockTicketCommand[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<MockTicketCommandKind | null>(null);
  const [body, setBody] = useState("");
  const [dialogError, setDialogError] = useState<string | null>(null);
  const pending = useRef<PendingCommand | null>(null);
  const sequence = useRef(0);
  const operationSequence = useRef(0);
  const busyRef = useRef(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  async function load(pagination = page, afterCommand = false) {
    if (!externalId || (!afterCommand && (busyRef.current || pending.current))) return;
    const current = ++sequence.current;
    setLoading(true); setError(null); setTicket(null); setCommands([]);
    try {
      const response = await delayRequest(() => withRefreshedToken(async (token) => {
        const [detail, records] = await Promise.all([getMockTicket(token, externalId), listMockTicketCommands(token, externalId, pagination.current, pagination.pageSize)]);
        return { detail, records };
      }));
      if (current === sequence.current) { setTicket(response.value.detail); setCommands(response.value.records.items); setTotal(response.value.records.total); }
    } catch (cause) { if (current === sequence.current) { setTotal(0); setError(cause instanceof Error ? cause.message : "读取工单失败"); } }
    finally { if (current === sequence.current) setLoading(false); }
  }
  useEffect(() => { const first = { current: 1, pageSize: 20 }; setPage(first); pending.current = null; busyRef.current = false; setBusy(false); setKind(null); void load(first);
    return () => { sequence.current += 1; operationSequence.current += 1; }; }, [externalId]);
  function begin(operation: MockTicketCommandKind, trigger: HTMLButtonElement) {
    if (busyRef.current || pending.current || loading) return;
    notify.dismissSuccess(); triggerRef.current = trigger; setKind(operation); setBody(""); setDialogError(null);
  }
  async function execute() {
    if (busyRef.current || !ticket || !kind || !externalId || ((kind === "note" || kind === "reply") && !body.trim())) return;
    const current = operationSequence.current;
    pending.current ??= { kind, body: body.trim(), expected_version: ticket.version, idempotency_key: crypto.randomUUID() };
    const command = pending.current;
    busyRef.current = true; setBusy(true); setDialogError(null);
    try {
      await withRefreshedToken((token) => submitMockTicketCommand(token, externalId, command));
      if (current !== operationSequence.current) return;
      pending.current = null; setKind(null); notify.success("人工 Mock 操作已记录，正在重新读取目标状态。"); await load(page, true);
    } catch (cause) { if (current === operationSequence.current) {
      setDialogError(cause instanceof Error ? cause.message : "操作尚未确认，可用同一命令重试。");
      notify.error(cause, "人工操作尚未确认");
      if (cause instanceof ApiError && [400, 403, 404, 409, 422].includes(cause.status)) {
        pending.current = null;
        if ([403, 404, 409].includes(cause.status)) {
          setTicket(null); setCommands([]); setTotal(0);
          setError("当前工单权限或版本已变化，请关闭弹窗后重新读取。");
        }
      }
    } } finally { if (current === operationSequence.current) { busyRef.current = false; setBusy(false); } }
  }
  const readBlocked = busy || pending.current !== null;
  const columns: AppTableColumn<MockTicketCommand>[] = [
    { title: "操作", key: "kind", render: (_, item) => commandLabels[item.kind] },
    { title: "人员", key: "actor", render: (_, item) => item.actor_name ?? "历史人员" },
    { title: "正文", key: "body", render: (_, item) => <p className="max-w-xl whitespace-pre-wrap break-words">{item.body || "—"}</p> },
    { title: "记录时间", key: "time", render: (_, item) => new Date(item.created_at).toLocaleString() },
  ];
  return <div ref={contentRef} tabIndex={-1} aria-label="工单详情内容" className="space-y-5 [overflow-wrap:anywhere]"><PageHeader title="Mock 工单详情" description="独立读取的隔离业务状态；人工操作需要明确项目权限和认领关系。"
    actions={ticket && <div className="flex flex-wrap gap-2">{ticket.can_claim && <Button disabled={busy} onClick={(event) => begin("claim", event.currentTarget)}>认领并接管</Button>}
      {ticket.can_update && (Object.keys(commandLabels) as MockTicketCommandKind[]).filter((operation) => operation !== "claim").map((operation) => <Button key={operation} variant={operation === "close" ? "destructive" : "outline"} disabled={busy} onClick={(event) => begin(operation, event.currentTarget)}>{commandLabels[operation]}</Button>)}</div>} />
    <Link className="text-sm text-emerald-700 underline" to="/mock-tickets">返回工单工作台</Link>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <ListToolbar title="当前工单状态" loading={loading || readBlocked} onRefresh={() => void load()} />
    {loading ? <GlobalLoading fullScreen={false} label="正在读取当前工单权限与状态…" /> : ticket && <section className="space-y-3 rounded-xl bg-white p-4 text-sm">
      <p className="break-all text-xs text-muted-foreground">{ticket.external_id}</p><h2 className="text-lg font-semibold">{ticket.title}</h2>
      <p className="whitespace-pre-wrap break-words">{ticket.summary}</p><p role="status"><StatusBadge value={ticket.status === "closed" ? "completed" : "pending"} label={ticketStatusLabels[ticket.status]} /> · 处理人：{ticket.handler_name ?? "尚未认领"}</p>
      {ticket.takeover_active && <p className="text-amber-700">客服接管中，关联问题的 Agent 写动作被冻结；只读诊断仍可继续。</p>}
      <p className="text-muted-foreground">资源主体：{ticket.owner_name ?? "历史用户"} · 更新：{new Date(ticket.updated_at).toLocaleString()}</p>
    </section>}
    {!loading && ticket && (ticket.can_read_context
      ? <EscalationPackagePanel key={`${ticket.external_id}:${ticket.version}`} externalId={ticket.external_id} />
      : <p className="text-sm text-muted-foreground">当前仅具备工单权限；读取排查升级包还需独立的项目上下文授权。</p>)}
    <ListToolbar title="人工处理记录列表" loading={loading || readBlocked} onRefresh={() => void load()} />
    <AppTable columns={columns} dataSource={commands} rowKey="id" ariaLabel="当前权限工单处理记录" emptyText="暂无人工操作记录"
      pagination={{ ...page, total, onChange: (current, pageSize) => { if (readBlocked) return; const next = { current, pageSize }; setPage(next); void load(next); } }} />
    <TicketCommandDialog kind={kind} body={body} busy={busy} pending={pending.current !== null} canSubmit={ticket !== null}
      error={dialogError} onBodyChange={setBody} onClose={() => setKind(null)} onExecute={() => void execute()}
      onCloseAutoFocus={(event) => { event.preventDefault(); (triggerRef.current?.isConnected ? triggerRef.current : contentRef.current)?.focus(); }} />
  </div>;
}
