import { useCallback, useEffect, useRef, useState } from "react";
import { getKnowledgeIngestionProvenance } from "@/api";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { KnowledgeIngestionProvenance } from "@/types";
import { provenanceSupported } from "../lib/provenance";

/** 仅显式读取一项；选择、会话和请求序号共同隔离迟到信息。 */
export function useIngestionProvenance(jobId: string, busy: boolean) {
  const [sessionToken] = useState(getStoredAccessToken);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<KnowledgeIngestionProvenance | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState<string | null>(null);
  const sequence = useRef(0);
  const sessionCurrent = sessionToken !== null && sessionToken === getStoredAccessToken();

  const clear = useCallback(() => {
    sequence.current += 1;
    setSelectedId(null); setView(null); setError(null); setLoading(false);
  }, []);

  const load = useCallback(async () => {
    const request = ++sequence.current;
    setView(null); setError(null); setLoading(selectedId !== null);
    if (!selectedId) return;
    const current = () => request === sequence.current && sessionToken !== null && sessionToken === getStoredAccessToken();
    if (!current()) { setLoading(false); return; }
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => {
        if (!current() || token !== sessionToken) throw new Error("登录会话已变化，请重新打开任务。");
        return getKnowledgeIngestionProvenance(token, jobId, selectedId);
      }));
      if (!current() || response.token !== sessionToken) return;
      if (!provenanceSupported(response.value) || response.value.job_id !== jobId
        || response.value.materialization_id !== selectedId) throw new Error("来源追溯契约或目标无法确认，已隐藏内容，请重新读取。");
      setView(response.value);
    } catch (cause) {
      if (current()) setError(cause instanceof Error ? cause.message : "来源追溯读取失败");
    } finally { if (request === sequence.current) setLoading(false); }
  }, [jobId, selectedId, sessionToken]);

  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [load]);

  function select(id: string | null) {
    if (busy || !sessionCurrent || id === selectedId) return;
    sequence.current += 1;
    setView(null); setError(null); setLoading(id !== null); setSelectedId(id);
  }

  return { selectedId, view: sessionCurrent ? view : null, loading: sessionCurrent && loading,
    error: sessionCurrent ? error : "登录会话已变化，请重新打开任务。", load, select, clear };
}
