import { useEffect, useRef, useState } from "react";
import { BookOpen } from "lucide-react";

import { ApiError, getKnowledgeEvidence } from "@/api";
import { AppDrawer } from "@/components/AppDrawer";
import { GlobalLoading } from "@/components/GlobalLoading";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import type { KnowledgeEvidenceDetail, ResultCitation, SupportResult } from "@/types";
import { DiagnosticReportPanel } from "./DiagnosticReportPanel";
import { MessageBusinessActions } from "./MessageBusinessActions";
import { RunHttpMcpDispatches } from "./RunHttpMcpDispatches";

/** 每次展开重新授权读取引用；建议明确标注尚未执行。 */
export function MessageSupportResult({ result }: { result: SupportResult }) {
  const [selected, setSelected] = useState<ResultCitation | null>(null);
  const [selectionResult, setSelectionResult] = useState<SupportResult | null>(null);
  const [detail, setDetail] = useState<KnowledgeEvidenceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unavailableIds, setUnavailableIds] = useState<string[]>([]);
  const requestId = useRef(0);

  useEffect(() => {
    requestId.current += 1;
    setSelected(null); setSelectionResult(null); setDetail(null);
    setError(null); setLoading(false); setUnavailableIds([]);
    return () => { requestId.current += 1; };
  }, [result]);

  async function openCitation(citation: ResultCitation) {
    const current = ++requestId.current;
    setSelected(citation);
    setSelectionResult(result);
    setDetail(null);
    setError(null);
    setLoading(true);
    try {
      const response = await withRefreshedToken((token) => getKnowledgeEvidence(token, result.run_id, citation.evidence_id));
      if (current === requestId.current) setDetail(response.value);
    } catch (cause) {
      if (current === requestId.current) {
        setError(cause instanceof Error ? cause.message : "读取引用失败");
        if (cause instanceof ApiError && (cause.status === 403 || cause.status === 404)) {
          setUnavailableIds((ids) => [...new Set([...ids, citation.evidence_id])]);
        }
      }
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }

  function close() {
    requestId.current += 1;
    setSelected(null);
    setSelectionResult(null);
    setDetail(null);
    setError(null);
    setLoading(false);
  }

  return <div className="mt-4 space-y-3 text-sm">
    {result.diagnostic_data_environment === "mock" && <p role="status" className="text-amber-700">模拟业务诊断；未查询真实日志、配额、路由或计费系统。</p>}
    {result.diagnostic && <DiagnosticReportPanel report={result.diagnostic} />}
    <MessageBusinessActions result={result} />
    <RunHttpMcpDispatches runId={result.run_id} />
    {result.evidence_conflicts && result.evidence_conflicts.length > 0 && <section aria-label="待复核的证据冲突" className="space-y-1 text-amber-700">
      <p className="font-medium">证据不一致，尚未确认结论</p>
      {result.evidence_conflicts.map((conflict, index) => <p key={index} className="whitespace-pre-wrap break-words">
        {conflict.status === "available" && !conflict.evidence_ids.some((id) => unavailableIds.includes(id))
          ? conflict.reason ?? "需要人工复核来源。" : "相关引用已失效或无权读取，原因已隐藏。"}
      </p>)}
    </section>}
    {result.citations.length > 0 && <section aria-label="答案引用" className="space-y-2">
      <p className="font-medium">参考依据</p>
      <div className="flex flex-wrap gap-2">{result.citations.map((citation, index) =>
        citation.status === "available" && !unavailableIds.includes(citation.evidence_id) ? <Button key={citation.evidence_id} size="sm" variant="outline"
          onClick={() => void openCitation(citation)} aria-label={`查看引用 ${index + 1}：${citation.title ?? "知识证据"}`}>
          <BookOpen className="size-4" aria-hidden="true" /><span className="max-w-[240px] truncate">{citation.title ?? `引用 ${index + 1}`}</span>
        </Button> : <span key={citation.evidence_id} className="text-muted-foreground">引用 {index + 1} 已失效或无权读取</span>,
      )}</div>
    </section>}
    {result.suggested_actions.length > 0 && <section aria-label="未执行的建议动作" className="space-y-1">
      <p className="font-medium">建议动作（尚未执行）</p>
      <ul className="list-inside list-disc space-y-1">{result.suggested_actions.map((action, index) => <li key={index}>{action.description}</li>)}</ul>
    </section>}
    {result.escalation_status === "recommended" && <p className="text-muted-foreground">建议转人工处理；尚未创建工单或执行升级。</p>}
    {/* 结果对象变化的当前渲染就隐藏旧正文，不等待effect清理完成。 */}
    <AppDrawer open={selected !== null && selectionResult === result} onOpenChange={(open) => { if (!open) close(); }} title="知识证据"
      description="每次读取都检查当前权限、版本和有效期。" showConfirm={false} cancelText="关闭">
      {selectionResult !== result ? <p className="text-muted-foreground">引用结果已更新，请重新选择。</p>
        : loading ? <GlobalLoading fullScreen={false} size="sm" label="正在读取获准证据…" /> : error ? <div role="alert" className="space-y-3">
        <p className="text-red-600">{error}</p><Button variant="outline" onClick={() => { if (selected) void openCitation(selected); }}>重新读取</Button>
      </div> : detail ? <div className="space-y-4">
        <h3 className="break-words font-semibold">{detail.title} · {detail.section_title}</h3>
        <p className="text-xs text-muted-foreground">检索时间：{new Date(detail.retrieved_at).toLocaleString()}</p>
        <p className="whitespace-pre-wrap break-words text-sm leading-6">{detail.content}</p>
        <p className="break-all font-mono text-xs text-muted-foreground">SHA-256：{detail.content_digest}</p>
      </div> : <p className="text-muted-foreground">暂无可读取的证据。</p>}
    </AppDrawer>
  </div>;
}
