import { useEffect, useRef, useState } from "react";

import { getKnowledgeIndex, getKnowledgeIndexJobs, rebuildKnowledgeIndex, retryKnowledgeIndexJob } from "@/api";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { KnowledgeIndexJob, KnowledgeIndexState } from "@/types";

/** 页面持有索引状态和分页请求，展示组件不重复请求。 */
export function useKnowledgeIndex(documentId: string | undefined) {
  const [state, setState] = useState<KnowledgeIndexState | null>(null);
  const [jobs, setJobs] = useState<KnowledgeIndexJob[]>([]);
  const [total, setTotal] = useState(0);
  const [pagination, setPagination] = useState({ current: 1, pageSize: 10 });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  async function load(next = pagination) {
    if (!documentId) return;
    const current = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const result = await delayRequest(() => withRefreshedToken(async (token) => {
        const [index, events] = await Promise.all([
          getKnowledgeIndex(token, documentId), getKnowledgeIndexJobs(token, documentId, next.current, next.pageSize),
        ]);
        return { index, events };
      }));
      if (current !== requestId.current) return;
      setState(result.value.index);
      setJobs(result.value.events.items);
      setTotal(result.value.events.total);
    } catch (cause) {
      if (current === requestId.current) setError(cause instanceof Error ? cause.message : "读取关键词索引状态失败");
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    setState(null);
    setJobs([]);
    setTotal(0);
    const first = { current: 1, pageSize: 10 };
    setPagination(first);
    void load(first);
    return () => { requestId.current += 1; };
  }, [documentId]);

  async function rebuild() {
    if (!documentId || busy) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => rebuildKnowledgeIndex(token, documentId));
      notify.success("重建任务已持久化，Worker 将构建并切换关键词索引。");
      await load();
    } catch (cause) { notify.error(cause, "提交重建任务失败"); }
    finally { setBusy(false); }
  }

  async function retry(job: KnowledgeIndexJob) {
    if (busy) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => retryKnowledgeIndexJob(token, job.id, job.attempts));
      notify.success("已创建有限重试任务，旧死信和尝试次数保留。");
      await load();
    } catch (cause) { notify.error(cause, "恢复索引任务失败"); }
    finally { setBusy(false); }
  }

  return { state, jobs, total, pagination, loading, busy, error, load, rebuild, retry,
    changePage: (current: number, pageSize: number) => {
      if (loading || busy) return;
      const next = { current, pageSize };
      setPagination(next);
      void load(next);
    },
  };
}
