"""知识子任务原物理调用的可信用量结算；终态/撤权不免除原消费。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.model_routing_accounting_contracts import RoutingExecutionUsageReceipt, routing_usage_cost
from supportops_core.models import ModelCallLedger, RunAttempt, RunEvent, Tenant
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import lock_run_scope
from supportops_core.specialist_accounting_contracts import (
    SpecialistAccountingError, SpecialistAccountingRecord, SpecialistAccountingView,
)
from supportops_core.specialist_budget_contracts import SpecialistBudgetReservation
from supportops_core.specialist_budget_services import SpecialistBudgetDenied, read_specialist_budget_reservation
from supportops_core.specialist_context_services import _utc
from supportops_core.specialist_dispatch_contracts import DISPATCH_CONSUMED_ERROR, SpecialistDispatchConsumption
from supportops_core.specialist_dispatch_history import read_specialist_dispatch_history
from supportops_core.services import append_event


class SpecialistAccountingUnconfirmed(RuntimeError):
    code = "SPECIALIST_ACCOUNTING_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("子任务用量记账暂不可确认，请只读查询原call_id，勿重派或退额")


class SpecialistUsageReader(Protocol):
    """默认缺失；必须从可信持久一次派发及原供应商回执核对真实物理调用。

    复用现有封闭physical usage回执Schema和精确USD计费函数，不接入模型自报用量。
    必须绑定原call/task/attempt/generation/reservation/model_input摘要、固定实际模型与凭据
    revision，证实无隐式重试或合并调用。dispatch ID/证据摘要和started_at终身固定；完整
    usage只来自已结束原调用，缺失/部分usage返回None，不由正文长度或预留估算。
    无真实调用观察则返回None而非伪造失败/零usage。不得取密、联网、重放或提交/替换事务。
    """

    async def read_usage(self, session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    ) -> RoutingExecutionUsageReceipt | None: ...


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


def _check_receipt(receipt: RoutingExecutionUsageReceipt, reservation: SpecialistBudgetReservation,
    consumption: SpecialistDispatchConsumption | None,
) -> None:
    if (consumption is None or receipt.dispatch_id != consumption.record.claim.dispatch_id
        or receipt.dispatch_evidence_digest != _digest(consumption) or receipt.started_at < consumption.consumed_at):
        raise SpecialistBudgetDenied("SPECIALIST_USAGE_DISPATCH_MISMATCH")
    if (receipt.tenant_id != reservation.tenant_id or receipt.call_id != reservation.call_id
        or receipt.run_id != reservation.run_id or receipt.attempt_id != reservation.attempt_id
        or receipt.generation != reservation.generation or receipt.reservation_digest != _digest(reservation)
        or receipt.actual_model_version_id != reservation.child_version.model_version_id
        or receipt.credential_revision != reservation.credential_revision
        or receipt.model_input_digest != reservation.model_input_digest
        or receipt.started_at < reservation.recorded_at or receipt.observed_at > datetime.now(UTC)):
        raise SpecialistBudgetDenied("SPECIALIST_USAGE_SCOPE_MISMATCH")


def _same_dispatch(before: RoutingExecutionUsageReceipt, after: RoutingExecutionUsageReceipt) -> bool:
    return (before.dispatch_id == after.dispatch_id and before.dispatch_evidence_digest == after.dispatch_evidence_digest
        and before.started_at == after.started_at)


def _amounts(reservation: SpecialistBudgetReservation, receipt: RoutingExecutionUsageReceipt,
    consumption: SpecialistDispatchConsumption,
) -> tuple[Literal["unknown", "settled"], int | None, bool, SpecialistAccountingError | None]:
    usage = receipt.usage
    expires_at = min(reservation.expires_at, consumption.expires_at)
    if usage is None:
        if receipt.started_at >= expires_at:
            return "unknown", None, True, "SPECIALIST_USAGE_BOUND_EXCEEDED"
        return "unknown", None, False, "SPECIALIST_MODEL_USAGE_UNKNOWN"
    try:
        cost = routing_usage_cost(reservation.price_snapshot, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    except ValueError:
        return "unknown", None, True, "SPECIALIST_USAGE_COST_OUT_OF_RANGE"
    # 过期后实际发生的原消费仍须记账，同时冻结后续调用；不能因没有许可而抹掉费用。
    exceeded = (receipt.started_at >= expires_at or usage.input_tokens > reservation.reserved_input_tokens
        or usage.output_tokens > reservation.reserved_output_tokens or cost > reservation.reserved_cost_microusd)
    return "settled", cost, exceeded, "SPECIALIST_USAGE_BOUND_EXCEEDED" if exceeded else None


async def _closed_before_consumption(session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    ledger: ModelCallLedger,
) -> bool:
    """只识别原尝试收尾造成的未知状态；不补造派发、用量或退款。"""
    if (ledger.status != "unknown" or not ledger.error_code or ledger.error_code == DISPATCH_CONSUMED_ERROR
        or ledger.finished_at is None
        or not reservation.recorded_at <= _utc(ledger.finished_at) <= datetime.now(UTC)):
        return False
    attempt = await session.scalar(select(RunAttempt).where(RunAttempt.id == reservation.attempt_id,
        RunAttempt.tenant_id == reservation.tenant_id, RunAttempt.run_id == reservation.run_id,
        RunAttempt.generation == reservation.generation).execution_options(populate_existing=True))
    if (attempt is None or attempt.status not in {"failed", "cancelled", "expired", "quarantined"}
        or attempt.finished_at is None
        or not reservation.recorded_at <= _utc(attempt.finished_at) <= datetime.now(UTC)):
        return False
    if ledger.error_code == "MANUAL_EXPIRED_ATTEMPT_RECOVERY":
        event = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == reservation.tenant_id,
            RunEvent.run_id == reservation.run_id, RunEvent.attempt_id == reservation.attempt_id,
            RunEvent.event_type.in_(("run.cancelled", "run.recovering")),
            RunEvent.data["reason"].as_string() == "MANUAL_EXPIRED_ATTEMPT_RECOVERY")
            .order_by(RunEvent.sequence.desc()).limit(1))
        return event is not None and attempt.status in {"expired", "cancelled"}
    return (attempt.error_code == ledger.error_code
        or ledger.error_code == "RUN_CANCELLED" and attempt.status == "cancelled")


async def _history(session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    ledger: ModelCallLedger,
) -> SpecialistAccountingRecord | None:
    dispatch = await read_specialist_dispatch_history(session, reservation=reservation)
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == reservation.tenant_id,
        RunEvent.run_id == reservation.run_id, RunEvent.attempt_id == reservation.attempt_id,
        RunEvent.event_type == "budget.model_specialist_accounted",
        RunEvent.data["call_id"].as_string() == str(reservation.call_id)).order_by(RunEvent.sequence).limit(3))).all()
    previous: SpecialistAccountingRecord | None = None
    try:
        if len(events) > 2:
            raise ValueError()
        for event in events:
            current = SpecialistAccountingRecord.model_validate_json(json.dumps(event.data["specialist_accounting"],
                ensure_ascii=False, sort_keys=True, allow_nan=False))
            _check_receipt(current.receipt, reservation, dispatch.consumption)
            if dispatch.consumption is None:
                raise ValueError()
            if (event.data.get("specialist_accounting_digest") != _digest(current)
                or current.reservation_digest != _digest(reservation) or current.receipt_digest != _digest(current.receipt)
                or current.previous_record_digest != (_digest(previous) if previous else None)
                or current.recorded_at > datetime.now(UTC)
                or (current.status, current.settled_cost_microusd, current.reservation_exceeded, current.error_code)
                    != _amounts(reservation, current.receipt, dispatch.consumption)):
                raise ValueError()
            if previous is not None and (previous.receipt.usage is not None or current.receipt.usage is None
                or not _same_dispatch(previous.receipt, current.receipt)
                or previous.receipt.observation_id == current.receipt.observation_id
                or previous.recorded_at > current.recorded_at or previous.receipt.observed_at > current.receipt.observed_at):
                raise ValueError()
            previous = current
        usage = previous.receipt.usage if previous else None
        unaccounted_status = "unknown" if dispatch.consumption else "reserved"
        unaccounted_error = DISPATCH_CONSUMED_ERROR if dispatch.consumption else None
        unaccounted_at = dispatch.consumption.consumed_at if dispatch.consumption else None
        if previous is None and dispatch.consumption is None and await _closed_before_consumption(
            session, reservation=reservation, ledger=ledger,
        ):
            unaccounted_status, unaccounted_error, unaccounted_at = "unknown", ledger.error_code, _utc(ledger.finished_at)
        if (ledger.status != (previous.status if previous else unaccounted_status)
            or ledger.settled_cost_microusd != (previous.settled_cost_microusd if previous else None)
            or (_utc(ledger.finished_at) if ledger.finished_at else None) != (previous.recorded_at if previous else unaccounted_at)
            or ledger.error_code != (previous.error_code if previous else unaccounted_error)
            or any(getattr(ledger, key) != (getattr(usage, key) if usage else None)
                for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"))):
            raise ValueError()
        return previous
    except (KeyError, TypeError, ValueError, ValidationError):
        raise SpecialistBudgetDenied("SPECIALIST_ACCOUNTING_HISTORY_INVALID") from None


def _reuse_accounting(*, reservation: SpecialistBudgetReservation, consumption: SpecialistDispatchConsumption,
    previous: SpecialistAccountingRecord | None, receipt: RoutingExecutionUsageReceipt,
) -> SpecialistAccountingRecord | None:
    _check_receipt(receipt, reservation, consumption)
    if previous is not None:
        if previous.receipt.usage is not None:
            if _digest(receipt) != previous.receipt_digest:
                raise SpecialistBudgetDenied("SPECIALIST_USAGE_OBSERVATION_CONFLICT")
            return previous
        if (not _same_dispatch(previous.receipt, receipt) or receipt.observed_at < previous.receipt.observed_at
            or receipt.observation_id == previous.receipt.observation_id and _digest(receipt) != previous.receipt_digest):
            raise SpecialistBudgetDenied("SPECIALIST_USAGE_OBSERVATION_CONFLICT")
        if receipt.usage is None:
            return previous
    return None


async def append_specialist_accounting(session: AsyncSession, *, reservation: SpecialistBudgetReservation,
    consumption: SpecialistDispatchConsumption, ledger: ModelCallLedger,
    previous: SpecialistAccountingRecord | None, receipt: RoutingExecutionUsageReceipt,
) -> SpecialistAccountingRecord:
    """内部调用者先持有原Run/租户/账本锁并核对历史和可信回执；不提交事务。"""
    reused = _reuse_accounting(reservation=reservation, consumption=consumption, previous=previous, receipt=receipt)
    if reused is not None:
        return reused
    status, cost, exceeded, error = _amounts(reservation, receipt, consumption)
    result = SpecialistAccountingRecord(reservation_digest=_digest(reservation),
        previous_record_digest=_digest(previous) if previous else None,
        receipt=receipt, receipt_digest=_digest(receipt), status=status, settled_cost_microusd=cost,
        reservation_exceeded=exceeded, error_code=error, recorded_at=datetime.now(UTC))
    ledger.status, ledger.settled_cost_microusd = status, cost
    ledger.error_code, ledger.finished_at = error, result.recorded_at
    for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"):
        setattr(ledger, key, getattr(receipt.usage, key) if receipt.usage else None)
    await append_event(session, run_id=reservation.run_id, attempt_id=reservation.attempt_id,
        event_type="budget.model_specialist_accounted", data={"call_id": str(reservation.call_id),
            "specialist_accounting": result.model_dump(mode="json"), "specialist_accounting_digest": _digest(result)})
    return result


async def read_specialist_accounting(session: AsyncSession, *, tenant_id: UUID, run_id: UUID,
    call_id: UUID,
) -> SpecialistAccountingView | None:
    """受信任调用者先授权并提供一致只读快照；无accounting不表示仍可派发或零消费。"""
    reservation = await read_specialist_budget_reservation(session, tenant_id=tenant_id, run_id=run_id, call_id=call_id)
    if reservation is None:
        return None
    ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == call_id,
        ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.run_id == run_id).execution_options(populate_existing=True))
    if ledger is None:
        raise SpecialistBudgetDenied("SPECIALIST_ACCOUNTING_HISTORY_INVALID")
    accounting = await _history(session, reservation=reservation, ledger=ledger)
    return SpecialistAccountingView(reservation=reservation, accounting=accounting,
        accounting_digest=_digest(accounting) if accounting else None)


async def settle_specialist_model_call(session_factory: async_sessionmaker[AsyncSession], *, tenant_id: UUID,
    run_id: UUID, call_id: UUID, usage_reader: SpecialistUsageReader | None = None,
) -> SpecialistAccountingView:
    if usage_reader is None:
        raise SpecialistBudgetDenied("SPECIALIST_USAGE_READER_UNAVAILABLE")
    wrote = False
    try:
        async with asyncio.timeout(10):
            async with session_factory() as session, session.begin():
                if session.get_bind().dialect.name != "postgresql":
                    raise SpecialistBudgetDenied("SPECIALIST_POSTGRES_REQUIRED")
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                await lock_run_scope(session, run_id, tenant_id=tenant_id)
                await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(key_share=True))
                ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == call_id,
                    ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.run_id == run_id)
                    .execution_options(populate_existing=True).with_for_update())
                reservation = await read_specialist_budget_reservation(session, tenant_id=tenant_id, run_id=run_id, call_id=call_id)
                if ledger is None or reservation is None:
                    raise SpecialistBudgetDenied("SPECIALIST_RESERVATION_NOT_FOUND")
                previous = await _history(session, reservation=reservation, ledger=ledger)
                if previous is not None and previous.receipt.usage is not None:
                    result = previous
                else:
                    dispatch = await read_specialist_dispatch_history(session, reservation=reservation)
                    if dispatch.consumption is None:
                        raise SpecialistBudgetDenied("SPECIALIST_USAGE_DISPATCH_MISSING")
                    transaction = session.get_transaction()
                    started = asyncio.get_running_loop().time()
                    reader_reservation = SpecialistBudgetReservation.model_validate_json(reservation.model_dump_json())
                    async with asyncio.timeout(2):
                        receipt = await usage_reader.read_usage(session, reservation=reader_reservation)
                    if (asyncio.get_running_loop().time() - started >= 2 or transaction is None
                        or session.get_transaction() is not transaction or not transaction.is_active
                        or _digest(reader_reservation) != _digest(reservation)):
                        raise SpecialistBudgetDenied("SPECIALIST_USAGE_TRANSACTION_UNCONFIRMED")
                    if not isinstance(receipt, RoutingExecutionUsageReceipt):
                        raise SpecialistAccountingUnconfirmed()
                    receipt = RoutingExecutionUsageReceipt.model_validate_json(receipt.model_dump_json())
                    reused = _reuse_accounting(reservation=reservation, consumption=dispatch.consumption,
                        previous=previous, receipt=receipt)
                    if reused is not None:
                        result = reused
                    else:
                        wrote = True
                        result = await append_specialist_accounting(session, reservation=reservation,
                            consumption=dispatch.consumption, ledger=ledger, previous=previous, receipt=receipt)
                view = SpecialistAccountingView(reservation=reservation, accounting=result, accounting_digest=_digest(result))
            return view
    except BaseException as exc:
        if wrote or isinstance(exc, Exception) and not isinstance(exc, SpecialistBudgetDenied):
            raise SpecialistAccountingUnconfirmed() from None
        raise
