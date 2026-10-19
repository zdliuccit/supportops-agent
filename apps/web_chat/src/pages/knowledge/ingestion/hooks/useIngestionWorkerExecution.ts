import { useCallback, useEffect, useRef, useState } from "react";
import { getKnowledgeIngestionExecutionRequest, submitKnowledgeIngestionExecutionRequest } from "@/api";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { KnowledgeIngestionExecutionRequest, KnowledgeIngestionJob } from "@/types";

/** 只提交本人明确确认的一份意图；首次或未知结果均先查询原Job，不自动POST。 */
export function useIngestionWorkerExecution(job: KnowledgeIngestionJob, canSubmit: boolean,
  externalBusy: boolean, onBusyChange: (busy: boolean) => void, onIntentObserved: (jobId: string) => void,
) {
  const [sessionToken] = useState(getStoredAccessToken);
  const [result, setResult] = useState<KnowledgeIngestionExecutionRequest | null>(null);
  const [loading, setLoading] = useState(true), [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null), [uncertain, setUncertain] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmation, setConfirmation] = useState<{ revision: number; scope: string } | null>(null);
  const sequence = useRef(0), mutation = useRef(false), mounted = useRef(false);
  const sessionCurrent = sessionToken !== null && sessionToken === getStoredAccessToken();

  const load = useCallback(async () => {
    const request = ++sequence.current;
    const current = () => mounted.current && sequence.current === request
      && sessionToken !== null && sessionToken === getStoredAccessToken();
    setLoading(true); setLoaded(false); setResult(null); setError(null);
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => {
        if (!current() || token !== sessionToken) throw new Error("登录会话已变化，请重新打开任务。");
        return getKnowledgeIngestionExecutionRequest(token, job.id);
      }));
      if (!current() || response.token !== sessionToken) return;
      setResult(response.value); setLoaded(true);
      if (response.value !== null) { setUncertain(false); onIntentObserved(job.id); }
    } catch (cause) {
      if (current()) setError(cause instanceof Error ? cause.message : "Worker执行意图读取失败");
    } finally { if (mounted.current && sequence.current === request) setLoading(false); }
  }, [job.id, sessionToken, onIntentObserved]);

  useEffect(() => {
    mounted.current = true; void load();
    return () => { mounted.current = false; sequence.current += 1; onBusyChange(false); };
  }, [load, onBusyChange]);

  const available = sessionCurrent && loaded && !loading && !result && !uncertain && canSubmit
    && job.status === "queued" && job.attempts === 0 && job.revision !== null && job.scope_digest !== null;

  function begin() {
    if (!available || externalBusy || mutation.current || job.revision === null || job.scope_digest === null) return;
    setConfirmation({ revision: job.revision, scope: job.scope_digest });
  }

  function close() { if (!mutation.current) setConfirmation(null); }

  async function confirm() {
    if (!confirmation || !available || externalBusy || mutation.current) return;
    if (job.revision !== confirmation.revision || job.scope_digest !== confirmation.scope) {
      setConfirmation(null); notify.warning("任务修订或范围已变化，请重新读取后确认。"); return;
    }
    const expectedRevision = confirmation.revision;
    onIntentObserved(job.id);
    mutation.current = true; setSaving(true); onBusyChange(true); setUncertain(true);
    try {
      const response = await withAuthenticatedToken((token) => {
        if (token !== sessionToken) throw new Error("登录会话已变化，请重新打开任务。");
        return submitKnowledgeIngestionExecutionRequest(token, job.id, expectedRevision);
      });
      if (!mounted.current || response.token !== sessionToken || sessionToken !== getStoredAccessToken()) return;
      setResult(response.value); setLoaded(true); setUncertain(false); setConfirmation(null);
      if (response.value.status === "queued" || response.value.status === "claimed") {
        notify.success("已保存单次Worker执行意图，请查询原意图及任务结果。");
      } else notify.warning("已找到原执行意图，请核对其状态和原任务结果。");
    } catch (cause) {
      if (mounted.current && sessionToken === getStoredAccessToken()) {
        setConfirmation(null);
        notify.error(cause, "提交结果尚未确认，请只查询原执行意图。");
        await load();
      }
    } finally {
      mutation.current = false;
      if (mounted.current) { setSaving(false); onBusyChange(false); }
    }
  }

  return { result: sessionCurrent ? result : null, loading: sessionCurrent && loading,
    loaded: sessionCurrent && loaded, error: sessionCurrent ? error : "登录会话已变化，请重新打开任务。",
    uncertain, saving, available, confirmation: sessionCurrent ? confirmation : null, begin, close, confirm, load };
}
