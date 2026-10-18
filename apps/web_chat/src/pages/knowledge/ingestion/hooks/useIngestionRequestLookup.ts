import { useEffect, useRef, useState } from "react";
import { getKnowledgeIngestionCreationStatus } from "@/api";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import { creationRequestOutcome, normalizeCreationRequestKey } from "../lib/creationRequest";

export interface IngestionRequestLookupResult {
  /** 本次查询并匹配回执的规范UUID创建请求键。 */
  requestKey: string;
  /** 服务器本次观察时间，不表示业务创建或停止时间。 */
  observedAt: string;
  /** 已核验的原停止事实时间，其他状态为空。 */
  stoppedAt: string | null;
  /** 复用原回执契约校验得到的封闭观察结果。 */
  outcome: ReturnType<typeof creationRequestOutcome>;
}

function requestKeyError(value: string): string {
  if (!value.trim()) return "请输入原创建请求标识。";
  return normalizeCreationRequestKey(value) ? "" : "请输入完整的UUID格式创建请求标识。";
}

/** 独立只读查询，不读写会话待确认记录，也不构造新的创建或执行意图。 */
export function useIngestionRequestLookup(externallyBusy: boolean, onOpenJob: (jobId: string) => void) {
  const [open, setOpen] = useState(false), [value, setValue] = useState("");
  const [fieldError, setFieldError] = useState(""), [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ token: string; value: IngestionRequestLookupResult } | null>(null);
  const sequence = useRef(0), controller = useRef<AbortController | null>(null);
  const reading = useRef(false);

  function invalidate() {
    sequence.current += 1; controller.current?.abort(); controller.current = null;
    reading.current = false;
  }

  useEffect(() => () => { sequence.current += 1; controller.current?.abort(); }, []);

  function begin(initialKey = "") {
    if (externallyBusy) return;
    if (!getStoredAccessToken()) { notify.warning("登录会话不可用，请重新登录后查询。"); return; }
    invalidate(); setOpen(true); setValue(initialKey); setFieldError("");
    setError(null); setResult(null); setLoading(false);
  }

  function close() {
    invalidate(); setOpen(false); setValue(""); setFieldError("");
    setError(null); setResult(null); setLoading(false);
  }

  function changeValue(next: string) {
    invalidate(); setValue(next); setFieldError(requestKeyError(next));
    setError(null); setResult(null); setLoading(false);
  }

  async function query() {
    if (!open || reading.current || externallyBusy) return;
    const problem = requestKeyError(value); setFieldError(problem);
    const requestKey = normalizeCreationRequestKey(value);
    if (problem || requestKey === null) return;
    const expectedToken = getStoredAccessToken();
    if (!expectedToken) { setResult(null); setError("登录会话不可用，请重新登录后查询。"); return; }
    invalidate();
    const request = sequence.current, abort = new AbortController(); controller.current = abort;
    reading.current = true; setLoading(true); setError(null); setResult(null);
    try {
      const response = await delayRequest(() => {
        if (abort.signal.aborted) throw new DOMException("查询已取消", "AbortError");
        return withAuthenticatedToken((token) => {
          if (abort.signal.aborted) throw new DOMException("查询已取消", "AbortError");
          if (token !== expectedToken) throw new Error("登录会话已变化，请重新查询。");
          return getKnowledgeIngestionCreationStatus(token, requestKey, abort.signal);
        });
      });
      if (request !== sequence.current || abort.signal.aborted) return;
      if (response.token !== getStoredAccessToken()) { setError("登录会话已变化，请重新查询。"); return; }
      const outcome = creationRequestOutcome(response.value, { requestKey });
      setResult({ token: response.token, value: {
        requestKey, observedAt: response.value.observed_at, stoppedAt: response.value.stopped_at, outcome,
      } });
    } catch (cause) {
      if (request === sequence.current && !abort.signal.aborted) {
        setError(expectedToken === getStoredAccessToken()
          ? cause instanceof Error ? cause.message : "原请求暂时无法查询，请保留标识后重试。"
          : "登录会话已变化，请重新查询。");
      }
    } finally {
      if (request === sequence.current) { controller.current = null; reading.current = false; setLoading(false); }
    }
  }

  function openJob() {
    if (externallyBusy || loading || result === null || result.value.outcome.status !== "recorded") return;
    if (result.token !== getStoredAccessToken()) {
      invalidate(); setResult(null); setError("登录会话已变化，请重新查询。");
      notify.warning("登录会话已变化，请重新查询原请求。"); return;
    }
    const jobId = result.value.outcome.job.id;
    close(); onOpenJob(jobId);
  }

  const visibleResult = result !== null && result.token === getStoredAccessToken() ? result.value : null;
  return { open, value, fieldError, error, loading, result: visibleResult, begin, close, changeValue, query, openJob };
}
