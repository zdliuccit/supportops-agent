import { useCallback, useEffect, useRef, useState } from "react";
import { getKnowledgeIngestionComparison, listKnowledgeIngestionDrafts } from "@/api";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { KnowledgeIngestionComparison, KnowledgeIngestionDraft, PaginatedListResponse } from "@/types";

/** 由任务详情工作区持有；任务/身份切换重挂载，列表与正文分别隔离迟到响应。 */
export function useIngestionDrafts(jobId: string, busy: boolean) {
  const [result, setResult] = useState<PaginatedListResponse<KnowledgeIngestionDraft> | null>(null);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [comparison, setComparison] = useState<KnowledgeIngestionComparison | null>(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const listSequence = useRef(0), comparisonSequence = useRef(0);

  const clearComparison = useCallback(() => {
    comparisonSequence.current += 1;
    setSelectedId(null); setComparison(null); setComparisonError(null); setComparisonLoading(false);
  }, []);

  const load = useCallback(async () => {
    const sequence = ++listSequence.current;
    setLoading(true); setResult(null); setError(null); clearComparison();
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) =>
        listKnowledgeIngestionDrafts(token, jobId, page.current, page.pageSize)));
      if (sequence === listSequence.current && response.token === getStoredAccessToken()) setResult(response.value);
    } catch (cause) {
      if (sequence === listSequence.current) setError(cause instanceof Error ? cause.message : "草稿列表读取失败");
    } finally { if (sequence === listSequence.current) setLoading(false); }
  }, [jobId, page.current, page.pageSize, clearComparison]);

  const loadComparison = useCallback(async () => {
    const sequence = ++comparisonSequence.current;
    setComparison(null); setComparisonError(null); setComparisonLoading(Boolean(selectedId));
    if (!selectedId) return;
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) =>
        getKnowledgeIngestionComparison(token, jobId, selectedId)));
      if (sequence !== comparisonSequence.current || response.token !== getStoredAccessToken()) return;
      if (response.value.job_id !== jobId || response.value.materialization_id !== selectedId) {
        throw new Error("比较回执与所选草稿不一致，请重新读取。");
      }
      setComparison(response.value);
    } catch (cause) {
      if (sequence === comparisonSequence.current) setComparisonError(cause instanceof Error ? cause.message : "草稿比较读取失败");
    } finally { if (sequence === comparisonSequence.current) setComparisonLoading(false); }
  }, [jobId, selectedId]);

  useEffect(() => { void load(); return () => { listSequence.current += 1; }; }, [load]);
  useEffect(() => { void loadComparison(); return () => { comparisonSequence.current += 1; }; }, [loadComparison]);

  function changePage(current: number, pageSize: number) {
    if (busy || (current === page.current && pageSize === page.pageSize)) return;
    listSequence.current += 1;
    setLoading(true); setResult(null); setError(null); clearComparison(); setPage({ current, pageSize });
  }

  function selectComparison(id: string | null) {
    if (busy || id === selectedId) return;
    comparisonSequence.current += 1;
    setComparison(null); setComparisonError(null); setComparisonLoading(id !== null); setSelectedId(id);
  }

  return { result, page, loading, error, load, changePage, selectedId, comparison, comparisonLoading,
    comparisonError, selectComparison, loadComparison, clearComparison };
}
