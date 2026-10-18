import { useCallback, useEffect, useRef, useState } from "react";
import { getKnowledgeIngestionGovernance, listKnowledgeIngestionOwners, updateKnowledgeIngestionGovernance,
  withdrawKnowledgeIngestionCandidate } from "@/api";
import { getStoredAccessToken, withAuthenticatedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { KnowledgeIngestionGovernance } from "@/types";
import { editableGovernance, governanceFormErrors, isWithdrawalReason, localReviewInput, reviewInputPayload, withdrawalAvailable,
  type EditableIngestionGovernance } from "../lib/governance";

export function useIngestionGovernance(jobId: string, externalBusy: boolean,
  onBusyChange: (busy: boolean) => void, invalidateComparison: () => void) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [view, setView] = useState<KnowledgeIngestionGovernance | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditableIngestionGovernance | null>(null);
  const [withdrawal, setWithdrawal] = useState<EditableIngestionGovernance | null>(null);
  const [withdrawalReason, setWithdrawalReason] = useState("");
  const [withdrawalError, setWithdrawalError] = useState("");
  const [owner, setOwner] = useState(""), [due, setDue] = useState("");
  const [errors, setErrors] = useState({ owner: "", due: "" }), [saving, setSaving] = useState(false);
  const sequence = useRef(0), mutation = useRef(false), mounted = useRef(false);
  const confirmationToken = useRef<string | null>(null);

  const load = useCallback(async () => {
    const request = ++sequence.current;
    setView(null); setError(null); setLoading(Boolean(selectedId));
    if (!selectedId) return;
    try {
      const response = await delayRequest(() => withAuthenticatedToken((token) => getKnowledgeIngestionGovernance(token, jobId, selectedId)));
      if (request !== sequence.current || response.token !== getStoredAccessToken()) return;
      if (response.value.job_id !== jobId || response.value.materialization_id !== selectedId) throw new Error("治理回执与当前草稿不一致，请刷新。");
      setView(response.value);
    } catch (cause) {
      if (request === sequence.current) setError(cause instanceof Error ? cause.message : "治理信息读取失败");
    } finally { if (request === sequence.current) setLoading(false); }
  }, [jobId, selectedId]);

  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; confirmationToken.current = null; onBusyChange(false);
  }; }, [onBusyChange]);
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [load]);

  function select(id: string | null) {
    if (mutation.current || externalBusy || id === selectedId) return;
    sequence.current += 1; setView(null); setError(null); setLoading(id !== null); setSelectedId(id);
    setEditing(null); setWithdrawal(null); confirmationToken.current = null;
  }

  function beginEdit() {
    const token = getStoredAccessToken();
    if (externalBusy || mutation.current || withdrawal || !token || !editableGovernance(view)) return;
    confirmationToken.current = token; setEditing(view);
    setOwner(view.owner_user_id); setDue(localReviewInput(view.review_due_at));
    setErrors(governanceFormErrors(view.owner_user_id, localReviewInput(view.review_due_at), view));
  }

  function closeEdit() {
    if (mutation.current) return;
    setEditing(null); confirmationToken.current = null;
  }

  function beginWithdrawal() {
    const token = getStoredAccessToken();
    if (externalBusy || mutation.current || editing || !token || !withdrawalAvailable(view)) return;
    confirmationToken.current = token; setWithdrawal(view); setWithdrawalReason(""); setWithdrawalError("");
  }

  function closeWithdrawal() {
    if (mutation.current) return;
    setWithdrawal(null); confirmationToken.current = null;
  }

  function changeWithdrawalReason(value: string) {
    if (mutation.current) return;
    setWithdrawalReason(value); setWithdrawalError(isWithdrawalReason(value) ? "" : "请选择撤回原因");
  }

  async function confirmWithdrawal() {
    if (externalBusy || mutation.current || !withdrawal || !withdrawalAvailable(withdrawal)) return;
    const reason = withdrawalReason;
    if (!isWithdrawalReason(reason)) { setWithdrawalError("请选择撤回原因"); return; }
    const snapshot = withdrawal, expected = confirmationToken.current;
    if (!expected || expected !== getStoredAccessToken()) {
      closeWithdrawal(); notify.warning("登录会话已变化，请重新读取后确认撤回。"); return;
    }
    mutation.current = true; setSaving(true); onBusyChange(true); invalidateComparison();
    try {
      const response = await withAuthenticatedToken((token) => {
        if (token !== expected) throw new Error("登录会话已变化，请重新读取。");
        return withdrawKnowledgeIngestionCandidate(token, jobId, snapshot.materialization_id, {
          expected_governance_digest: snapshot.governance_digest, reason_code: reason,
        });
      });
      if (!mounted.current || response.token !== getStoredAccessToken()) return;
      const receipt = response.value;
      if (receipt.schema_version !== "knowledge_ingestion_governance_v1" || receipt.automatic_publish_enabled !== false
        || receipt.job_id !== jobId || receipt.materialization_id !== snapshot.materialization_id
        || receipt.status !== "candidate_withdrawn" || receipt.update_available !== false || receipt.withdraw_available !== false) {
        throw new Error("撤回回执不可确认，请重新读取当前状态。");
      }
      setWithdrawal(null); confirmationToken.current = null;
      notify.success("候选已撤回，当前正式版本和索引保持不变。");
      await load();
    } catch (cause) {
      if (mounted.current && expected === getStoredAccessToken()) {
        setWithdrawal(null); confirmationToken.current = null;
        notify.error(cause, "撤回结果尚未确认，请刷新核对，勿重复提交。");
        await load();
      }
    } finally {
      mutation.current = false;
      if (mounted.current) { setSaving(false); onBusyChange(false); }
    }
  }

  function changeOwner(value: string) {
    if (mutation.current || !editing) return;
    setOwner(value); setErrors(governanceFormErrors(value, due, editing));
  }
  function changeDue(value: string) {
    if (mutation.current || !editing) return;
    setDue(value); setErrors(governanceFormErrors(owner, value, editing));
  }

  const loadOwners = useCallback(async (keywords: string, pageSize: number) => {
    const expected = confirmationToken.current;
    if (!editing || !expected || expected !== getStoredAccessToken()) throw new Error("登录会话已变化，请重新打开设置。");
    const response = await withAuthenticatedToken((token) => {
      if (token !== expected) throw new Error("登录会话已变化，请重新打开设置。");
      return listKnowledgeIngestionOwners(token, jobId, editing.materialization_id, keywords, pageSize);
    });
    if (!mounted.current || response.token !== getStoredAccessToken() || confirmationToken.current !== expected) {
      throw new Error("当前设置已关闭或登录会话已变化。");
    }
    return response.value.items.map((user) => ({ value: user.id, label: user.display_name, description: user.id }));
  }, [editing, jobId]);

  async function save() {
    if (mutation.current || externalBusy || !editing) return;
    const snapshot = editing, expected = confirmationToken.current;
    const validation = governanceFormErrors(owner, due, snapshot); setErrors(validation);
    if (validation.owner || validation.due) return;
    if (!expected || expected !== getStoredAccessToken()) {
      closeEdit(); notify.warning("登录会话已变化，请重新读取后设置。"); return;
    }
    const requestedDue = reviewInputPayload(due, snapshot), requestedOwner = owner;
    mutation.current = true; setSaving(true); onBusyChange(true); invalidateComparison();
    try {
      const response = await withAuthenticatedToken((token) => {
        if (token !== expected) throw new Error("登录会话已变化，请重新读取后设置。");
        return updateKnowledgeIngestionGovernance(token, jobId, snapshot.materialization_id, {
          expected_governance_digest: snapshot.governance_digest, owner_user_id: requestedOwner, review_due_at: requestedDue,
        });
      });
      if (!mounted.current || response.token !== getStoredAccessToken()) return;
      const saved = response.value;
      if (!editableGovernance(saved) || saved.job_id !== jobId || saved.materialization_id !== snapshot.materialization_id
        || saved.document_id !== snapshot.document_id || saved.owner_user_id !== requestedOwner
        || (saved.review_due_at === null ? null : new Date(saved.review_due_at).getTime()) !==
          (requestedDue === null ? null : new Date(requestedDue).getTime())) {
        throw new Error("保存回执不可确认，请重新读取治理信息核对。");
      }
      setEditing(null); confirmationToken.current = null;
      notify.success("负责人和复核日期已保存，审核与发布仍需独立操作。");
      await load();
    } catch (cause) {
      if (mounted.current && expected === getStoredAccessToken()) {
        setEditing(null); confirmationToken.current = null;
        notify.error(cause, "保存结果尚未确认，请重新读取后核对，勿重复提交。");
        await load();
      }
    } finally {
      mutation.current = false;
      if (mounted.current) { setSaving(false); onBusyChange(false); }
    }
  }

  return { selectedId, select, view, loading, error, load, editing, beginEdit, closeEdit,
    owner, due, errors, changeOwner, changeDue, saving, save, loadOwners,
    withdrawal, withdrawalReason, withdrawalError, beginWithdrawal, closeWithdrawal, changeWithdrawalReason, confirmWithdrawal };
}
