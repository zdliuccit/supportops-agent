import { useCallback, useEffect, useRef, useState } from "react";
import { listAuthSessions, revokeAllAuthSessions, revokeAuthSession } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { clearAccessToken, getStoredAccessToken, notifyRevokedSession, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { AuthSession, PaginatedListResponse } from "@/types";
import { RevokeSessionDialog } from "./components/RevokeSessionDialog";

/** 本人会话元数据；不展示令牌、IP 或设备指纹。 */
export function AuthSessionsPage() {
  const [result, setResult] = useState<PaginatedListResponse<AuthSession> | null>(null);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<AuthSession | "all" | null>(null);
  const [busy, setBusy] = useState(false);
  const mutation = useRef(false), sequence = useRef(0), mounted = useRef(false);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true); setError(null); setResult(null);
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => listAuthSessions(token, {
        page: page.current, pageSize: page.pageSize,
      })));
      if (sequence.current === current) setResult(response.value);
    } catch (cause) {
      if (sequence.current === current) setError(cause instanceof Error ? cause.message : "登录会话读取失败");
    } finally { if (sequence.current === current) setLoading(false); }
  }, [page.current, page.pageSize]);
  const latestLoad = useRef(load); latestLoad.current = load;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [load]);

  async function revoke() {
    if (!target || mutation.current) return;
    mutation.current = true; setBusy(true);
    try {
      const response = await withAuthenticatedToken(async (token) => {
        if (target === "all") await revokeAllAuthSessions(token);
        else await revokeAuthSession(token, target.id);
      });
      // 请求期间重新登录时，不清理随后取得的新会话。
      if (getStoredAccessToken() === response.token) {
        if (target === "all") clearAccessToken({ all: true });
        else if (target.is_current) clearAccessToken();
        else notifyRevokedSession(target.id);
      }
      notify.success(target === "all" ? "全部登录会话已撤销。" : "登录会话已撤销。");
      if (mounted.current) { setTarget(null); await latestLoad.current(); }
    } catch (cause) { notify.error(cause, "撤销未确认，请刷新会话记录后重试"); }
    finally { mutation.current = false; if (mounted.current) setBusy(false); }
  }
  const columns: AppTableColumn<AuthSession>[] = [
    { title: "会话", key: "session", render: (_, row) => <div className="max-w-xs break-all text-xs">{row.id}{row.is_current && <p className="mt-1 text-sm font-medium">当前页面会话</p>}</div> },
    { title: "状态", key: "status", render: (_, row) => <StatusBadge value={row.is_active ? "active" : "expired"}
      label={row.revoked_at ? "已撤销" : row.is_active ? "有效" : "已过期或失效"} /> },
    { title: "创建时间", key: "created", render: (_, row) => new Date(row.created_at).toLocaleString() },
    { title: "到期时间", key: "expires", render: (_, row) => new Date(row.expires_at).toLocaleString() },
    { title: "撤销时间", key: "revoked", render: (_, row) => row.revoked_at ? new Date(row.revoked_at).toLocaleString() : "—" },
    { title: "操作", key: "actions", align: "right", render: (_, row) => <Button size="sm" variant="outline"
      disabled={busy || loading || !row.is_active} onClick={() => setTarget(row)}>撤销{row.is_current ? "当前会话" : "会话"}</Button> },
  ];
  return <div className="space-y-6">
    <PageHeader title="登录会话" description="查看本人短期登录会话；到期后重新登录，每次登录生成独立会话。"
      actions={<Button variant="outline" disabled={busy || loading} onClick={() => setTarget("all")}>撤销全部会话</Button>} />
    {error && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-red-600"><span>{error}</span>
      <Button size="sm" variant="outline" disabled={loading} onClick={() => void load()}>重试</Button></div>}
    <section className="rounded-2xl bg-white">
      <ListToolbar title="登录会话列表" loading={loading} onRefresh={() => void load()} />
      <div className="relative min-h-48"><AppTable columns={columns} dataSource={result?.items ?? []} rowKey="id"
        ariaLabel="本人登录会话列表" emptyText={error ? "登录会话暂时无法读取" : "暂无登录会话"}
        pagination={{ ...page, total: result?.total ?? 0, onChange: (current, pageSize) => setPage({ current, pageSize }) }} />
        {loading && <ListLoadingOverlay label="正在读取登录会话…" />}
      </div>
    </section>
    <RevokeSessionDialog target={target} busy={busy} onClose={() => setTarget(null)} onConfirm={() => void revoke()} />
  </div>;
}
