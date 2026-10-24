"""按所选模型的冻结价格结算；缺可信物理调用回执源时关闭，不派发或重试模型。"""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.model_routing_accounting_contracts import (
    AccountingErrorCode, AccountingStatus,
    RoutingAccountingRecord, RoutingAccountingView, RoutingExecutionUsageReceipt,
    RoutingPriceSnapshot, routing_usage_cost,
)
from supportops_core.model_routing_policy_contracts import RoutingPolicyVersionRecord
from supportops_core.model_routing_dispatch_contracts import (
    ROUTING_DISPATCH_CONSUMED_ERROR, RoutingDispatchConsumption, RoutingDispatchView,
)
from supportops_core.model_routing_dispatch_history import read_routing_dispatch_history
from supportops_core.model_routing_reservation_contracts import RoutingReservationRecord
from supportops_core.model_routing_reservations import RoutingReservationDenied, read_routing_reservation
from supportops_core.models import ModelCallLedger, ModelRoutingPolicyVersion, RunEvent, Tenant, utc_now
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import lock_run_scope
from supportops_core.services import ResourceNotFoundError, append_event


class RoutingAccountingDenied(RoutingReservationDenied):
    def __init__(self, code: str = "ROUTING_ACCOUNTING_HISTORY_INVALID") -> None:
        super().__init__(code)


class RoutingAccountingUnconfirmed(RuntimeError):
    code = "ROUTING_ACCOUNTING_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("路由用量记账结果暂不可确认，请只读查询原调用，勿重新派发")


class RoutingExecutionUsageReader(Protocol):
    """可信执行层必须核实原一次派发及其真实回执，不接受用户/模型自报用量。

    必须从可信持久派发证据核对原call/attempt/generation、reservation摘要、实际固定模型/
    凭据revision及完整输入摘要，并确认隐式重试为0、未合并多个物理调用。
    有routing.dispatch_consumed历史时，dispatch_id绑定原领取ID，dispatch_evidence_digest
    必须是原完整消费记录规范摘要；receipt.started_at不得早于消费提交记录。历史费用
    读取不补造旧消费记录，消费本身不证明已发送。dispatch_evidence_digest不随usage观察改变；observation_id固定一份
    不可变观察。usage只返回该物理调用供应商明确报告的完整总量，缺失须为None；不得由
    文本长度/预留/部分流式chunk估算。缓存/推理为总量子集，不能重复加总。
    读取不取得调用授权，不取密、不联网或重放模型，不提交/替换调用者事务。
    当前无默认提供者或Worker接线；该协议不证明已经存在真实调用。
    """

    async def read_usage(self, session: AsyncSession, *, reservation: RoutingReservationRecord
        ) -> RoutingExecutionUsageReceipt | None: ...


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


async def _pricing(session: AsyncSession, reservation: RoutingReservationRecord) -> RoutingPriceSnapshot:
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == reservation.tenant_id,
        RunEvent.run_id == reservation.run_id, RunEvent.attempt_id == reservation.attempt_id,
        RunEvent.event_type == "budget.model_reserved",
        RunEvent.data["call_id"].as_string() == str(reservation.call_id)).limit(2))).all()
    try:
        if len(events) != 1:
            raise ValueError()
        payload = events[0].data
        if "routing_pricing_snapshot" not in payload:
            raise RoutingAccountingDenied("ROUTING_PRICING_SNAPSHOT_MISSING")
        price = RoutingPriceSnapshot.model_validate(payload["routing_pricing_snapshot"])
        policy = await session.scalar(select(ModelRoutingPolicyVersion).where(
            ModelRoutingPolicyVersion.tenant_id == reservation.tenant_id,
            ModelRoutingPolicyVersion.id == reservation.policy_version_id))
        if policy is None:
            raise ValueError()
        original = RoutingPolicyVersionRecord.model_validate(policy.payload)
        candidate = next((row for row in original.definition.candidates if row.candidate_id == reservation.candidate_id), None)
        if (payload.get("routing_pricing_snapshot_digest") != _digest(price)
            or price.call_id != reservation.call_id or price.tenant_id != reservation.tenant_id
            or price.actual_model_version_id != reservation.actual_model_version_id
            or price.candidate_digest != reservation.candidate_digest
            or manifest_digest(price.source_pricing) != price.source_pricing_digest
            or _digest(original) != reservation.policy_digest or policy.policy_digest != reservation.policy_digest
            or original.version_id != reservation.policy_version_id or original.tenant_id != reservation.tenant_id
            or candidate is None or _digest(candidate) != reservation.candidate_digest
            or candidate.pricing_digest != price.source_pricing_digest
            or candidate.model_version_id != reservation.actual_model_version_id
            or candidate.credential_revision != reservation.credential_revision
            or routing_usage_cost(price, input_tokens=reservation.reserved_input_tokens,
                output_tokens=reservation.reserved_output_tokens) != reservation.reserved_cost_microusd):
            raise ValueError()
        return price
    except RoutingAccountingDenied:
        raise
    except (KeyError, TypeError, ValueError, ValidationError):
        raise RoutingAccountingDenied() from None


def _check_receipt(receipt: RoutingExecutionUsageReceipt, reservation: RoutingReservationRecord) -> None:
    if (receipt.tenant_id != reservation.tenant_id or receipt.call_id != reservation.call_id
        or receipt.run_id != reservation.run_id or receipt.attempt_id != reservation.attempt_id
        or receipt.generation != reservation.generation or receipt.reservation_digest != _digest(reservation)
        or receipt.actual_model_version_id != reservation.actual_model_version_id
        or receipt.credential_revision != reservation.credential_revision
        or receipt.model_input_digest != reservation.model_input_digest
        or receipt.started_at < reservation.recorded_at or receipt.observed_at > utc_now()):
        raise RoutingAccountingDenied("ROUTING_USAGE_SCOPE_MISMATCH")


def _same_dispatch(before: RoutingExecutionUsageReceipt, after: RoutingExecutionUsageReceipt) -> bool:
    return (before.dispatch_id == after.dispatch_id
        and before.dispatch_evidence_digest == after.dispatch_evidence_digest
        and before.started_at == after.started_at)


async def _dispatch_history(session: AsyncSession, reservation: RoutingReservationRecord) -> RoutingDispatchView:
    try:
        return await read_routing_dispatch_history(session, reservation=reservation)
    except ValueError:
        raise RoutingAccountingDenied("ROUTING_DISPATCH_HISTORY_INVALID") from None


def _check_dispatch_receipt(receipt: RoutingExecutionUsageReceipt, consumption: RoutingDispatchConsumption | None) -> None:
    if consumption is not None and (receipt.dispatch_id != consumption.record.dispatch_id
        or receipt.dispatch_evidence_digest != _digest(consumption) or receipt.started_at < consumption.consumed_at):
        raise RoutingAccountingDenied("ROUTING_USAGE_DISPATCH_MISMATCH")
    # 取消/撤权/过期后仍须补计同一次真实消费；费用记账不表示派发时具备执行许可。


def _accounting_values(reservation: RoutingReservationRecord, price: RoutingPriceSnapshot,
    receipt: RoutingExecutionUsageReceipt,
) -> tuple[AccountingStatus, int | None, bool, AccountingErrorCode | None]:
    usage = receipt.usage
    if usage is None:
        return "unknown", None, False, "ROUTING_MODEL_USAGE_UNKNOWN"
    try:
        cost = routing_usage_cost(price, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    except ValueError:
        # 实际消费超过BIGINT不能截断或按零记；保留用量/预留并冻结后续调用。
        return "unknown", None, True, "ROUTING_USAGE_COST_OUT_OF_RANGE"
    exceeded = (usage.input_tokens > reservation.reserved_input_tokens
        or usage.output_tokens > reservation.reserved_output_tokens or cost > reservation.reserved_cost_microusd)
    return "settled", cost, exceeded, "ROUTING_USAGE_BOUND_EXCEEDED" if exceeded else None


async def _history(session: AsyncSession, *, reservation: RoutingReservationRecord,
    price: RoutingPriceSnapshot, ledger: ModelCallLedger,
) -> RoutingAccountingRecord | None:
    dispatch = await _dispatch_history(session, reservation)
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == reservation.tenant_id,
        RunEvent.run_id == reservation.run_id, RunEvent.attempt_id == reservation.attempt_id,
        RunEvent.event_type == "budget.model_routing_accounted",
        RunEvent.data["call_id"].as_string() == str(reservation.call_id))
        .order_by(RunEvent.sequence).limit(3))).all()
    previous: RoutingAccountingRecord | None = None
    try:
        if len(events) > 2:
            raise ValueError()
        for event in events:
            data = event.data
            current = RoutingAccountingRecord.model_validate(data["routing_accounting"])
            _check_receipt(current.receipt, reservation)
            if dispatch.record is not None and dispatch.consumption is None:
                raise RoutingAccountingDenied("ROUTING_DISPATCH_NOT_CONSUMED")
            _check_dispatch_receipt(current.receipt, dispatch.consumption)
            if (data.get("routing_accounting_digest") != _digest(current)
                or current.reservation_digest != _digest(reservation)
                or current.receipt_digest != _digest(current.receipt)
                or current.pricing_snapshot_digest != _digest(price)
                or current.previous_record_digest != (_digest(previous) if previous else None)
                or current.recorded_at > utc_now()
                or (current.status, current.settled_cost_microusd, current.reservation_exceeded, current.error_code)
                    != _accounting_values(reservation, price, current.receipt)):
                raise ValueError()
            if previous is not None and (previous.status != "unknown" or previous.receipt.usage is not None
                or current.receipt.usage is None
                or not _same_dispatch(previous.receipt, current.receipt)
                or previous.receipt.observation_id == current.receipt.observation_id
                or previous.recorded_at > current.recorded_at
                or previous.receipt.observed_at > current.receipt.observed_at):
                raise ValueError()
            previous = current
        usage = previous.receipt.usage if previous else None
        consumed = dispatch.consumption if previous is None else None
        if (ledger.status != (previous.status if previous else "unknown" if consumed else "reserved")
            or ledger.settled_cost_microusd != (previous.settled_cost_microusd if previous else None)
            or ledger.finished_at != (previous.recorded_at if previous else consumed.consumed_at if consumed else None)
            or ledger.error_code != (previous.error_code if previous else ROUTING_DISPATCH_CONSUMED_ERROR if consumed else None)
            or any(getattr(ledger, key) != (getattr(usage, key) if usage else None)
                for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"))):
            raise ValueError()
        return previous
    except RoutingAccountingDenied:
        raise
    except (KeyError, TypeError, ValueError, ValidationError):
        raise RoutingAccountingDenied() from None


async def read_routing_accounting(session: AsyncSession, *, tenant_id: UUID, run_id: UUID,
    call_id: UUID,
) -> RoutingAccountingView:
    """原调用只读查证；不要求主体或attempt仍有执行权限，不改账本、不重建回执。"""
    reservation = await read_routing_reservation(session, tenant_id=tenant_id, run_id=run_id, call_id=call_id)
    price = await _pricing(session, reservation)
    ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == call_id,
        ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.run_id == run_id)
        .execution_options(populate_existing=True))
    if ledger is None:
        raise ResourceNotFoundError("路由记账记录不存在")
    accounting = await _history(session, reservation=reservation, price=price, ledger=ledger)
    if accounting is None:
        raise ResourceNotFoundError("路由记账记录不存在")
    return RoutingAccountingView(reservation=reservation, pricing=price, accounting=accounting,
        accounting_digest=_digest(accounting))


async def settle_routed_model_call(session_factory: async_sessionmaker[AsyncSession], *, tenant_id: UUID,
    run_id: UUID, call_id: UUID, usage_reader: RoutingExecutionUsageReader | None = None,
) -> RoutingAccountingView:
    """仅原一次物理调用的记账事务；终态/撤权不免除消费，不提供产品提交或再次派发。"""
    if usage_reader is None:
        raise RoutingAccountingDenied("ROUTING_USAGE_READER_UNAVAILABLE")
    try:
        async with asyncio.timeout(12):
            async with session_factory() as session, session.begin():
                if session.get_bind().dialect.name != "postgresql":
                    raise RoutingAccountingDenied("ROUTING_POSTGRES_REQUIRED")
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                await lock_run_scope(session, run_id, tenant_id=tenant_id)
                await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update(key_share=True))
                ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == call_id,
                    ModelCallLedger.tenant_id == tenant_id, ModelCallLedger.run_id == run_id).with_for_update())
                if ledger is None:
                    raise ResourceNotFoundError("路由预留记录不存在")
                reservation = await read_routing_reservation(session, tenant_id=tenant_id, run_id=run_id, call_id=call_id)
                price = await _pricing(session, reservation)
                previous = await _history(session, reservation=reservation, price=price, ledger=ledger)
                dispatch = await _dispatch_history(session, reservation)
                if dispatch.record is not None and dispatch.consumption is None:
                    raise RoutingAccountingDenied("ROUTING_DISPATCH_NOT_CONSUMED")
                if previous is not None and previous.receipt.usage is not None:
                    result = previous
                else:
                    transaction = session.get_transaction()
                    async with asyncio.timeout(2):
                        receipt = await usage_reader.read_usage(session, reservation=reservation)
                    if transaction is None or session.get_transaction() is not transaction or not transaction.is_active:
                        raise RoutingAccountingDenied("ROUTING_USAGE_TRANSACTION_CHANGED")
                    if not isinstance(receipt, RoutingExecutionUsageReceipt):
                        raise RoutingAccountingUnconfirmed()
                    receipt = RoutingExecutionUsageReceipt.model_validate(receipt.model_dump(mode="json"))
                    _check_receipt(receipt, reservation)
                    _check_dispatch_receipt(receipt, dispatch.consumption)
                    if previous is not None and (not _same_dispatch(previous.receipt, receipt)
                        or receipt.observed_at < previous.receipt.observed_at
                        or receipt.observation_id == previous.receipt.observation_id and _digest(receipt) != previous.receipt_digest):
                        raise RoutingAccountingDenied("ROUTING_USAGE_OBSERVATION_CONFLICT")
                    if previous is not None and receipt.usage is None:
                        result = previous
                    else:
                        status, cost, exceeded, error = _accounting_values(reservation, price, receipt)
                        result = RoutingAccountingRecord(reservation_digest=_digest(reservation),
                            pricing_snapshot_digest=_digest(price), previous_record_digest=_digest(previous) if previous else None,
                            receipt=receipt, receipt_digest=_digest(receipt), status=status,
                            settled_cost_microusd=cost, reservation_exceeded=exceeded, error_code=error, recorded_at=utc_now())
                        ledger.status, ledger.settled_cost_microusd = result.status, result.settled_cost_microusd
                        ledger.error_code, ledger.finished_at = result.error_code, result.recorded_at
                        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"):
                            setattr(ledger, key, getattr(receipt.usage, key) if receipt.usage else None)
                        await append_event(session, run_id=run_id, attempt_id=reservation.attempt_id,
                            event_type="budget.model_routing_accounted", data={"call_id": str(call_id),
                                "routing_accounting": result.model_dump(mode="json"), "routing_accounting_digest": _digest(result)})
                view = RoutingAccountingView(reservation=reservation, pricing=price, accounting=result,
                    accounting_digest=_digest(result))
            return view
    except (RoutingReservationDenied, ResourceNotFoundError):
        raise
    except Exception:
        # 包括提交未知；不自动重试、退额、清除原记录或再次派发。取消保持向外传播。
        raise RoutingAccountingUnconfirmed() from None
