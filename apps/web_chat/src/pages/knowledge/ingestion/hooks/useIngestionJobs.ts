import { useCallback, useEffect, useRef, useState } from "react";
import { cancelKnowledgeIngestionJob, executeKnowledgeIngestionJob, expireKnowledgeIngestionJob,
  getKnowledgeIngestionJob, listKnowledgeIngestionJobs } from "@/api";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { KnowledgeIngestionJob, KnowledgeIngestionSourceReadiness, KnowledgeIngestionStatus, PaginatedListResponse } from "@/types";
import { canExecuteIngestion, ingestionContractSupported, type IngestionCommand } from "../lib/presentation";

export function useIngestionJobs(subjectId: string | undefined, administrator: boolean) {
  const [result, setResult] = useState<PaginatedListResponse<KnowledgeIngestionJob> | null>(null);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [status, setStatus] = useState<KnowledgeIngestionStatus | "all">("all");
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<KnowledgeIngestionJob | null>(null);
  const [detailLoading, setDetailLoading] = useState(false), [detailError, setDetailError] = useState<string | null>(null);
  const [command, setCommand] = useState<IngestionCommand | null>(null);
  const [busy, setBusy] = useState(false);
  const listSequence = useRef(0), detailSequence = useRef(0), mutation = useRef(false), mounted = useRef(false);
  const confirmationToken = useRef<string | null>(null);
  const currentSubject = useRef(subjectId); currentSubject.current = subjectId;

  const load = useCallback(async () => {
    const sequence = ++listSequence.current;
    setLoading(true); setError(null); setResult(null);
    if (!administrator || !subjectId) { setLoading(false); return; }
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => listKnowledgeIngestionJobs(token, {
        page: page.current, pageSize: page.pageSize, status: status === "all" ? undefined : status,
      })));
      if (sequence === listSequence.current && response.token === getStoredAccessToken()) setResult(response.value);
    } catch (cause) {
      if (sequence === listSequence.current) setError(cause instanceof Error ? cause.message : "导入任务读取失败");
    } finally { if (sequence === listSequence.current) setLoading(false); }
  }, [page.current, page.pageSize, status, subjectId, administrator]);

  const loadDetail = useCallback(async () => {
    const sequence = ++detailSequence.current;
    setDetail(null); setDetailError(null); setDetailLoading(Boolean(selectedId));
    if (!selectedId || !administrator || !subjectId) { setDetailLoading(false); return; }
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => getKnowledgeIngestionJob(token, selectedId)));
      if (sequence === detailSequence.current && response.token === getStoredAccessToken()) setDetail(response.value);
    } catch (cause) {
      if (sequence === detailSequence.current) setDetailError(cause instanceof Error ? cause.message : "任务详情读取失败");
    } finally { if (sequence === detailSequence.current) setDetailLoading(false); }
  }, [selectedId, subjectId, administrator]);
  const latestLoad = useRef(load), latestDetailLoad = useRef(loadDetail);
  latestLoad.current = load; latestDetailLoad.current = loadDetail;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; confirmationToken.current = null; }; }, []);
  useEffect(() => { void load(); return () => { listSequence.current += 1; }; }, [load]);
  useEffect(() => { void loadDetail(); return () => { detailSequence.current += 1; }; }, [loadDetail]);
  useEffect(() => { setCommand(null); setSelectedId(null); setDetail(null); confirmationToken.current = null; }, [subjectId]);

  function changeStatus(next: KnowledgeIngestionStatus | "all") {
    if (mutation.current || next === status) return;
    listSequence.current += 1;
    setLoading(true); setResult(null); setError(null);
    setStatus(next); setPage((old) => ({ ...old, current: 1 }));
  }

  function changePage(current: number, pageSize: number) {
    if (mutation.current || (current === page.current && pageSize === page.pageSize)) return;
    listSequence.current += 1;
    setLoading(true); setResult(null); setError(null);
    setPage({ current, pageSize });
  }

  function selectDetail(id: string | null) {
    if (mutation.current || id === selectedId) return;
    detailSequence.current += 1;
    setDetail(null); setDetailError(null); setDetailLoading(id !== null); setSelectedId(id);
  }

  function requestCommand(job: KnowledgeIngestionJob, kind: IngestionCommand["kind"]) {
    const token = getStoredAccessToken();
    if (mutation.current || !token || !ingestionContractSupported(job)
      || (kind === "cancel" ? job.cancel_available !== true : kind === "expire" ? job.expire_available !== true
        : job.status !== "queued" && job.status !== "retry_wait")) return;
    confirmationToken.current = token;
    setCommand({ job, kind });
  }

  function closeCommand() {
    if (mutation.current) return;
    confirmationToken.current = null; setCommand(null);
  }

  async function confirmCommand(source: KnowledgeIngestionSourceReadiness | null) {
    if (!command || mutation.current || command.job.revision === null || !ingestionContractSupported(command.job)) return;
    const expectedToken = confirmationToken.current;
    if (!expectedToken || expectedToken !== getStoredAccessToken()) {
      setCommand(null); notify.warning("登录会话已变化，请刷新任务后重新确认。"); return;
    }
    if (command.kind === "execute" && !canExecuteIngestion(command.job, source)) {
      confirmationToken.current = null; setCommand(null);
      notify.warning("当前来源批准或任务条件已变化，请刷新后重新确认执行。"); return;
    }
    const target = command, requester = subjectId;
    const revision = command.job.revision;
    mutation.current = true; setBusy(true);
    try {
      const response = await withAuthenticatedToken((token) => {
        if (token !== expectedToken) throw new Error("登录会话已变化，请重新读取任务。");
        if (target.kind === "execute" && !canExecuteIngestion(target.job, source)) {
          throw new Error("当前来源批准或任务条件已变化，请刷新后重新确认执行。");
        }
        return target.kind === "cancel" ? cancelKnowledgeIngestionJob(token, target.job.id, revision)
          : target.kind === "expire" ? expireKnowledgeIngestionJob(token, target.job.id, revision)
            : executeKnowledgeIngestionJob(token, target.job.id, revision);
      });
      if (!mounted.current || currentSubject.current !== requester || response.token !== getStoredAccessToken()) return;
      if (response.value.id !== target.job.id || !ingestionContractSupported(response.value)
        || (target.kind === "execute" && response.value.scope_digest !== target.job.scope_digest)
        || response.value.status !== (target.kind === "cancel" ? "cancelled" : target.kind === "expire" ? "failed" : "completed")) {
        throw new Error("操作回执状态不可确认，请刷新任务核对结果。");
      }
      notify.success(target.kind === "cancel" ? "导入任务已取消，已保存的知识草稿保留。"
        : target.kind === "expire" ? "已记录任务超时或预算耗尽，未重新执行导入。" : "本次来源处理已完成，候选仍须独立审核和发布。");
      setCommand(null);
      await Promise.all([latestLoad.current(), latestDetailLoad.current()]);
    } catch (cause) {
      if (mounted.current && currentSubject.current === requester && expectedToken === getStoredAccessToken()) {
        notify.error(cause, "操作结果尚未确认，请刷新任务后核对，勿重复提交。");
        setCommand(null);
        await Promise.all([latestLoad.current(), latestDetailLoad.current()]);
      }
    } finally { mutation.current = false; if (mounted.current) setBusy(false); }
  }

  return { result, page, changePage, status, changeStatus, loading, error, load, selectedId, selectDetail,
    detail, detailLoading, detailError, loadDetail, command, closeCommand, busy, requestCommand, confirmCommand };
}
