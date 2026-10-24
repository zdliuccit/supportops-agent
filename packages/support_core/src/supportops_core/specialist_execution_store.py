"""原一次物理回执的PG捕获和费用/回答读取；没有SDK、HTTP上传或默认存储批准。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SupportAnswer
from supportops_core.config import Settings
from supportops_core.model_routing_accounting_contracts import RoutingExecutionUsageReceipt
from supportops_core.models import Conversation, ModelCallLedger, RunAttempt, SpecialistExecutionObservation, Tenant
from supportops_core.redaction import redact_sensitive_text
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import RunAttemptIdentity, lock_run_scope, owned_run
from supportops_core.specialist_accounting_contracts import SpecialistAccountingRecord
from supportops_core.specialist_accounting_services import _check_receipt, _history as accounting_history, append_specialist_accounting
from supportops_core.specialist_budget_contracts import SpecialistBudgetReservation
from supportops_core.specialist_budget_services import read_specialist_budget_reservation
from supportops_core.specialist_context_services import _utc
from supportops_core.specialist_dispatch_contracts import SpecialistDispatchConsumption
from supportops_core.specialist_dispatch_history import read_specialist_dispatch_history
from supportops_core.specialist_execution_cipher import SpecialistResponseCipher
from supportops_core.specialist_execution_contracts import (
    SpecialistExecutionRecord, SpecialistExecutionStatus, SpecialistExecutionStoragePermit, StoragePurpose,
)
from supportops_core.specialist_execution_history import assert_specialist_execution_history
from supportops_core.specialist_request_contracts import canonical_request_json
from supportops_core.specialist_response_contracts import SPECIALIST_RESPONSE_BYTES_MAX, SpecialistProviderAnswerObservation
from supportops_core.specialist_response_services import _json_object
from supportops_core.specialist_task_contracts import contract_digest


class SpecialistExecutionStoreDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("子任务原执行记录当前不可保存或读取")
        self.code = code


class SpecialistExecutionStoreUnconfirmed(RuntimeError):
    code = "SPECIALIST_EXECUTION_CAPTURE_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("原执行记录提交暂不可确认，请只读原call_id，勿再次捕获或派发")


class SpecialistExecutionStorageAuthority(Protocol):
    """独立真实存储/读取批准，默认None；不能用调用者DTO或摘要自批。

    capture须独立核对原一次SDK派发/完整供应商回执、固定主体/数据处理与W21保留批准，
    禁止从模型自报补造物理回执。answer_until仅在当前主体/知识/数据读取仍获准时签发；
    read_usage只读历史费用，不要求恢复原业务许可；read_answer另须当前正文读取批准。
    许可须绑定原预留/消费/完整回执及原政策，当前撤销/法律保留/恢复隔离必须复检。
    不取密/联网、提交或替换事务；当前没有默认企业批准提供者。
    """

    async def authorize(self, session: AsyncSession, *, purpose: StoragePurpose,
        reservation: SpecialistBudgetReservation, consumption: SpecialistDispatchConsumption,
        receipt: RoutingExecutionUsageReceipt,
    ) -> SpecialistExecutionStoragePermit: ...


async def _permit(session: AsyncSession, *, authority: SpecialistExecutionStorageAuthority,
    purpose: StoragePurpose, reservation: SpecialistBudgetReservation, consumption: SpecialistDispatchConsumption,
    receipt: RoutingExecutionUsageReceipt,
) -> SpecialistExecutionStoragePermit:
    copies = (SpecialistBudgetReservation.model_validate_json(reservation.model_dump_json()),
        SpecialistDispatchConsumption.model_validate_json(consumption.model_dump_json()),
        RoutingExecutionUsageReceipt.model_validate_json(receipt.model_dump_json()))
    before = tuple(manifest_digest(item.model_dump(mode="json")) for item in copies)
    transaction, since = session.get_transaction(), datetime.now(UTC)
    loop = asyncio.get_running_loop()
    cutoff = loop.time() + 1
    try:
        async with asyncio.timeout_at(cutoff):
            result = await authority.authorize(session, purpose=purpose, reservation=copies[0], consumption=copies[1], receipt=copies[2])
    except asyncio.CancelledError:
        raise
    except Exception:
        raise SpecialistExecutionStoreDenied("SPECIALIST_STORAGE_APPROVAL_UNCONFIRMED") from None
    if (loop.time() >= cutoff or transaction is None or session.get_transaction() is not transaction or not transaction.is_active
        or tuple(manifest_digest(item.model_dump(mode="json")) for item in copies) != before or not isinstance(result, SpecialistExecutionStoragePermit)):
        raise SpecialistExecutionStoreDenied("SPECIALIST_STORAGE_APPROVAL_UNCONFIRMED")
    result = SpecialistExecutionStoragePermit.model_validate_json(result.model_dump_json())
    if (result.purpose != purpose or result.reservation_digest != contract_digest(reservation)
        or result.consumption_digest != contract_digest(consumption) or result.receipt_digest != manifest_digest(receipt.model_dump(mode="json"))
        or not since <= result.checked_at <= datetime.now(UTC) < result.expires_at):
        raise SpecialistExecutionStoreDenied("SPECIALIST_STORAGE_APPROVAL_UNCONFIRMED")
    return result


def _body_scope(record: SpecialistExecutionRecord) -> str:
    return manifest_digest(record.model_dump(mode="json", exclude={"ciphertext_digest"}))


async def _history(session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    consumption: SpecialistDispatchConsumption,
) -> SpecialistExecutionRecord | None:
    await assert_specialist_execution_history(session)
    # 费用/状态只查元数据，不加载sealed_answer；回答经单独许可后按摘要再取。
    row = (await session.execute(select(SpecialistExecutionObservation.record_digest,
        SpecialistExecutionObservation.payload, SpecialistExecutionObservation.created_at,
        SpecialistExecutionObservation.attempt_id, SpecialistExecutionObservation.generation).where(
        SpecialistExecutionObservation.call_id == reservation.call_id,
        SpecialistExecutionObservation.tenant_id == reservation.tenant_id,
        SpecialistExecutionObservation.run_id == reservation.run_id))).one_or_none()
    if row is None:
        return None
    try:
        record = SpecialistExecutionRecord.model_validate_json(json.dumps(row.payload,
            ensure_ascii=False, sort_keys=True, allow_nan=False))
        _check_receipt(record.receipt, reservation, consumption)
        if (row.record_digest != contract_digest(record) or record.call_id != reservation.call_id
            or record.tenant_id != reservation.tenant_id or record.conversation_id != reservation.conversation_id
            or record.run_id != reservation.run_id or record.attempt_id != reservation.attempt_id
            or record.generation != reservation.generation or row.attempt_id != record.attempt_id or row.generation != record.generation
            or _utc(row.created_at) != record.recorded_at or record.recorded_at > datetime.now(UTC)
            or record.reservation_digest != contract_digest(reservation) or record.consumption_digest != contract_digest(consumption)
            or record.prepared_request_digest != consumption.record.prepared_request_digest
            or record.answer_until is not None and record.answer_until > consumption.expires_at):
            raise ValueError()
        return record
    except (TypeError, ValueError):
        raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_HISTORY_INVALID") from None


def _seal_answer(record: SpecialistExecutionRecord, *, answer_json: str, cipher: SpecialistResponseCipher,
    answer_until: datetime, secrets: tuple[str, ...],
) -> tuple[SpecialistExecutionRecord, str]:
    if len(answer_json) > SPECIALIST_RESPONSE_BYTES_MAX:
        raise ValueError()
    _json_object(answer_json, bytes_limit=SPECIALIST_RESPONSE_BYTES_MAX)
    answer = SupportAnswer.model_validate_json(answer_json, strict=True)
    before = answer.model_dump(mode="json")
    after = answer.model_dump(mode="json")
    after["answer"] = redact_sensitive_text(answer.answer, secrets=secrets)
    after["summary"] = redact_sensitive_text(answer.summary, secrets=secrets) if answer.summary is not None else None
    after["suggested_actions"] = [redact_sensitive_text(item, secrets=secrets) for item in answer.suggested_actions]
    for citation in after["citations"]:
        citation["title"] = redact_sensitive_text(citation["title"], secrets=secrets)
        # 链接不属于当前只读协议；不密封后再当作可信出处。
        if citation["url"] is not None:
            raise ValueError()
        if citation["evidence_id"] is not None and redact_sensitive_text(citation["evidence_id"], secrets=secrets) != citation["evidence_id"]:
            raise ValueError()
    for conflict in after["evidence_conflicts"]:
        conflict["reason"] = redact_sensitive_text(conflict["reason"], secrets=secrets)
        if any(redact_sensitive_text(item, secrets=secrets) != item for item in conflict["evidence_ids"]):
            raise ValueError()
    sanitized = SupportAnswer.model_validate_json(canonical_request_json(after), strict=True)
    body = canonical_request_json(sanitized.model_dump(mode="json"))
    if len(body.encode("utf-8")) > SPECIALIST_RESPONSE_BYTES_MAX:
        raise ValueError()
    candidate = SpecialistExecutionRecord.model_validate_json(canonical_request_json({**record.model_dump(mode="json"),
        "answer_status": "sealed_redacted", "original_answer_digest": hashlib.sha256(answer_json.encode("utf-8")).hexdigest(),
        "answer_digest": hashlib.sha256(body.encode("utf-8")).hexdigest(), "answer_was_redacted": before != after,
        "answer_until": answer_until.isoformat(), "key_id": str(cipher.key_id), "ciphertext_digest": "0" * 64}))
    sealed = cipher.seal(tenant_id=record.tenant_id, scope_digest=_body_scope(candidate), answer_json=body)
    final = SpecialistExecutionRecord.model_validate_json(canonical_request_json({**candidate.model_dump(mode="json"),
        "ciphertext_digest": hashlib.sha256(sealed.encode("ascii")).hexdigest()}))
    return final, sealed


async def capture_specialist_execution(factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity,
    receipt: RoutingExecutionUsageReceipt, settings: Settings, answer_json: str | None = None,
    authority: SpecialistExecutionStorageAuthority | None = None, cipher: SpecialistResponseCipher | None = None,
    redaction_secrets: tuple[str, ...] = (),
) -> SpecialistExecutionStatus:
    """仅可信SDK捕获者提交原完整回执；不是报告上传。未知用量保持原消费，不补造零量。

    可在父取消/旧租约结束后保存费用，但正文只有当前原尝试和独立批准仍有效时保存。
    正文不符/密封不可用仍保存完整费用及明确遗漏状态，不因无结果免除原消费。
    """
    if authority is None:
        raise SpecialistExecutionStoreDenied("SPECIALIST_STORAGE_AUTHORITY_UNAVAILABLE")
    receipt = RoutingExecutionUsageReceipt.model_validate_json(receipt.model_dump_json())
    if receipt.usage is None or receipt.finished_at is None or receipt.outcome == "unknown":
        raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_USAGE_INCOMPLETE")
    wrote = False
    try:
        async with asyncio.timeout(10):
            async with factory() as session, session.begin():
                if session.get_bind().dialect.name != "postgresql":
                    raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_POSTGRES_REQUIRED")
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                conversation, run = await lock_run_scope(session, attempt.run_id, tenant_id=attempt.tenant_id)
                original = await session.scalar(select(RunAttempt).where(RunAttempt.id == attempt.attempt_id,
                    RunAttempt.tenant_id == attempt.tenant_id, RunAttempt.run_id == attempt.run_id).with_for_update())
                if (original is None or original.owner_id != attempt.owner_id or original.generation != attempt.generation
                    or conversation.id != attempt.conversation_id):
                    raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_OWNER_MISMATCH")
                await session.scalar(select(Tenant.id).where(Tenant.id == attempt.tenant_id).with_for_update(key_share=True))
                ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == receipt.call_id,
                    ModelCallLedger.tenant_id == attempt.tenant_id, ModelCallLedger.run_id == attempt.run_id)
                    .execution_options(populate_existing=True).with_for_update())
                reservation = await read_specialist_budget_reservation(session, tenant_id=attempt.tenant_id,
                    run_id=attempt.run_id, call_id=receipt.call_id)
                if ledger is None or reservation is None or reservation.attempt_id != attempt.attempt_id or reservation.generation != attempt.generation:
                    raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_RESERVATION_MISMATCH")
                dispatch = await read_specialist_dispatch_history(session, reservation=reservation)
                consumption = dispatch.consumption
                _check_receipt(receipt, reservation, consumption)
                if consumption is None:
                    raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_CONSUMPTION_MISSING")
                if consumption.record.owner_digest != hashlib.sha256(attempt.owner_id.encode("utf-8")).hexdigest():
                    raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_OWNER_MISMATCH")
                if await _history(session, reservation=reservation, consumption=consumption) is not None:
                    raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_ALREADY_CAPTURED")
                previous = await accounting_history(session, reservation=reservation, ledger=ledger)
                permit = await _permit(session, authority=authority, purpose="capture", reservation=reservation,
                    consumption=consumption, receipt=receipt)
                now = datetime.now(UTC)
                record = SpecialistExecutionRecord(call_id=reservation.call_id, tenant_id=reservation.tenant_id,
                    conversation_id=reservation.conversation_id, subject_user_id=conversation.user_id,
                    run_id=reservation.run_id, attempt_id=reservation.attempt_id, generation=reservation.generation,
                    reservation_digest=contract_digest(reservation), consumption_digest=contract_digest(consumption),
                    prepared_request_digest=consumption.record.prepared_request_digest, storage_policy_digest=permit.policy_digest,
                    capture_permit_digest=contract_digest(permit), receipt=receipt,
                    receipt_digest=manifest_digest(receipt.model_dump(mode="json")), recorded_at=now)
                sealed: str | None = None
                if answer_json is not None:
                    answer_status = "not_permitted"
                    active = (run.current_attempt_id == attempt.attempt_id and run.status.value == "running"
                        and original.status == "running" and _utc(original.lease_expires_at) > now
                        and conversation.deleted_at is None and permit.answer_until is not None
                        and now < min(permit.answer_until, consumption.expires_at))
                    if active and permit.answer_until is not None and receipt.outcome == "completed":
                        if cipher is None:
                            answer_status = "unavailable"
                        else:
                            try:
                                record, sealed = _seal_answer(record, answer_json=answer_json, cipher=cipher,
                                    answer_until=min(permit.answer_until, consumption.expires_at),
                                    secrets=(settings.jwt_secret, settings.secret_encryption_key, *redaction_secrets))
                            except Exception:
                                answer_status = "invalid"
                    if sealed is None:
                        record = record.model_copy(update={"answer_status": answer_status})
                if record.answer_until is not None and datetime.now(UTC) >= record.answer_until:
                    record = SpecialistExecutionRecord.model_validate_json(canonical_request_json({**record.model_dump(mode="json"),
                        "answer_status": "not_permitted", "original_answer_digest": None, "answer_digest": None,
                        "answer_was_redacted": None, "answer_until": None, "key_id": None, "ciphertext_digest": None}))
                    sealed = None
                if datetime.now(UTC) >= permit.expires_at:
                    raise SpecialistExecutionStoreDenied("SPECIALIST_STORAGE_APPROVAL_EXPIRED")
                payload = record.model_dump(mode="json")
                if len(canonical_request_json(payload).encode("utf-8")) > 16384:
                    raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_METADATA_TOO_LARGE")
                session.add(SpecialistExecutionObservation(call_id=record.call_id, tenant_id=record.tenant_id,
                    run_id=record.run_id, attempt_id=record.attempt_id, generation=record.generation,
                    record_digest=contract_digest(record), payload=payload, sealed_answer=sealed, created_at=record.recorded_at))
                wrote = True
                await session.flush()
                await append_specialist_accounting(session, reservation=reservation, consumption=consumption,
                    ledger=ledger, previous=previous, receipt=receipt)
                await assert_specialist_execution_history(session)
                if datetime.now(UTC) >= permit.expires_at:
                    raise SpecialistExecutionStoreDenied("SPECIALIST_STORAGE_APPROVAL_EXPIRED")
            if datetime.now(UTC) >= permit.expires_at:
                raise SpecialistExecutionStoreUnconfirmed()
            return SpecialistExecutionStatus(record=record, record_digest=contract_digest(record))
    except BaseException as exc:
        if wrote:
            raise SpecialistExecutionStoreUnconfirmed() from None
        if isinstance(exc, Exception) and not isinstance(exc, SpecialistExecutionStoreDenied):
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_CAPTURE_UNAVAILABLE") from None
        raise


class PostgresSpecialistExecutionSource:
    """提供现有usage reader及原回答source；显式批准必需，无默认注册或数据库身份配置。"""

    def __init__(self, *, attempt: RunAttemptIdentity, authority: SpecialistExecutionStorageAuthority,
        cipher: SpecialistResponseCipher | None = None,
    ) -> None:
        if authority is None:
            raise SpecialistExecutionStoreDenied("SPECIALIST_STORAGE_AUTHORITY_UNAVAILABLE")
        self._attempt, self._authority, self._cipher = attempt, authority, cipher

    async def _read(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
        purpose: StoragePurpose,
    ) -> tuple[SpecialistExecutionRecord, SpecialistDispatchConsumption, SpecialistExecutionStoragePermit] | None:
        identity = self._attempt
        if (reservation.tenant_id != identity.tenant_id or reservation.run_id != identity.run_id
            or reservation.conversation_id != identity.conversation_id or reservation.attempt_id != identity.attempt_id
            or reservation.generation != identity.generation):
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_SCOPE_MISMATCH")
        dispatch = await read_specialist_dispatch_history(session, reservation=reservation)
        if dispatch.consumption is None:
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_CONSUMPTION_MISSING")
        record = await _history(session, reservation=reservation, consumption=dispatch.consumption)
        if record is None:
            return None
        subject = await session.scalar(select(Conversation.user_id).where(Conversation.id == record.conversation_id,
            Conversation.tenant_id == record.tenant_id).execution_options(include_deleted=True))
        if subject != record.subject_user_id:
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_SUBJECT_MISMATCH")
        permit = await _permit(session, authority=self._authority, purpose=purpose, reservation=reservation,
            consumption=dispatch.consumption, receipt=record.receipt)
        if permit.policy_digest != record.storage_policy_digest:
            raise SpecialistExecutionStoreDenied("SPECIALIST_STORAGE_POLICY_CHANGED")
        return record, dispatch.consumption, permit

    async def read_usage(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    ) -> RoutingExecutionUsageReceipt | None:
        row = await self._read(session, reservation=reservation, purpose="read_usage")
        return row[0].receipt if row else None

    async def read_status(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    ) -> SpecialistExecutionStatus | None:
        row = await self._read(session, reservation=reservation, purpose="read_status")
        return SpecialistExecutionStatus(record=row[0], record_digest=contract_digest(row[0])) if row else None

    async def read_answer(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
        consumption: SpecialistDispatchConsumption, accounting: SpecialistAccountingRecord,
    ) -> SpecialistProviderAnswerObservation | None:
        await owned_run(session, self._attempt)
        row = await self._read(session, reservation=reservation, purpose="read_answer")
        if row is None:
            return None
        record, original, permit = row
        if (original != consumption or accounting.receipt != record.receipt
            or accounting.reservation_digest != record.reservation_digest):
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_ACCOUNTING_MISMATCH")
        if record.answer_until is None:
            return None
        if record.receipt.finished_at is None:
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_HISTORY_INVALID")
        if (permit.answer_until is None or self._cipher is None or record.key_id != self._cipher.key_id
            or datetime.now(UTC) >= min(record.answer_until, permit.answer_until, permit.expires_at, consumption.expires_at)):
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_ANSWER_UNAVAILABLE")
        sealed = await session.scalar(select(SpecialistExecutionObservation.sealed_answer).where(
            SpecialistExecutionObservation.call_id == record.call_id, SpecialistExecutionObservation.tenant_id == record.tenant_id,
            SpecialistExecutionObservation.record_digest == contract_digest(record)))
        try:
            if sealed is None or hashlib.sha256(sealed.encode("ascii")).hexdigest() != record.ciphertext_digest:
                raise ValueError()
            body = self._cipher.open(tenant_id=record.tenant_id, scope_digest=_body_scope(record), sealed=sealed)
            if hashlib.sha256(body.encode("utf-8")).hexdigest() != record.answer_digest:
                raise ValueError()
        except (ValueError, UnicodeError, TypeError):
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_ANSWER_INVALID") from None
        await owned_run(session, self._attempt)
        if datetime.now(UTC) >= min(record.answer_until, permit.answer_until, permit.expires_at, consumption.expires_at):
            raise SpecialistExecutionStoreDenied("SPECIALIST_EXECUTION_ANSWER_EXPIRED")
        return SpecialistProviderAnswerObservation(observation_id=record.receipt.observation_id, call_id=record.call_id,
            reservation_digest=record.reservation_digest, consumption_digest=record.consumption_digest,
            prepared_request_digest=record.prepared_request_digest, model_input_digest=record.receipt.model_input_digest,
            answer_json=body, answer_bytes_digest=record.answer_digest,
            finished_at=record.receipt.finished_at, observed_at=datetime.now(UTC))
