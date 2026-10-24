import { useCallback, useEffect, useRef, useState } from "react";
import { getRoutingPolicyVersion, listRoutingPolicyVersions } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { useIdentity } from "@/components/IdentityContext";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { RoutingPolicyVersionView } from "@/types";
import { policyLabels, requirePolicy, requirePolicyPage } from "./contracts";
import { RoutingPolicyDrawer, type RoutingDetailState } from "./components/RoutingPolicyDrawer";

const errorMessage = (cause: unknown) => cause instanceof Error ? cause.message : "路由策略读取失败，请重试。";

export function ModelRoutingPage() {
  const identity = useIdentity(), token = getStoredAccessToken();
  if (!identity?.roles.includes("platform_admin")) return <p role="alert">需要平台管理员权限才能查看路由策略。</p>;
  return token ? <RoutingPolicies key={`${token}:${identity.tenant_id}`} token={token} tenantId={identity.tenant_id} /> : <p role="alert">请重新登录。</p>;
}

function RoutingPolicies({ token, tenantId }: { token: string; tenantId: string }) {
  const [items, setItems] = useState<RoutingPolicyVersionView[]>([]);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });
  const [total, setTotal] = useState(0), [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null), [detail, setDetail] = useState<RoutingDetailState | null>(null);
  const live = useRef(true), listSequence = useRef(0), detailSequence = useRef(0);
  const current = useCallback(() => live.current && getStoredAccessToken() === token, [token]);
  const request = useCallback(async <T,>(operation: (activeToken: string) => Promise<T>) => {
    const result = await withAuthenticatedToken((activeToken) => {
      if (activeToken !== token || !live.current) throw new Error("登录会话已变化，请重新打开页面。");
      return operation(activeToken);
    });
    if (!current()) throw new Error("登录会话已变化。");
    return result.value;
  }, [current, token]);

  const load = useCallback(async (page: number, pageSize: number) => {
    const sequence = ++listSequence.current;
    detailSequence.current += 1;
    setLoading(true); setError(null); setItems([]); setTotal(0); setDetail(null);
    try {
      const raw = await delayRequest(() => {
        if (!current() || sequence !== listSequence.current) throw new Error("读取已取消");
        return request((activeToken) => listRoutingPolicyVersions(activeToken, { page, pageSize }));
      });
      if (!current() || sequence !== listSequence.current) return;
      const result = requirePolicyPage(raw, page, pageSize, tenantId);
      setItems(result.items); setTotal(result.total);
    } catch (cause) { if (current() && sequence === listSequence.current) setError(errorMessage(cause)); }
    finally { if (current() && sequence === listSequence.current) setLoading(false); }
  }, [current, request, tenantId]);

  useEffect(() => {
    live.current = true; void load(1, 10);
    return () => { live.current = false; listSequence.current += 1; detailSequence.current += 1; };
  }, [load]);

  async function openDetail(target: Pick<RoutingDetailState, "policyId" | "versionId" | "digest">) {
    const sequence = ++detailSequence.current;
    setDetail({ ...target, value: null, loading: true, error: null });
    try {
      const raw = await delayRequest(() => {
        if (!current() || sequence !== detailSequence.current) throw new Error("读取已取消");
        return request((activeToken) => getRoutingPolicyVersion(activeToken, target.policyId, target.versionId));
      });
      if (!current() || sequence !== detailSequence.current) return;
      const value = requirePolicy(raw);
      if (value.record.policy_id !== target.policyId || value.record.version_id !== target.versionId
        || value.record.tenant_id !== tenantId || value.policy_digest !== target.digest) throw new Error("原策略版本或摘要不匹配，请刷新列表。");
      setDetail({ ...target, value, loading: false, error: null });
    } catch (cause) {
      if (current() && sequence === detailSequence.current) setDetail({ ...target, value: null, loading: false, error: errorMessage(cause) });
    }
  }

  const columns: AppTableColumn<RoutingPolicyVersionView>[] = [
    { title: "策略", key: "policy", render: (_, row) => <Button variant="ghost" className="h-auto p-0 font-mono text-xs"
      onClick={() => void openDetail({ policyId: row.record.policy_id, versionId: row.record.version_id, digest: row.policy_digest })}>{row.record.policy_id}</Button> },
    { title: "版本", key: "revision", render: (_, row) => row.record.revision },
    { title: "读取时状态", key: "status", render: (_, row) => <StatusBadge value={row.status === "candidate" ? "pending" : "disabled"} label={policyLabels[row.status]} /> },
    { title: "候选数", key: "candidates", render: (_, row) => row.record.definition.candidates.length },
    { title: "原有效期", key: "until", render: (_, row) => new Date(row.record.definition.valid_until).toLocaleString() },
    { title: "执行", key: "execution", render: () => <StatusBadge value="disabled" label="关闭" /> },
  ];
  return <>
    <PageHeader title="模型路由策略" description="查看固定策略、候选范围和费用兼容性。路由尚未启用，预览不会调用模型或批准发布。" />
    {error && <p role="alert" className="text-sm text-red-600">{error} 请使用列表刷新重试。</p>}
    <ListToolbar title="路由策略版本列表" loading={loading} onRefresh={() => void load(pagination.current, pagination.pageSize)} />
    <div className="relative min-h-[320px]"><AppTable columns={columns} dataSource={items} rowKey={(row) => row.record.version_id}
      ariaLabel="模型路由策略版本列表" emptyText={error ? "读取失败，请刷新重试" : "当前租户尚未保存路由策略候选"}
      pagination={{ ...pagination, total, onChange: (page, pageSize) => {
        if (loading) return;
        setPagination({ current: page, pageSize }); void load(page, pageSize);
      } }} />{loading && <ListLoadingOverlay label="正在读取路由策略…" />}</div>
    <RoutingPolicyDrawer state={detail} request={request} onRefresh={(target) => void openDetail(target)}
      onClose={() => { detailSequence.current += 1; setDetail(null); }} />
  </>;
}
