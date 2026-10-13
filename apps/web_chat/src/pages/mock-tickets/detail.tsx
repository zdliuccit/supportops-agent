import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getMockTicket, listMockTicketCommands, submitMockTicketCommand } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { GlobalLoading } from "@/components/GlobalLoading";
import { PageHeader } from "@/components/PageHeader";
import { ListToolbar } from "@/components/ListToolbar";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import { ticketStatusLabels } from "./index";
import type { MockTicket, MockTicketCommand, MockTicketCommandKind } from "@/types";

const commandLabels: Record<MockTicketCommandKind, string> = { claim: "认领", note: "内部备注", reply: "回复申请者", close: "关闭", escalate: "升级", release: "释放接管" };
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
  async function load(pagination = page) {
    if (!externalId) return;
    const current = ++sequence.current;
    setLoading(true); setError(null); setTicket(null); setCommands([]); setTotal(0);
    try {
      const response = await delayRequest(() => withRefreshedToken(async (token) => {
        const [detail, records] = await Promise.all([getMockTicket(token, externalId), listMockTicketCommands(token, externalId, pagination.current, pagination.pageSize)]);
        return { detail, records };
      }));
      if (current === sequence.current) { setTicket(response.value.detail); setCommands(response.value.records.items); setTotal(response.value.records.total); }
    } catch (cause) { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "读取工单失败"); }
    finally { if (current === sequence.current) setLoading(false); }
  }
  useEffect(() => { const first = { current: 1, pageSize: 20 }; setPage(first); pending.current = null; setKind(null); void load(first);
    return () => { sequence.current += 1; }; }, [externalId]);
  function begin(operation: MockTicketCommandKind) { if (pending.current) return; setKind(operation); setBody(""); setDialogError(null); }
  async function execute() {
    if (!ticket || !kind || !externalId) return;
    const current = sequence.current;
    pending.current ??= { kind, body: body.trim(), expected_version: ticket.version, idempotency_key: crypto.randomUUID() };
    setBusy(true); setDialogError(null);
    try {
      await withRefreshedToken((token) => submitMockTicketCommand(token, externalId, pending.current!));
      if (current !== sequence.current) return;
      pending.current = null; setKind(null); notify.success("人工 Mock 操作已记录，正在重新读取目标状态。"); await load();
    } catch (cause) { if (current === sequence.current) {
      setDialogError(cause instanceof Error ? cause.message : "操作尚未确认，可用同一命令重试。");
      if (cause instanceof ApiError && [400, 403, 404, 409, 422].includes(cause.status)) {
        pending.current = null;
        if ([403, 404].includes(cause.status)) { setTicket(null); setCommands([]); setError("当前工单权限已失效，请重新读取。"); }
      }
    } } finally { setBusy(false); }
  }
  const columns: AppTableColumn<MockTicketCommand>[] = [
    { title: "操作", key: "kind", render: (_, item) => commandLabels[item.kind] },
    { title: "人员", key: "actor", render: (_, item) => item.actor_name ?? "历史人员" },
    { title: "正文", key: "body", render: (_, item) => <p className="max-w-xl whitespace-pre-wrap break-words">{item.body || "—"}</p> },
    { title: "记录时间", key: "time", render: (_, item) => new Date(item.created_at).toLocaleString() },
  ];
  return <div className="space-y-5"><PageHeader title="Mock 工单详情" description="独立读取的隔离业务状态；人工操作需要明确项目权限和认领关系。"
    actions={ticket && <div className="flex flex-wrap gap-2">{ticket.can_claim && <Button disabled={busy} onClick={() => begin("claim")}>认领并接管</Button>}
      {ticket.can_update && (Object.keys(commandLabels) as MockTicketCommandKind[]).filter((operation) => operation !== "claim").map((operation) => <Button key={operation} variant={operation === "close" ? "destructive" : "outline"} disabled={busy} onClick={() => begin(operation)}>{commandLabels[operation]}</Button>)}</div>} />
    <Link className="text-sm text-emerald-700 underline" to="/mock-tickets">返回工单工作台</Link>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <ListToolbar title="当前工单状态" loading={loading} onRefresh={() => void load()} />
    {loading ? <GlobalLoading fullScreen={false} label="正在读取当前工单权限与状态…" /> : ticket && <section className="space-y-3 rounded-xl bg-white p-4 text-sm">
      <p className="break-all text-xs text-muted-foreground">{ticket.external_id}</p><h2 className="text-lg font-semibold">{ticket.title}</h2>
      <p className="whitespace-pre-wrap break-words">{ticket.summary}</p><p role="status">{ticketStatusLabels[ticket.status]} · 处理人：{ticket.handler_name ?? "尚未认领"}</p>
      {ticket.takeover_active && <p className="text-amber-700">客服接管中，关联问题的 Agent 写动作被冻结；只读诊断仍可继续。</p>}
      <p className="text-muted-foreground">资源主体：{ticket.owner_name ?? "历史用户"} · 更新：{new Date(ticket.updated_at).toLocaleString()}</p>
    </section>}
    <ListToolbar title="人工处理记录列表" loading={loading} onRefresh={() => void load()} />
    <AppTable columns={columns} dataSource={commands} rowKey="id" ariaLabel="当前权限工单处理记录" emptyText="暂无人工操作记录"
      pagination={{ ...page, total, onChange: (current, pageSize) => { const next = { current, pageSize }; setPage(next); void load(next); } }} />
    <Dialog open={kind !== null} onOpenChange={(open) => { if (!open && !busy && !pending.current) setKind(null); }}><DialogContent>
      <DialogHeader><DialogTitle>{kind ? commandLabels[kind] : "人工操作"}</DialogTitle><DialogDescription>只操作当前 Mock 工单。认领冻结关联 Agent 写动作；关闭或释放后明确结束接管。</DialogDescription></DialogHeader>
      <DialogBody className="space-y-4"><Label htmlFor="mock-command-body">{kind === "note" ? "仅获准客服可读的备注" : kind === "reply" ? "申请者可读的回复" : "操作说明（可选）"}</Label>
        <Textarea id="mock-command-body" value={body} onChange={(event) => setBody(event.target.value)} maxLength={1000} disabled={busy || pending.current !== null} />
        {dialogError && <p role="alert" className="text-sm text-red-600">{dialogError}</p>}
        {pending.current && !busy && <p className="text-sm text-muted-foreground">尚未收到确认，重试会复用同一命令和原参数，不重新生成操作。</p>}
      </DialogBody><DialogFooter><Button variant="outline" disabled={busy || pending.current !== null} onClick={() => setKind(null)}>取消</Button>
        <Button variant={kind === "close" ? "destructive" : "default"} disabled={busy || !ticket || ((kind === "note" || kind === "reply") && !body.trim())}
          onClick={() => void execute()}>{busy ? "正在提交…" : pending.current ? "重试同一命令" : "确认操作"}</Button></DialogFooter>
    </DialogContent></Dialog>
  </div>;
}
