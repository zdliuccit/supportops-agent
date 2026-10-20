"""恢复屏障内按原完整事实集合汇总既有复核回执；不调重施入口、不授予开放。"""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from supportops_core.restore_review_contracts import (
    CredentialRestoreReviewEvidence, KnowledgeRestoreReviewEvidence, RestoreReviewEvidence,
    RestoreReviewItem, RestoreReviewReport, RestoreReviewRequest, SessionRestoreReviewEvidence,
    ReviewReason, ReviewStatus,
)
from supportops_core.restore_session_contracts import restore_session_digest
from supportops_core.restore_startup_barrier import RestoreStartupBlocked, _read_context, assert_service_start_allowed
from supportops_core.retention_batch_contracts import batch_digest
from supportops_core.retention_propagation_contracts import PropagationDeletionFact

_SUPPORTED = frozenset({"auth_sessions", "knowledge", "credentials"})
_EVIDENCE_TYPES = (SessionRestoreReviewEvidence, KnowledgeRestoreReviewEvidence, CredentialRestoreReviewEvidence)
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 32 * 1024


class RestoreReviewAuthority(Protocol):
    authority_id: str

    async def assert_current(self, request: RestoreReviewRequest) -> None:
        """独立确认原快照完整事实集合/账本代次及当前多租户读取许可、完整副本盘点。

        fact_set_digest不是快照字节摘要；必须独立比对全部原事实，不能批准省略子集。
        核对固定reader批准/范围及原恢复实例，平台角色不能隐式取得维护读取许可。
        """
        ...

    async def assert_evidence_current(self, request: RestoreReviewRequest, fact: PropagationDeletionFact,
        evidence: RestoreReviewEvidence,
    ) -> None:
        """独立查询原持久审计成员/来源请求、回执状态/观察与证据摘要及当前读取许可。

        不得只相信调用者提供的digest/receipt；最新有效性变化或未知拒绝，不能回退旧通过记录。
        汇总不调用重施/业务目标，实际资源核对真实性由获批只读集成确认。
        """
        ...


class RestoreReviewReader(Protocol):
    reader_id: str

    async def read(self, request: RestoreReviewRequest, fact: PropagationDeletionFact) -> RestoreReviewEvidence | None:
        """仅独立读取当前原回执和持久审计引用；不申请/重放写入、不补造缺失结果。"""
        ...


class RestoreReviewUnconfirmed(RuntimeError):
    code = "RESTORE_REVIEW_UNCONFIRMED"


def _context(request: RestoreReviewRequest) -> None:
    if not request.created_at <= datetime.now(UTC) < request.expires_at:
        raise ValueError()
    try:
        assert_service_start_allowed(mode="quarantined", expected_restore_id=request.restore_id,
            expected_context_digest=request.context_digest)
    except RestoreStartupBlocked as error:
        if error.code != "RESTORE_QUARANTINE_ACTIVE":
            raise ValueError() from None
    else:
        raise ValueError()
    raw = _read_context()
    if hashlib.sha256(raw).hexdigest() != request.context_digest:
        raise ValueError()
    marker = json.loads(raw.decode("utf-8"))
    if (marker["restore_id"] != str(request.restore_id) or marker["backup_id"] != request.backup_id
        or marker["target_alias"] != request.target_alias or marker["ledger_id"] != str(request.ledger_id)
        or marker["ledger_generation"] != request.ledger_generation
        or marker["ledger_snapshot_sha256"] != request.ledger_snapshot_digest):
        raise ValueError()


def _packet(request: RestoreReviewRequest, fact: PropagationDeletionFact, reader_id: str,
    evidence: RestoreReviewEvidence,
) -> RestoreReviewItem:
    if type(evidence) not in _EVIDENCE_TYPES:
        raise ValueError()
    raw = evidence.model_dump_json()
    if len(raw.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise ValueError()
    # 重核封闭模型，不接受model_construct绕过原请求/回执校验后直接进入汇总。
    evidence = type(evidence).model_validate_json(raw)
    receipt = evidence.receipt
    if isinstance(evidence, SessionRestoreReviewEvidence):
        source = evidence.request
        scope = (source.restore_id, source.context_digest, source.backup_id, source.target_alias,
            source.ledger_id, source.ledger_generation, source.ledger_snapshot_digest)
        source_fact = source.fact
        request_digest = restore_session_digest(source)
        receipt_digest = restore_session_digest(evidence.receipt)
        category = "auth_sessions"
        isolated = evidence.receipt.access_revocation_confirmed
        window = source.created_at, source.expires_at
    elif isinstance(evidence, KnowledgeRestoreReviewEvidence):
        knowledge = evidence.request
        scope = (knowledge.restore_id, knowledge.context_digest, knowledge.backup_id, knowledge.target_alias,
            knowledge.propagation.ledger_id, knowledge.propagation.ledger_generation, knowledge.ledger_snapshot_digest)
        source_fact = knowledge.propagation.fact
        request_digest = batch_digest(knowledge)
        receipt_digest = batch_digest(evidence.receipt)
        category = "knowledge"
        isolated = evidence.receipt.snapshot_read_quarantine_confirmed
        window = knowledge.created_at, knowledge.expires_at
    elif isinstance(evidence, CredentialRestoreReviewEvidence):
        credential = evidence.request
        scope = (credential.restore_id, credential.context_digest, credential.backup_id, credential.target_alias,
            credential.ledger_id, credential.ledger_generation, credential.ledger_snapshot_digest)
        source_fact = credential.fact
        request_digest = batch_digest(credential)
        receipt_digest = batch_digest(evidence.receipt)
        category = "credentials"
        isolated = evidence.receipt.fixed_revision_read_quarantine_confirmed
        window = credential.created_at, credential.expires_at
    else:
        raise ValueError()
    expected = (request.restore_id, request.context_digest, request.backup_id, request.target_alias,
        request.ledger_id, request.ledger_generation, request.ledger_snapshot_digest)
    now = datetime.now(UTC)
    if (scope != expected or category != fact.category or evidence.reader_id != reader_id
        or source_fact.id != fact.id or source_fact.record_digest != fact.record_digest
        or receipt.request_id != evidence.request.request_id or receipt.request_digest != request_digest
        or not window[0] <= receipt.observed_at < window[1]
        or not 0 <= (now - receipt.observed_at).total_seconds() <= request.max_observation_age_seconds):
        raise ValueError()
    status: ReviewStatus
    reason: ReviewReason
    if isolated:
        status = "isolation_observed" if fact.action == "quarantined" else "needs_attention"
        reason = "ISOLATION_OBSERVED" if fact.action == "quarantined" else "ORIGINAL_ERASURE_UNCONFIRMED"
    else:
        status = "not_observed" if receipt.status == "not_observed" else "needs_attention"
        reason = "TARGET_NOT_OBSERVED" if receipt.status == "not_observed" else "ISOLATION_UNCONFIRMED"
    return RestoreReviewItem(fact_id=fact.id, fact_digest=fact.record_digest, category=fact.category,
        original_action=fact.action, status=status, reason=reason, isolation_observed=isolated,
        source_request_digest=request_digest, source_receipt_digest=receipt_digest,
        source_audit_record_digest=evidence.audit_record_digest, observed_at=receipt.observed_at)


async def review_restored_resources(request: RestoreReviewRequest, *, authority: RestoreReviewAuthority | None = None,
    readers: Mapping[str, RestoreReviewReader] | None = None,
) -> RestoreReviewReport:
    """有界核对完整原事实集合；默认无可信依赖即拒绝，不访问引擎或调用重施函数。"""
    try:
        if (not isinstance(request, RestoreReviewRequest) or authority is None or readers is None
            or authority.authority_id != request.authority_id or len(readers) > 3
            or len(request.model_dump_json().encode("utf-8")) > MAX_INPUT_BYTES):
            raise ValueError()
        registry = dict(readers)
        bindings: dict[str, str] = {binding.category: binding.reader_id for binding in request.readers}
        if not set(registry) <= set(bindings) or any(registry[key].reader_id != bindings[key] for key in registry):
            raise ValueError()

        async def current() -> None:
            _context(request)
            if authority.authority_id != request.authority_id:
                raise ValueError()
            async with asyncio.timeout(0.5):
                await authority.assert_current(request)
            _context(request)

        def unresolved(fact: PropagationDeletionFact, status: ReviewStatus, reason: ReviewReason) -> RestoreReviewItem:
            return RestoreReviewItem(fact_id=fact.id, fact_digest=fact.record_digest, category=fact.category,
                original_action=fact.action, status=status, reason=reason, isolation_observed=False,
                source_request_digest=None, source_receipt_digest=None, source_audit_record_digest=None, observed_at=None)

        deadline = asyncio.get_running_loop().time() + 20
        async with asyncio.timeout(20):
            await current()
            items: list[RestoreReviewItem] = []
            checked: list[tuple[PropagationDeletionFact, RestoreReviewEvidence]] = []
            for fact in request.facts:
                await current()
                if fact.category not in _SUPPORTED:
                    items.append(unresolved(fact, "unsupported", "ADAPTER_NOT_IMPLEMENTED"))
                    continue
                reader = registry.get(fact.category)
                if reader is None:
                    items.append(unresolved(fact, "missing_evidence", "READER_UNAVAILABLE"))
                    continue
                if reader.reader_id != bindings[fact.category]:
                    raise ValueError()
                evidence: RestoreReviewEvidence | None = None
                item: RestoreReviewItem | None = None
                try:
                    async with asyncio.timeout(1):
                        evidence = await reader.read(request, fact)
                    if reader.reader_id != bindings[fact.category]:
                        raise ValueError()
                    if evidence is not None:
                        item = _packet(request, fact, bindings[fact.category], evidence)
                except Exception:
                    items.append(unresolved(fact, "unconfirmed", "EVIDENCE_UNCONFIRMED"))
                    await current()
                    continue
                await current()
                if evidence is None or item is None:
                    items.append(unresolved(fact, "missing_evidence", "EVIDENCE_MISSING"))
                    continue
                async with asyncio.timeout(0.5):
                    await authority.assert_evidence_current(request, fact, evidence)
                await current()
                items.append(item)
                checked.append((fact, evidence))
            # 返回前重新查证原持久来源，不能让后续等待使较早回执失效后仍计为通过。
            # 这仍是有界顺序观察，不声称跨资源原子快照或授予开放许可。
            for fact, evidence in checked:
                await current()
                async with asyncio.timeout(0.5):
                    await authority.assert_evidence_current(request, fact, evidence)
            await current()
            now = datetime.now(UTC)
            report = RestoreReviewReport(request_id=request.request_id, request_digest=batch_digest(request),
                restore_id=request.restore_id, fact_set_digest=request.fact_set_digest, inventory_digest=request.inventory_digest,
                status="listed_isolations_observed" if items and all(item.status == "isolation_observed" for item in items)
                    else "needs_attention", fact_count=len(items), isolation_observed_count=sum(item.isolation_observed for item in items),
                erasure_required_count=sum(item.original_action == "erased" for item in items), items=tuple(items), reviewed_at=now)
            if len(report.model_dump_json().encode("utf-8")) > MAX_INPUT_BYTES:
                raise ValueError()
            _context(request)
            finished_at = datetime.now(UTC)
            if any(item.observed_at is not None and not 0 <= (finished_at - item.observed_at).total_seconds()
                <= request.max_observation_age_seconds for item in items):
                raise ValueError()
        if asyncio.get_running_loop().time() > deadline:
            raise ValueError()
        return report
    except Exception:
        raise RestoreReviewUnconfirmed() from None
