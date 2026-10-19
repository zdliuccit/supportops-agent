import { useCallback, useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";

import { ApiError, configureToolDiscoverySource, getFixedDiscoveredTool, getToolDiscoverySource, invalidateToolDiscoverySource, listToolDiscoverySources } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { ToolDiscoveryBinding, ToolDiscoveryPolicy, ToolDiscoveryView } from "@/types";
import { discoveryLabels, requireDiscoveryView, requireSourcePage } from "./contracts";
import { SourceDetailsDrawer, type SourceDetailState } from "./components/SourceDetailsDrawer";
import { SourceInvalidationDialog } from "./components/SourceInvalidationDialog";
import { SourcePolicyDialog } from "./components/SourcePolicyDialog";

const message = (cause: unknown) => cause instanceof Error ? cause.message : "读取工具来源失败，请重试。";

export function ToolSourcesPage() {
  const token = getStoredAccessToken();
  return token ? <ToolSourcesWorkspace key={token} token={token} /> : null;
}

function ToolSourcesWorkspace({ token }: { token: string }) {
  const [sources, setSources] = useState<ToolDiscoveryView[]>([]);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ source: ToolDiscoveryView | null } | null>(null);
  const [invalidating, setInvalidating] = useState<ToolDiscoveryView | null>(null);
  const [details, setDetails] = useState<SourceDetailState | null>(null);
  const [unconfirmed, setUnconfirmed] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const live = useRef(true), listSequence = useRef(0), detailSequence = useRef(0), writeLock = useRef(false);
  const current = useCallback(() => live.current && getStoredAccessToken() === token, [token]);

  const request = useCallback(async <T,>(operation: (activeToken: string) => Promise<T>) => {
    const result = await withAuthenticatedToken((activeToken) => {
      if (activeToken !== token || !live.current) throw new Error("登录会话已变化，请重新打开页面。");
      return operation(activeToken);
    });
    return result.value;
  }, [token]);

  const load = useCallback(async (page: number, pageSize: number) => {
    const sequence = ++listSequence.current;
    setLoading(true); setVerified(false); setError(null); setSources([]); setTotal(0);
    try {
      const raw = await delayRequest(() => {
        if (!current() || sequence !== listSequence.current) throw new Error("读取已取消");
        return request((activeToken) => listToolDiscoverySources(activeToken, page, pageSize));
      });
      if (!current() || sequence !== listSequence.current) return;
      const result = requireSourcePage(raw, page, pageSize);
      setDetails((value) => value?.source && result.items.some((item) => item.record_id === value.source?.record_id) ? value : null);
      setSources(result.items); setTotal(result.total); setVerified(true);
    } catch (cause) {
      if (current() && sequence === listSequence.current) setError(message(cause));
    } finally {
      if (current() && sequence === listSequence.current) setLoading(false);
    }
  }, [current, request]);

  useEffect(() => {
    live.current = true; void load(1, 10);
    return () => { live.current = false; listSequence.current += 1; detailSequence.current += 1; };
  }, [load]);

  async function openDetails(sourceKey: string) {
    const sequence = ++detailSequence.current;
    setDetails({ sourceKey, source: null, loading: true, error: null, descriptor: null, descriptorLoading: false, descriptorError: null });
    try {
      const raw = await request((activeToken) => getToolDiscoverySource(activeToken, sourceKey));
      if (!current() || sequence !== detailSequence.current) return;
      const source = requireDiscoveryView(raw);
      if (source.source_key !== sourceKey) throw new Error("来源详情与当前目标不匹配。");
      setDetails({ sourceKey, source, loading: false, error: null, descriptor: null, descriptorLoading: false, descriptorError: null });
    } catch (cause) {
      if (current() && sequence === detailSequence.current) setDetails((value) => value ? { ...value, loading: false, error: message(cause), source: null } : null);
    }
  }

  async function describe(source: ToolDiscoveryView, tool: ToolDiscoveryBinding) {
    const catalogDigest = source.catalog_digest;
    if (!catalogDigest || source.status !== "ready") return;
    if (Date.parse(source.valid_until) <= Date.now()) { notify.warning("批准已到期，请刷新来源并重新批准。"); return; }
    const sequence = ++detailSequence.current;
    setDetails((value) => value?.source?.record_id === source.record_id ? { ...value, descriptor: null, descriptorLoading: true, descriptorError: null } : value);
    try {
      const descriptor = await request((activeToken) => getFixedDiscoveredTool(activeToken, source.source_key,
        { record_id: source.record_id, catalog_digest: catalogDigest, tool_id: tool.tool_id }));
      if (!current() || sequence !== detailSequence.current) return;
      if (!descriptor || descriptor.name !== tool.remote_name || typeof descriptor.description !== "string"
        || descriptor.description.length > 2000 || !descriptor.input_schema || !descriptor.output_schema
        || descriptor.input_schema.type !== "object" || descriptor.input_schema.additionalProperties !== false
        || descriptor.output_schema.type !== "object" || descriptor.output_schema.additionalProperties !== false) {
        throw new Error("固定工具定义契约无法确认，请重新读取来源。");
      }
      setDetails((value) => value?.source?.record_id === source.record_id ? { ...value, descriptor, descriptorLoading: false } : value);
    } catch (cause) {
      if (current() && sequence === detailSequence.current) setDetails((value) => value?.source?.record_id === source.record_id
        ? { ...value, descriptor: null, descriptorLoading: false, descriptorError: message(cause) } : value);
    }
  }

  async function mutate(sourceKey: string, previousRevision: number, operation: () => Promise<ToolDiscoveryView>) {
    if (writeLock.current || !verified || unconfirmed !== null || !current()) return;
    writeLock.current = true; setBusy(true); detailSequence.current += 1; setDetails(null);
    try {
      const result = requireDiscoveryView(await operation());
      if (!current()) return;
      if (result.source_key !== sourceKey || result.revision !== previousRevision + 1) throw new Error("保存回执与目标修订不一致，请查询当前来源。");
      notify.success(`来源 ${sourceKey} 已保存为修订 ${result.revision}。`);
    } catch (cause) {
      if (current()) {
        // 明确的参数/认证/隐藏资源拒绝不当作已提交；409仍可能表示提交结果未知。
        if (!(cause instanceof ApiError && [400, 401, 403, 404, 422].includes(cause.status))) setUnconfirmed(sourceKey);
        notify.error(cause, "写入结果尚未确认，请查询原来源。");
      }
    } finally {
      writeLock.current = false;
      if (current()) {
        setBusy(false); setEditing(null); setInvalidating(null);
        await load(pagination.current, pagination.pageSize);
      }
    }
  }

  async function save(sourceKey: string, expectedRevision: number, policy: ToolDiscoveryPolicy) {
    await mutate(sourceKey, expectedRevision, () => request((activeToken) => configureToolDiscoverySource(activeToken, sourceKey, expectedRevision, policy)));
  }

  async function invalidate() {
    if (!invalidating) return;
    const source = invalidating;
    await mutate(source.source_key, source.revision, () => request((activeToken) => invalidateToolDiscoverySource(activeToken, source.source_key,
      { expected_policy_digest: source.policy_digest, source_fingerprint: source.source_fingerprint, reason: "manual_refresh" })));
  }

  async function checkUnconfirmed() {
    if (!unconfirmed || checking) return;
    const sourceKey = unconfirmed; setChecking(true);
    try {
      const result = requireDiscoveryView(await request((activeToken) => getToolDiscoverySource(activeToken, sourceKey)));
      if (!current()) return;
      if (result.source_key !== sourceKey) throw new Error("来源查询目标不匹配。");
      setUnconfirmed(null);
      notify.warning("已读取当前服务器修订，请核对；这不证明上次写入成功。后续修改需要重新确认。");
      await load(pagination.current, pagination.pageSize);
      if (current()) await openDetails(sourceKey);
    } catch (cause) { if (current()) notify.error(cause, "尚未确认原来源，未重放写入。"); }
    finally { if (current()) setChecking(false); }
  }

  const disabled = busy || loading || !verified || unconfirmed !== null || checking;
  const columns: AppTableColumn<ToolDiscoveryView>[] = [
    { title: "来源", key: "source", render: (_, source) => <Button variant="link" className="h-auto p-0" disabled={busy}
      onClick={() => void openDetails(source.source_key)}>{source.source_key}</Button> },
    { title: "修订", dataIndex: "revision", key: "revision" },
    { title: "目录状态", key: "status", render: (_, source) => <StatusBadge value={source.status === "ready" ? "passed" : source.status === "rejected" ? "failed" : source.status} label={discoveryLabels[source.status]} /> },
    { title: "连接健康", key: "connection", render: () => <StatusBadge value="unknown" label="未接入" /> },
    { title: "批准截止", key: "expiry", render: (_, source) => new Date(source.valid_until).toLocaleString() },
    { title: "操作", key: "actions", align: "right", render: (_, source) => <div className="flex flex-wrap justify-end gap-2">
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => setEditing({ source })}>编辑批准 / 启停</Button>
      <Button variant="outline" size="sm" disabled={disabled || !source.policy.enabled || source.status === "expired"}
        onClick={() => setInvalidating(source)}>使目录失效</Button>
    </div> },
  ];
  return <>
    <PageHeader title="工具来源" description="管理来源批准、启停和固定目录。真实协议连接尚未接入，目录验证不代表连接健康。"
      actions={<Button disabled={disabled} onClick={() => setEditing({ source: null })}><Plus aria-hidden="true" />新增来源</Button>} />
    {error && <Alert variant="destructive"><AlertDescription>{error} 请通过列表刷新重试。</AlertDescription></Alert>}
    {unconfirmed && <Alert className="mt-4"><AlertDescription>来源 {unconfirmed} 的上次写入尚未确认，写操作已暂停。查询只读取当前事实，不会重发。
      <Button variant="outline" size="sm" className="ml-3" disabled={checking || loading} onClick={() => void checkUnconfirmed()}>{checking ? "查询中…" : "查询原来源"}</Button>
    </AlertDescription></Alert>}
    <ListToolbar title="工具来源列表" onRefresh={() => void load(pagination.current, pagination.pageSize)} loading={loading || busy || checking} />
    <div className="relative min-h-[320px]"><AppTable columns={columns} dataSource={sources} rowKey="source_key" ariaLabel="工具来源列表"
      emptyText={error ? "来源读取失败，请刷新重试" : "当前租户尚未配置工具来源"}
      pagination={{ ...pagination, total, onChange: (currentPage, pageSize) => {
        if (busy || loading || checking) return;
        setPagination({ current: currentPage, pageSize }); void load(currentPage, pageSize);
      } }} />{loading && <ListLoadingOverlay label="正在加载工具来源…" />}</div>
    {editing && <SourcePolicyDialog key={editing.source?.record_id ?? "new"} source={editing.source} busy={busy} onClose={() => { if (!writeLock.current) setEditing(null); }} onSave={save} />}
    {invalidating && <SourceInvalidationDialog source={invalidating} busy={busy} onClose={() => { if (!writeLock.current) setInvalidating(null); }} onConfirm={invalidate} />}
    {details && <SourceDetailsDrawer key={details.source?.record_id ?? details.sourceKey} state={details} onClose={() => { detailSequence.current += 1; setDetails(null); }}
      onRefresh={() => void openDetails(details.sourceKey)} onDescribe={(source, tool) => void describe(source, tool)} />}
  </>;
}
