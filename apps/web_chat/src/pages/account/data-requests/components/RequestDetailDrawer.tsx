import { useCallback, useEffect, useRef, useState } from "react";
import { getErasureRequest } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { Button } from "@/components/ui/button";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { ErasureCategoryImpact, ErasureRequestDetail } from "@/types";
import { blockerLabel, categoryLabels, requestStatus } from "../lib";
import { FixedReferenceDetails } from "./FixedReferenceDetails";

export function RequestDetailDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const [loadedDetail, setDetail] = useState<{ token: string; value: ErasureRequestDetail } | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const detail = loadedDetail !== null && loadedDetail.token === getStoredAccessToken()
    && loadedDetail.value.request.id === id ? loadedDetail.value : null;
  const sequence = useRef(0);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true); setError(null); setDetail(null);
    try {
      const expectedToken = getStoredAccessToken();
      if (!expectedToken) throw new Error("登录会话不可用，请重新登录后读取。");
      const response = await delayRequest(() => withAuthenticatedToken((token) => {
        if (token !== expectedToken) throw new Error("登录会话已变化，请重新读取申请。");
        return getErasureRequest(token, id);
      }));
      if (current !== sequence.current) return;
      if (response.token !== expectedToken || response.token !== getStoredAccessToken()) {
        throw new Error("登录会话已变化，请重新读取申请。");
      }
      if (response.value.request.id !== id) throw new Error("申请详情响应不匹配，请重新读取。");
      setDetail(response);
    } catch (cause) { if (current === sequence.current) setError(cause instanceof Error ? cause.message : "申请详情读取失败"); }
    finally { if (current === sequence.current) setLoading(false); }
  }, [id]);
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [load]);
  const columns: AppTableColumn<ErasureCategoryImpact>[] = [
    { title: "数据类别", key: "category", render: (_, row) => categoryLabels[row.category] },
    { title: "已知关联行数", dataIndex: "known_related_rows" },
    { title: "未覆盖依赖项", dataIndex: "unknown_table_count" },
    { title: "阻塞原因", key: "blockers", render: (_, row) => row.blockers.map(blockerLabel).join("；") },
  ];
  return <AppDrawer open onOpenChange={(open) => { if (!open) onClose(); }} title="本人数据申请" description="只读影响预览，关联行数不是可删除数量。" showConfirm={false} cancelText="关闭" className="sm:max-w-3xl">
    <div className="relative min-h-48 space-y-5">
      {loading && <ListLoadingOverlay label="正在读取申请详情…" />}
      {error && <div role="alert" className="space-y-3 text-sm text-red-600"><p>{error}</p><Button variant="outline" onClick={() => void load()}>重试</Button></div>}
      {detail && <>
        <p className="text-sm">{requestStatus[detail.request.status]} · 版本 {detail.request.revision} · 当前无批准或执行入口</p>
        <p className="text-sm text-muted-foreground">统计创建截止：{new Date(detail.request.cutoff_at).toLocaleString()}。各类可重叠，未知依赖未计为零；跨表计数不是执行时一致快照。</p>
        <dl className="space-y-3 text-xs"><div><dt>固定申请范围摘要</dt><dd className="break-all font-mono">{detail.request.scope_digest}</dd></div>
          <div><dt>策略草稿摘要</dt><dd className="break-all font-mono">{detail.request.policy_digest ?? "未配置"}</dd></div>
          <div><dt>预览摘要</dt><dd className="break-all font-mono">{detail.report_digest ?? "未记录"}</dd></div></dl>
        {detail.report_status === "integrity_failed" && <p role="alert" className="text-sm text-red-600">预览完整性校验失败，已隐藏内容，请联系数据治理负责人核对。</p>}
        {detail.report_status === "not_recorded" && <p role="status" className="text-sm text-muted-foreground">尚未生成预览。待预览申请可在列表中生成一次影响清单。</p>}
        {detail.report && <>
          <p className="text-sm">观察区间：{new Date(detail.report.observed_at).toLocaleString()} 至 {new Date(detail.report.finished_at).toLocaleString()}；未结束运行 {detail.report.active_run_count}，未确认动作 {detail.report.unresolved_action_count}。</p>
          <ul className="list-disc space-y-1 pl-5 text-sm">{detail.report.blockers.map((code) => <li key={code}>{blockerLabel(code)}</li>)}</ul>
          <AppTable columns={columns} dataSource={detail.report.impacts} rowKey="category" ariaLabel="数据影响清单" />
          <FixedReferenceDetails key={detail.report_digest} impacts={detail.report.impacts} />
        </>}
        <p className="text-sm text-muted-foreground">未来批准须绑定此范围、策略与预览摘要，由独立非本人复核，并重新盘点依赖；独立维护服务、实际擦除和备份恢复重施尚未实现。</p>
      </>}
    </div>
  </AppDrawer>;
}
