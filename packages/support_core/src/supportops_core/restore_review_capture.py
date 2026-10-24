"""从本次独立复核捕获原回执，绑定最新持久审计并只创建受保护产物；不重施隔离。"""

import asyncio
import hashlib
import os
import stat
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncEngine

from supportops_core.restore_credential_audit import (
    PostgresRestoreCredentialAuditSink, RestoreCredentialAuditRecord, RestoreCredentialHistoryAuthority,
)
from supportops_core.restore_credential_contracts import RestoreCredentialReceipt, RestoreCredentialRequest
from supportops_core.restore_credential_reapplication import RestoreCredentialAuthority, verify_restored_credential_quarantine
from supportops_core.restore_knowledge_audit import (
    PostgresRestoreKnowledgeAuditSink, RestoreKnowledgeAuditRecord, RestoreKnowledgeHistoryAuthority,
)
from supportops_core.restore_knowledge_contracts import RestoreKnowledgeReceipt, RestoreKnowledgeRequest
from supportops_core.restore_knowledge_reapplication import RestoreKnowledgeAuthority, verify_restored_knowledge_quarantine
from supportops_core.restore_review import MAX_EVIDENCE_BYTES, MAX_INPUT_BYTES, _context, _packet
from supportops_core.restore_review_artifacts import (
    ProtectedRestoreReviewReader, RestoreReviewArtifactAuthority, RestoreReviewArtifactBinding,
    RestoreReviewArtifactScope, _deadline, _read,
)
from supportops_core.restore_review_contracts import (
    CredentialRestoreReviewEvidence, KnowledgeRestoreReviewEvidence, RestoreReviewEvidence, RestoreReviewRequest, ReviewCategory,
    SessionRestoreReviewEvidence,
)
from supportops_core.restore_session_audit import (
    PostgresRestoreSessionAuditSink, RestoreSessionAuditRecord, RestoreSessionHistoryAuthority,
    RestoreSessionReceiptRecord,
)
from supportops_core.restore_session_contracts import (
    RestoreSessionAudit, RestoreSessionReceipt, RestoreSessionRequest, restore_session_digest,
)
from supportops_core.restore_session_reapplication import RestoreSessionAuthority, verify_restored_session_quarantine
from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract, batch_digest
from supportops_core.retention_propagation_contracts import PropagationDeletionFact
from supportops_core.retention_tombstone_snapshot import MAX_FACTS

SourceRequest = RestoreKnowledgeRequest | RestoreCredentialRequest | RestoreSessionRequest
SourceReceipt = RestoreKnowledgeReceipt | RestoreCredentialReceipt | RestoreSessionReceipt
SourceAuditRecord = RestoreKnowledgeAuditRecord | RestoreCredentialAuditRecord | RestoreSessionAuditRecord | RestoreSessionReceiptRecord
CapturedEvidence = KnowledgeRestoreReviewEvidence | CredentialRestoreReviewEvidence | SessionRestoreReviewEvidence


class RestoreReviewCapturePlan(RetentionBatchContract):
    schema_version: Literal["restore_review_capture_plan_v1"] = "restore_review_capture_plan_v1"
    publication_id: UUID
    review_request_digest: Digest
    source_request_digest: Digest
    fact_id: UUID
    fact_digest: Digest
    category: ReviewCategory
    reader_id: Identifier
    parent_directory: str = Field(min_length=2, max_length=4096, repr=False)
    expected_uid: int = Field(ge=1, le=2**32 - 2)

    @property
    def directory(self) -> str:
        return f"{self.parent_directory}/restore-review.{self.publication_id}"

    @model_validator(mode="after")
    def fixed_destination(self) -> "RestoreReviewCapturePlan":
        # 复用reader对完整目标路径/身份的限制；这里不打开或创建文件。
        RestoreReviewArtifactScope(review_request_digest=self.review_request_digest, reader_id=self.reader_id,
            category=self.category, directory=self.directory, expected_uid=self.expected_uid, bindings=())
        return self


class RestoreReviewCaptureReceipt(RetentionBatchContract):
    schema_version: Literal["restore_review_capture_receipt_v1"] = "restore_review_capture_receipt_v1"
    publication_id: UUID
    review_request_digest: Digest
    source_request_digest: Digest
    source_audit_record_digest: Digest
    source_observed_at: AwareDatetime
    artifact_sha256: Digest
    artifact_scope: RestoreReviewArtifactScope = Field(repr=False)
    published_at: AwareDatetime
    status: Literal["artifact_published"] = "artifact_published"
    source_reapplication_performed: Literal[False] = False
    reader_access_authorized: Literal[False] = False
    restore_access_allowed: Literal[False] = False

    @model_validator(mode="after")
    def fixed_receipt(self) -> "RestoreReviewCaptureReceipt":
        if (self.artifact_scope.review_request_digest != self.review_request_digest
            or len(self.artifact_scope.bindings) != 1
            or self.artifact_scope.bindings[0].artifact_sha256 != self.artifact_sha256
            or self.source_observed_at > self.published_at):
            raise ValueError("RESTORE_REVIEW_CAPTURE_RECEIPT_INVALID")
        return self


class RestoreReviewCaptureAuthority(RestoreReviewArtifactAuthority, Protocol):
    async def assert_capture_allowed(self, request: RestoreReviewRequest, plan: RestoreReviewCapturePlan,
        source_request: SourceRequest,
    ) -> None:
        """批准本次只读目标复核/审计追加和固定非root身份/输出父目录/一次性目录。

        须独立核对实际目标、source authority和审计/历史许可；会话还需明确批准
        本次原回执及原观察时间的追加保留。不授予隔离写入或reader访问。
        """
        ...

    async def assert_publication_allowed(self, request: RestoreReviewRequest, plan: RestoreReviewCapturePlan,
        evidence: CapturedEvidence,
    ) -> None:
        """当前独立原回执持久保留许可；不把文件生成当作reader allowlist或恢复开放批准。"""
        ...


class RestoreReviewCaptureUnconfirmed(RuntimeError):
    code = "RESTORE_REVIEW_CAPTURE_UNCONFIRMED"


class RoutedRestoreReviewArtifactReader:
    """将同类别逐事实产物接入一个reader槽；固定唯一路由，不选旧通过或回退目录。"""

    def __init__(self, request: RestoreReviewRequest, *, category: ReviewCategory, reader_id: str,
        scopes: tuple[RestoreReviewArtifactScope, ...], authority: RestoreReviewArtifactAuthority,
    ) -> None:
        try:
            if (not isinstance(request, RestoreReviewRequest) or not isinstance(scopes, tuple)
                or len(scopes) > MAX_FACTS or authority is None or authority.authority_id != request.authority_id
                or len(request.model_dump_json().encode("utf-8")) > MAX_INPUT_BYTES
                or not any(reader.category == category and reader.reader_id == reader_id for reader in request.readers)):
                raise ValueError()
            request_digest = batch_digest(request)
            facts = {fact.id: fact.record_digest for fact in request.facts if fact.category == category}
            routes: dict[UUID, RestoreReviewArtifactScope] = {}
            for scope in scopes:
                if (not isinstance(scope, RestoreReviewArtifactScope) or scope.category != category
                    or scope.reader_id != reader_id or scope.review_request_digest != request_digest
                    or len(routes) + len(scope.bindings) > MAX_FACTS):
                    raise ValueError()
                for binding in scope.bindings:
                    if (binding.fact_id in routes or binding.fact_id not in facts
                        or facts[binding.fact_id] != binding.fact_digest):
                        raise ValueError()
                    routes[binding.fact_id] = scope
            self._digest, self._category, self._reader_id = request_digest, category, reader_id
            self._facts, self._routes, self._authority = facts, routes, authority
        except Exception:
            raise RestoreReviewCaptureUnconfirmed() from None

    @property
    def reader_id(self) -> str:
        return self._reader_id

    async def read(self, request: RestoreReviewRequest, fact: PropagationDeletionFact) -> RestoreReviewEvidence | None:
        try:
            expires_at = time.monotonic() + 0.95
            async with asyncio.timeout(0.95):
                if (not isinstance(request, RestoreReviewRequest) or not isinstance(fact, PropagationDeletionFact)
                    or batch_digest(request) != self._digest or fact.category != self._category
                    or self._facts.get(fact.id) != fact.record_digest
                    or self._authority.authority_id != request.authority_id):
                    raise ValueError()
                scope = self._routes.get(fact.id)
                if scope is None:
                    _context(request)
                    async with asyncio.timeout(0.5):
                        await self._authority.assert_current(request)
                    _context(request)
                    evidence = None
                else:
                    reader = ProtectedRestoreReviewReader(request, scope, authority=self._authority)
                    evidence = await reader.read(request, fact)
                if self._authority.authority_id != request.authority_id:
                    raise ValueError()
                _deadline(expires_at)
            return evidence
        except Exception:
            raise RestoreReviewCaptureUnconfirmed() from None


def _identity(info: os.stat_result) -> tuple[int, ...]:
    # 新建目录会合法改变父目录时间/链接数；这里只固定父目录对象、属主和权限。
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode


@contextmanager
def _directory(plan: RestoreReviewCapturePlan, expires_at: float) -> Iterator[tuple[int, int, Callable[[], None]]]:
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_DIRECTORY
        parent_fd = os.open("/", flags)
        stack.callback(os.close, parent_fd)
        chain: list[tuple[int, int | None, str | None, tuple[int, ...]]] = []
        parts = plan.parent_directory.split("/")[1:]
        for index, part in enumerate([None, *parts]):
            _deadline(expires_at)
            previous = None
            if part is not None:
                previous = parent_fd
                parent_fd = os.open(part, flags, dir_fd=previous)
                stack.callback(os.close, parent_fd)
            info = os.fstat(parent_fd)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, plan.expected_uid}
                or stat.S_IMODE(info.st_mode) & 0o022
                or index == len(parts) and (info.st_uid != plan.expected_uid or stat.S_IMODE(info.st_mode) != 0o700)):
                raise ValueError()
            chain.append((parent_fd, previous, part, _identity(info)))
        name = f"restore-review.{plan.publication_id}"
        # mkdir对既有名称直接失败；未知/部分目录也不能被接管、覆盖或自动删除后重跑。
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        child_fd = os.open(name, flags, dir_fd=parent_fd)
        stack.callback(os.close, child_fd)
        created = _identity(os.fstat(child_fd))

        def validate() -> None:
            _deadline(expires_at)
            if os.geteuid() != plan.expected_uid:
                raise ValueError()
            for descriptor, previous, part, expected in chain:
                _deadline(expires_at)
                if _identity(os.fstat(descriptor)) != expected:
                    raise ValueError()
                if previous is not None and part is not None:
                    if _identity(os.stat(part, dir_fd=previous, follow_symlinks=False)) != expected:
                        raise ValueError()
            info = os.fstat(child_fd)
            linked = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != plan.expected_uid
                or stat.S_IMODE(info.st_mode) not in {0o700, 0o500}
                or _identity(info)[:4] != created[:4] or _identity(linked) != _identity(info)):
                raise ValueError()

        validate()
        if stat.S_IMODE(os.fstat(child_fd).st_mode) != 0o700:
            raise ValueError()
        os.fsync(child_fd)
        os.fsync(parent_fd)
        validate()
        yield parent_fd, child_fd, validate


def _write(parent_fd: int, child_fd: int, fact_id: UUID, raw: bytes, expires_at: float) -> None:
    _deadline(expires_at)
    if not 0 < len(raw) <= MAX_EVIDENCE_BYTES:
        raise ValueError()
    with ExitStack() as stack:
        descriptor = os.open(f"{fact_id}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=child_fd)
        stack.callback(os.close, descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size != 0:
            raise ValueError()
        offset = 0
        while offset < len(raw):
            _deadline(expires_at)
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise ValueError()
            offset += written
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    os.fsync(child_fd)
    os.fchmod(child_fd, 0o500)
    os.fsync(child_fd)
    os.fsync(parent_fd)
    _deadline(expires_at)


def _source(request: RestoreReviewRequest, plan: RestoreReviewCapturePlan, source: SourceRequest) -> PropagationDeletionFact:
    if isinstance(source, RestoreKnowledgeRequest):
        fact = source.propagation.fact
        ledger = source.propagation.ledger_id, source.propagation.ledger_generation
        category = "knowledge"
    elif isinstance(source, RestoreCredentialRequest):
        fact = source.fact
        ledger = source.ledger_id, source.ledger_generation
        category = "credentials"
    elif isinstance(source, RestoreSessionRequest):
        # 沿原fact_v1重新验证兼容的封闭模型，不重签会话事实或改变其+00:00摘要。
        fact = PropagationDeletionFact.model_validate_json(source.fact.model_dump_json())
        ledger = source.ledger_id, source.ledger_generation
        category = "auth_sessions"
    else:
        raise ValueError()
    source_digest = restore_session_digest(source) if isinstance(source, RestoreSessionRequest) else batch_digest(source)
    if (source_digest != plan.source_request_digest or category != plan.category
        or fact.id != plan.fact_id or fact.record_digest != plan.fact_digest
        or not any(original.id == fact.id and original.record_digest == fact.record_digest for original in request.facts)
        or (source.restore_id, source.context_digest, source.backup_id, source.target_alias,
            *ledger, source.ledger_snapshot_digest) != (request.restore_id, request.context_digest, request.backup_id,
            request.target_alias, request.ledger_id, request.ledger_generation, request.ledger_snapshot_digest)):
        raise ValueError()
    return fact


def _latest(source: SourceRequest, receipt: SourceReceipt, history: tuple[SourceAuditRecord, ...]) -> str:
    if not history or len(history) > 8:
        raise ValueError()
    last = history[-1]
    audit = last.audit
    if isinstance(source, RestoreSessionRequest):
        # 原事件不含回执；必须是紧随本次verify原事件追加的完整回执记录。
        if (not isinstance(receipt, RestoreSessionReceipt) or not isinstance(last, RestoreSessionReceiptRecord)
            or len(history) < 2 or type(history[-2]) is not RestoreSessionAuditRecord):
            raise ValueError()
        previous = history[-2]
        expected = "target_not_observed" if receipt.status == "not_observed" else "quarantine_verified"
        if (last.tenant_id != source.fact.tenant_id or last.subject_user_id != source.fact.subject_user_id
            or audit.event != expected or previous.audit.event != expected
            or last.source_event_id != previous.audit.event_id or last.previous_digest != restore_session_digest(previous)
            or audit.restore_id != source.restore_id or audit.request_id != source.request_id
            or audit.request_digest != restore_session_digest(source) or receipt.request_digest != audit.request_digest
            or receipt.request_id != source.request_id or audit.fact_digest != source.fact.record_digest
            or audit.resource_key_digest != source.fact.resource_key_digest
            or restore_session_digest(last.receipt) != restore_session_digest(receipt)
            or not receipt.observed_at <= previous.audit.observed_at <= previous.recorded_at
                <= audit.observed_at <= last.recorded_at <= datetime.now(UTC)):
            raise ValueError()
        return restore_session_digest(last)
    fact = source.propagation.fact if isinstance(source, RestoreKnowledgeRequest) else source.fact
    expected = {"quarantined": "quarantine_verified", "not_observed": "target_not_observed",
        "needs_attention": "quarantine_not_confirmed"}[receipt.status]
    if (last.tenant_id != fact.tenant_id or last.subject_user_id != fact.subject_user_id
        or audit.event != expected or audit.restore_id != source.restore_id or audit.request_id != source.request_id
        or audit.request_digest != batch_digest(source) or receipt.request_digest != audit.request_digest
        or receipt.request_id != source.request_id or audit.fact_digest != fact.record_digest
        or audit.resource_key_digest != fact.resource_key_digest
        or audit.observation_digest != receipt.observation_digest or audit.evidence_digest != receipt.evidence_digest
        or not receipt.observed_at <= audit.observed_at <= last.recorded_at <= datetime.now(UTC)):
        raise ValueError()
    return batch_digest(last)


async def _capture(request: RestoreReviewRequest, plan: RestoreReviewCapturePlan, source: SourceRequest, *,
    authority: RestoreReviewCaptureAuthority, verify: Callable[[], Awaitable[SourceReceipt]],
    history: Callable[[], Awaitable[tuple[SourceAuditRecord, ...]]],
) -> RestoreReviewCaptureReceipt:
    try:
        if (not isinstance(request, RestoreReviewRequest) or not isinstance(plan, RestoreReviewCapturePlan)
            or authority is None or authority.authority_id != request.authority_id
            or len(request.model_dump_json().encode("utf-8")) > MAX_INPUT_BYTES
            or batch_digest(request) != plan.review_request_digest
            or not any(reader.category == plan.category and reader.reader_id == plan.reader_id for reader in request.readers)):
            raise ValueError()
        fact = _source(request, plan, source)

        async def current(evidence: CapturedEvidence | None = None) -> None:
            _context(request)
            if os.geteuid() != plan.expected_uid or authority.authority_id != request.authority_id:
                raise ValueError()
            async with asyncio.timeout(0.5):
                await authority.assert_current(request)
                await authority.assert_capture_allowed(request, plan, source)
                if evidence is not None:
                    await authority.assert_evidence_current(request, fact, evidence)
                    await authority.assert_publication_allowed(request, plan, evidence)
            _context(request)
            if os.geteuid() != plan.expected_uid or authority.authority_id != request.authority_id:
                raise ValueError()

        expires_at = time.monotonic() + 15
        async with asyncio.timeout(15):
            await current()
            with _directory(plan, expires_at) as (parent_fd, child_fd, validate):
                # 先持久占用一次性目录，再调用固定只读复核；同plan重试在此之前即拒绝。
                receipt = await verify()
                digest = _latest(source, receipt, await history())
                if isinstance(source, RestoreKnowledgeRequest) and isinstance(receipt, RestoreKnowledgeReceipt):
                    evidence: CapturedEvidence = KnowledgeRestoreReviewEvidence(reader_id=plan.reader_id,
                        audit_record_digest=digest, request=source, receipt=receipt)
                elif isinstance(source, RestoreCredentialRequest) and isinstance(receipt, RestoreCredentialReceipt):
                    evidence = CredentialRestoreReviewEvidence(reader_id=plan.reader_id,
                        audit_record_digest=digest, request=source, receipt=receipt)
                elif isinstance(source, RestoreSessionRequest) and isinstance(receipt, RestoreSessionReceipt):
                    evidence = SessionRestoreReviewEvidence(reader_id=plan.reader_id,
                        audit_record_digest=digest, request=source, receipt=receipt)
                else:
                    raise ValueError()
                _packet(request, fact, plan.reader_id, evidence)
                await current(evidence)
                validate()
                raw = evidence.model_dump_json().encode("utf-8")
                sha256 = hashlib.sha256(raw).hexdigest()
                scope = RestoreReviewArtifactScope(review_request_digest=plan.review_request_digest,
                    reader_id=plan.reader_id, category=plan.category, directory=plan.directory, expected_uid=plan.expected_uid,
                    bindings=(RestoreReviewArtifactBinding(fact_id=fact.id, fact_digest=fact.record_digest, artifact_sha256=sha256),))
                _write(parent_fd, child_fd, fact.id, raw, expires_at)
                validate()
                saved, fingerprint = _read(scope, fact.id, expires_at)
                if saved != raw or _latest(source, receipt, await history()) != digest:
                    raise ValueError()
                await current(evidence)
                validate()
                repeated, after = _read(scope, fact.id, expires_at)
                if saved != repeated or fingerprint != after:
                    raise ValueError()
                _packet(request, fact, plan.reader_id, evidence)
                _context(request)
                result = RestoreReviewCaptureReceipt(publication_id=plan.publication_id,
                    review_request_digest=plan.review_request_digest, source_request_digest=plan.source_request_digest,
                    source_audit_record_digest=digest, source_observed_at=receipt.observed_at,
                    artifact_sha256=sha256, artifact_scope=scope, published_at=datetime.now(UTC))
        _context(request)
        _packet(request, fact, plan.reader_id, evidence)
        _deadline(expires_at)
        return result
    except Exception:
        raise RestoreReviewCaptureUnconfirmed() from None


async def capture_knowledge_restore_review_artifact(verification_engine: AsyncEngine, source: RestoreKnowledgeRequest,
    request: RestoreReviewRequest, plan: RestoreReviewCapturePlan, *, source_authority: RestoreKnowledgeAuthority,
    audit_sink: PostgresRestoreKnowledgeAuditSink, history_authority: RestoreKnowledgeHistoryAuthority,
    authority: RestoreReviewCaptureAuthority,
) -> RestoreReviewCaptureReceipt:
    """只调用固定知识只读复核；不接收外部手填回执或隔离写引擎。"""
    try:
        if (not isinstance(source, RestoreKnowledgeRequest) or not isinstance(audit_sink, PostgresRestoreKnowledgeAuditSink)
            or not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or source_authority is None or source_authority.authority_id != source.propagation.authority_id
            or history_authority is None):
            raise ValueError()

        async def verify() -> SourceReceipt:
            return await verify_restored_knowledge_quarantine(verification_engine, source,
                authority=source_authority, audit_sink=audit_sink)

        async def history() -> tuple[SourceAuditRecord, ...]:
            return await audit_sink.read_history(authority=history_authority)

        return await _capture(request, plan, source, authority=authority, verify=verify, history=history)
    except Exception:
        raise RestoreReviewCaptureUnconfirmed() from None


async def capture_credential_restore_review_artifact(verification_engine: AsyncEngine, source: RestoreCredentialRequest,
    request: RestoreReviewRequest, plan: RestoreReviewCapturePlan, *, source_authority: RestoreCredentialAuthority,
    audit_sink: PostgresRestoreCredentialAuditSink, history_authority: RestoreCredentialHistoryAuthority,
    authority: RestoreReviewCaptureAuthority,
) -> RestoreReviewCaptureReceipt:
    """只调用固定凭据修订只读复核；不解封秘密、不重施隔离或接收外部回执。"""
    try:
        if (not isinstance(source, RestoreCredentialRequest) or not isinstance(audit_sink, PostgresRestoreCredentialAuditSink)
            or not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or source_authority is None or source_authority.authority_id != source.authority_id
            or history_authority is None):
            raise ValueError()

        async def verify() -> SourceReceipt:
            return await verify_restored_credential_quarantine(verification_engine, source,
                authority=source_authority, audit_sink=audit_sink)

        async def history() -> tuple[SourceAuditRecord, ...]:
            return await audit_sink.read_history(authority=history_authority)

        return await _capture(request, plan, source, authority=authority, verify=verify, history=history)
    except Exception:
        raise RestoreReviewCaptureUnconfirmed() from None


async def capture_session_restore_review_artifact(verification_engine: AsyncEngine, source: RestoreSessionRequest,
    request: RestoreReviewRequest, plan: RestoreReviewCapturePlan, *, source_authority: RestoreSessionAuthority,
    audit_sink: PostgresRestoreSessionAuditSink, history_authority: RestoreSessionHistoryAuthority,
    authority: RestoreReviewCaptureAuthority,
) -> RestoreReviewCaptureReceipt:
    """从固定会话只读verify捕获原回执，绑定该次原事件，不接写引擎或外部回执。"""
    try:
        if (not isinstance(source, RestoreSessionRequest) or not isinstance(audit_sink, PostgresRestoreSessionAuditSink)
            or not isinstance(verification_engine, AsyncEngine) or verification_engine.dialect.name != "postgresql"
            or source_authority is None or source_authority.authority_id != source.authority_id
            or history_authority is None):
            raise ValueError()

        class CaptureAudit:
            event: RestoreSessionAudit | None = None

            async def record(self, event: RestoreSessionAudit) -> None:
                if self.event is not None or event.event not in {"quarantine_verified", "target_not_observed"}:
                    raise ValueError()
                await audit_sink.record(event)
                self.event = event

        async def capture_current() -> None:
            _context(request)
            if os.geteuid() != plan.expected_uid or authority.authority_id != request.authority_id:
                raise ValueError()
            async with asyncio.timeout(0.5):
                await authority.assert_current(request)
                await authority.assert_capture_allowed(request, plan, source)
            _context(request)
            if os.geteuid() != plan.expected_uid or authority.authority_id != request.authority_id:
                raise ValueError()

        async def verify() -> SourceReceipt:
            await capture_current()
            await audit_sink.require_receipt_capacity(authority=history_authority)
            capture = CaptureAudit()
            receipt = await verify_restored_session_quarantine(verification_engine, source,
                authority=source_authority, audit_sink=capture)
            if capture.event is None:
                raise ValueError()
            # 原事件已独立提交，保留原回执需另一次追加；未知/并发变化不重放。
            await capture_current()
            await audit_sink._record_receipt(receipt, source_event_id=capture.event.event_id)
            return receipt

        async def history() -> tuple[SourceAuditRecord, ...]:
            return await audit_sink.read_history(authority=history_authority)

        return await _capture(request, plan, source, authority=authority, verify=verify, history=history)
    except Exception:
        raise RestoreReviewCaptureUnconfirmed() from None
