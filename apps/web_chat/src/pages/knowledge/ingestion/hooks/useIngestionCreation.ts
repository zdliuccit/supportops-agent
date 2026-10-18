import { useCallback, useEffect, useRef, useState } from "react";
import { createKnowledgeIngestionJob, getKnowledgeIngestionCreationStatus, getKnowledgeIngestionSourceReadiness,
  stopKnowledgeIngestionCreation } from "@/api";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { KnowledgeIngestionJob, KnowledgeIngestionSourceReadiness } from "@/types";
import { sourceReady } from "../lib/presentation";
import { clearConfirmedCreation, creationRequestOutcome, readPendingCreation, reservePendingCreation,
  validateCreatedJob, type PendingIngestionCreation } from "../lib/creationRequest";

export interface IngestionCreationSnapshot {
  source: KnowledgeIngestionSourceReadiness | null;
  sourceId: string;
  requestKey: string;
  uncertain: boolean;
}

export function useIngestionCreation(tenantId: string | undefined, subjectId: string | undefined, administrator: boolean,
  onCreated: (job: KnowledgeIngestionJob) => void, externallyBusy: boolean,
) {
  const [source, setSource] = useState<KnowledgeIngestionSourceReadiness | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<IngestionCreationSnapshot | null>(null);
  const [stopConfirmation, setStopConfirmation] = useState<IngestionCreationSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingIngestionCreation | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const mounted = useRef(false), sequence = useRef(0), mutation = useRef(false);
  const confirmationToken = useRef<string | null>(null), sent = useRef(false);
  const afterCreated = useRef(onCreated); afterCreated.current = onCreated;
  const load = useCallback(async () => {
    const request = ++sequence.current;
    setLoading(true); setSource(null); setError(null);
    if (!tenantId || !subjectId || !administrator) { setLoading(false); return; }
    try { setPending(readPendingCreation(tenantId, subjectId)); setStorageError(null); }
    catch (cause) { setStorageError(cause instanceof Error ? cause.message : "待确认请求无法读取，当前不能新建任务。"); }
    try {
      const response = await delayRequest(() => withAuthenticatedToken(getKnowledgeIngestionSourceReadiness));
      if (request !== sequence.current || response.token !== getStoredAccessToken()) return;
      if (response.value.schema_version !== "knowledge_ingestion_source_readiness_v1"
        || response.value.automatic_publish_enabled !== false || typeof response.value.available !== "boolean") {
        throw new Error("来源可用性契约不可确认，请刷新后再操作。");
      }
      if (response.value.available && !sourceReady(response.value)) throw new Error("来源批准范围或有效期不可确认，请刷新。");
      setSource(response.value);
    } catch (cause) {
      if (request === sequence.current) setError(cause instanceof Error ? cause.message : "来源可用性暂时无法确认");
    } finally { if (request === sequence.current) setLoading(false); }
  }, [tenantId, subjectId, administrator]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; confirmationToken.current = null; }; }, []);
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [load]);

  function begin() {
    const token = getStoredAccessToken();
    if (mutation.current || externallyBusy || snapshot || !token) return;
    if (pending) {
      confirmationToken.current = token; sent.current = true;
      setSnapshot({ source: null, sourceId: pending.sourceId, requestKey: pending.requestKey, uncertain: true });
      return;
    }
    if (storageError || !sourceReady(source) || source.create_available !== true) return;
    confirmationToken.current = token; sent.current = false;
    setSnapshot({ source, sourceId: source.source_id, requestKey: crypto.randomUUID(), uncertain: false });
  }

  function close() {
    if (mutation.current || stopConfirmation) return;
    confirmationToken.current = null; setSnapshot(null);
  }

  function beginStop() {
    if (mutation.current || externallyBusy || !snapshot?.uncertain) return;
    const token = confirmationToken.current;
    if (!token || token !== getStoredAccessToken()) {
      setStopConfirmation(null); setSnapshot(null); confirmationToken.current = null;
      notify.warning("登录会话已变化，请刷新后重新确认。"); return;
    }
    setStopConfirmation(snapshot);
  }

  function closeStop() { if (!mutation.current) setStopConfirmation(null); }

  async function submit(mode: "create" | "query" | "stop") {
    const queryOnly = mode !== "create";
    if (!tenantId || !subjectId || !snapshot || mutation.current || externallyBusy
      || (!queryOnly && (storageError || sent.current || snapshot.uncertain))) return;
    if (mode === "stop" && (stopConfirmation?.requestKey !== snapshot.requestKey || !snapshot.uncertain)) return;
    const expectedToken = confirmationToken.current, target = snapshot;
    if (!expectedToken || expectedToken !== getStoredAccessToken()) {
      setStopConfirmation(null); setSnapshot(null); confirmationToken.current = null;
      notify.warning("登录会话已变化，请刷新后重新确认。"); return;
    }
    if (!queryOnly && !sourceReady(target.source)) { notify.warning("来源批准已到期，请刷新来源。"); return; }
    if (!queryOnly) {
      try {
        const previous = readPendingCreation(tenantId, subjectId);
        if (previous) {
          setPending(previous); sent.current = true;
          setSnapshot({ source: null, sourceId: previous.sourceId, requestKey: previous.requestKey, uncertain: true });
          notify.warning("已有待确认创建，请先查询原请求。"); return;
        }
        const request = { requestKey: target.requestKey, sourceId: target.sourceId };
        reservePendingCreation(tenantId, subjectId, request); setPending(request);
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : "请求标识无法保存，未发送创建请求。";
        setStorageError(message); notify.error(cause, message); return;
      }
    }
    mutation.current = true; setBusy(true);
    if (!queryOnly) sent.current = true;
    try {
      const request = { requestKey: target.requestKey, sourceId: target.sourceId };
      const response = await withAuthenticatedToken(async (token) => {
        if (token !== expectedToken) throw new Error("登录会话已变化，请重新读取。");
        if (queryOnly) {
          const value = mode === "stop" ? await stopKnowledgeIngestionCreation(token, target.requestKey)
            : await getKnowledgeIngestionCreationStatus(token, target.requestKey);
          const outcome = creationRequestOutcome(value, request);
          if (mode === "stop" && outcome.status === "unobserved") throw new Error("停止回执未确认，请查询原请求状态。");
          return outcome;
        }
        if (!sourceReady(target.source)) throw new Error("来源批准已到期，请刷新后核对。");
        const job = await createKnowledgeIngestionJob(token, target.source.scope_digest, target.requestKey);
        validateCreatedJob(job, request);
        return { status: "recorded" as const, job };
      });
      if (!mounted.current || response.token !== getStoredAccessToken()) return;
      if (response.value.status === "unobserved") {
        setSnapshot({ ...target, uncertain: true });
        notify.warning("当前尚未观察到原任务。可继续查询，或明确封锁原创建请求；查询结果不代表在途请求未提交。");
        return;
      }
      try {
        clearConfirmedCreation(tenantId, subjectId, { requestKey: target.requestKey, sourceId: target.sourceId });
        setPending(null); setStorageError(null);
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : "任务已确认，请刷新核对会话中的请求记录。";
        setStorageError(message); notify.warning(message);
      }
      setSnapshot(null); setStopConfirmation(null); confirmationToken.current = null;
      if (response.value.status === "stopped") notify.success("原创建请求已封锁，迟到创建将被拒绝；未取消其他任务。");
      else {
        notify.success(mode === "stop" ? "原任务已经生成，已保留原任务；可在详情另行处理。" : "已确认导入任务记录。需要在详情中另行确认执行。");
        afterCreated.current(response.value.job);
      }
    } catch (cause) {
      if (mounted.current && expectedToken === getStoredAccessToken()) {
        setSnapshot({ ...target, uncertain: true });
        setStopConfirmation(null);
        notify.error(cause, "暂时无法确认原请求结果，请查询原请求状态，勿重复创建。");
      }
    } finally { mutation.current = false; if (mounted.current) setBusy(false); }
  }

  const canBegin = pending !== null || (!storageError && sourceReady(source) && source.create_available === true);
  return { source, loading, error, load, snapshot, busy, begin, close, pending, storageError, canBegin,
    stopConfirmation, beginStop, closeStop, stop: () => submit("stop"),
    create: () => submit("create"), query: () => submit("query") };
}
