"""父尝试围栏下的持久一次领取/入口消费；不构造SDK或发送模型请求。"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncIterator
from uuid import UUID, uuid5

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.model_routing_accounting_guards import routing_accounting_blocks_budget
from supportops_core.models import ModelCallLedger, ModelEndpointVersion, Tenant
from supportops_core.quota_services import quota_day_start
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import RunAttemptIdentity, owned_run
from supportops_core.services import append_event
from supportops_core.specialist_accounting_guards import specialist_accounting_blocks_budget, specialist_shared_tokens_allow
from supportops_core.specialist_budget_contracts import SpecialistBudgetAuthorization, SpecialistBudgetReservation
from supportops_core.specialist_budget_services import (
    SpecialistBudgetAuthority, _authorize, _microusd, _parent_context,
    project_specialist_shared_budget, read_specialist_budget_reservation,
)
from supportops_core.specialist_material_contracts import SpecialistMaterialScope, material_context_digest
from supportops_core.specialist_material_services import prepare_specialist_material
from supportops_core.specialist_dispatch_contracts import (
    DISPATCH_CONSUMED_ERROR, SpecialistDispatchConsumption, SpecialistDispatchRecord,
)
from supportops_core.specialist_dispatch_history import read_specialist_dispatch_history
from supportops_core.specialist_request_contracts import SpecialistPreparedRequest
from supportops_core.specialist_scheduler_contracts import SpecialistDispatchClaim
from supportops_core.specialist_task_contracts import SpecialistTaskInput, contract_digest


class SpecialistDispatchDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("子任务当前不可领取或消费派发入口")
        self.code = code


class SpecialistDispatchUnconfirmed(RuntimeError):
    code = "SPECIALIST_DISPATCH_COMMIT_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("原子任务派发入口提交结果暂不可确认，请只读原call_id，勿重新领取或派发")


def _owner_digest(attempt: RunAttemptIdentity) -> str:
    return hashlib.sha256(attempt.owner_id.encode("utf-8")).hexdigest()


@asynccontextmanager
async def _transaction(factory: async_sessionmaker[AsyncSession], attempt: RunAttemptIdentity,
) -> AsyncIterator[AsyncSession]:
    async with asyncio.timeout(10):
        async with factory() as session, session.begin():
            if session.get_bind().dialect.name != "postgresql":
                raise SpecialistDispatchDenied("SPECIALIST_POSTGRES_REQUIRED")
            await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            await owned_run(session, attempt)
            # 与共享预留保持相同锁序，租户预算锁先于知识/凭据元数据锁。
            tenant = await session.scalar(select(Tenant).where(Tenant.id == attempt.tenant_id)
                .execution_options(populate_existing=True).with_for_update(key_share=True))
            if tenant is None or tenant.status != "active":
                raise SpecialistDispatchDenied("SPECIALIST_AUTHORIZATION_CHANGED")
            yield session


async def _reservation(session: AsyncSession, *, attempt: RunAttemptIdentity, call_id: UUID,
) -> tuple[SpecialistBudgetReservation, ModelCallLedger]:
    record = await read_specialist_budget_reservation(session, tenant_id=attempt.tenant_id,
        run_id=attempt.run_id, call_id=call_id)
    ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == call_id,
        ModelCallLedger.tenant_id == attempt.tenant_id, ModelCallLedger.run_id == attempt.run_id)
        .execution_options(populate_existing=True).with_for_update())
    if (record is None or ledger is None or record.conversation_id != attempt.conversation_id
        or record.attempt_id != attempt.attempt_id or record.generation != attempt.generation):
        raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_SCOPE_MISMATCH")
    return record, ledger


def _unconsumed(ledger: ModelCallLedger) -> None:
    # unknown是永久消费标记，不因缺回执、过期、崩溃或事件缺失退回reserved。
    if (ledger.status != "reserved" or ledger.error_code is not None or ledger.finished_at is not None
        or ledger.settled_cost_microusd is not None
        or any(getattr(ledger, key) is not None for key in (
            "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"))):
        raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_ALREADY_CONSUMED")


async def _available(session: AsyncSession, *, reservation: SpecialistBudgetReservation, settings: Settings) -> None:
    tenant = await session.scalar(select(Tenant).where(Tenant.id == reservation.tenant_id)
        .execution_options(populate_existing=True).with_for_update(key_share=True))
    if tenant is None or tenant.status != "active":
        raise SpecialistDispatchDenied("SPECIALIST_AUTHORIZATION_CHANGED")
    for guard in (specialist_accounting_blocks_budget, routing_accounting_blocks_budget):
        if await guard(session, tenant_id=reservation.tenant_id, run_id=reservation.run_id,
            enforce_tenant_cost=settings.tenant_model_cost_usd_per_day is not None):
            raise SpecialistDispatchDenied("SPECIALIST_SHARED_BUDGET_BLOCKED")
    if not await specialist_shared_tokens_allow(session, tenant_id=reservation.tenant_id, run_id=reservation.run_id,
        input_tokens=0, output_tokens=0):
        raise SpecialistDispatchDenied("SPECIALIST_SHARED_TOKEN_BUDGET_EXCEEDED")
    consumed = func.coalesce(ModelCallLedger.settled_cost_microusd, ModelCallLedger.reserved_cost_microusd)
    for filters, count_limit, cost_limit, run_scope in (
        ([ModelCallLedger.tenant_id == reservation.tenant_id, ModelCallLedger.run_id == reservation.run_id],
            reservation.budget.model_calls_limit, reservation.budget.cost_microusd_limit, True),
        ([ModelCallLedger.tenant_id == reservation.tenant_id, ModelCallLedger.created_at >= quota_day_start()],
            settings.tenant_model_call_limit_per_day,
            _microusd(settings.tenant_model_cost_usd_per_day) if settings.tenant_model_cost_usd_per_day is not None else None, False),
    ):
        count, last, cost, unknown = (await session.execute(select(func.count(), func.coalesce(func.max(ModelCallLedger.call_number), 0),
            func.coalesce(func.sum(consumed), 0),
            func.count().filter(consumed.is_(None))).where(*filters))).one()
        if run_scope and last != count:
            raise SpecialistDispatchDenied("SPECIALIST_BUDGET_HISTORY_INVALID")
        # 原call已经预留并计入总量，不再加一次调用或费用；刚好到上限仍可消费原call。
        if count > count_limit or cost_limit is not None and (unknown or cost > cost_limit):
            raise SpecialistDispatchDenied("SPECIALIST_SHARED_BUDGET_BLOCKED")


async def _current(session: AsyncSession, *, attempt: RunAttemptIdentity, reservation: SpecialistBudgetReservation,
    task: SpecialistTaskInput, scope: SpecialistMaterialScope, settings: Settings,
    authority: SpecialistBudgetAuthority, redaction_secrets: tuple[str, ...],
) -> tuple[SpecialistPreparedRequest, SpecialistBudgetAuthorization]:
    if (task.task_id != reservation.task_id or contract_digest(task) != reservation.task_input_digest
        or task.parent.version != reservation.parent_version or task.child_version != reservation.child_version
        or task.budget != reservation.budget or datetime.now(UTC) >= reservation.expires_at):
        raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_SCOPE_MISMATCH")
    await _parent_context(session, attempt=attempt, settings=settings)
    context, prepared = await prepare_specialist_material(session, attempt=attempt, task=task, scope=scope,
        settings=settings, redaction_secrets=redaction_secrets)
    budget = await project_specialist_shared_budget(session, attempt=attempt, settings=settings,
        expires_at=reservation.budget.deadline)
    if (budget != reservation.budget or material_context_digest(context) != reservation.context_digest
        or prepared.model_input_digest != reservation.model_input_digest
        or prepared.credential_revision != reservation.credential_revision
        or prepared.reserved_input_tokens != reservation.reserved_input_tokens
        or prepared.reserved_output_tokens != reservation.reserved_output_tokens):
        raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_REQUEST_CHANGED")
    approval = await _authorize(session, authority=authority, call_id=reservation.call_id, task=task,
        schedule_digest=reservation.schedule_digest, context=context, budget=budget, prepared=prepared)
    if approval.governance_decision_digest != reservation.governance_decision_digest:
        raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_APPROVAL_CHANGED")
    # 独立批准等待后复查当前授权/正文/固定请求，不能用旧领取延长许可。
    await _parent_context(session, attempt=attempt, settings=settings)
    final_context, final_prepared = await prepare_specialist_material(session, attempt=attempt, task=task, scope=scope,
        settings=settings, redaction_secrets=redaction_secrets)
    final_budget = await project_specialist_shared_budget(session, attempt=attempt, settings=settings,
        expires_at=reservation.budget.deadline)
    if (material_context_digest(final_context) != reservation.context_digest or final_budget != budget
        or final_prepared != prepared):
        raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_REQUEST_CHANGED")
    pricing = await session.scalar(select(ModelEndpointVersion.pricing).where(
        ModelEndpointVersion.id == reservation.child_version.model_version_id,
        ModelEndpointVersion.tenant_id == reservation.tenant_id).with_for_update(read=True))
    if not isinstance(pricing, dict) or manifest_digest(pricing) != reservation.price_snapshot.source_pricing_digest:
        raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_PRICE_CHANGED")
    await _available(session, reservation=reservation, settings=settings)
    await owned_run(session, attempt)
    if datetime.now(UTC) >= min(reservation.expires_at, approval.expires_at):
        raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_EXPIRED")
    return prepared, approval


async def claim_specialist_dispatch(factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity,
    call_id: UUID, task: SpecialistTaskInput, scope: SpecialistMaterialScope, settings: Settings,
    authority: SpecialistBudgetAuthority | None = None, redaction_secrets: tuple[str, ...] = (),
) -> SpecialistDispatchRecord:
    """独立提交一份原领取。重复领取只允许只读查询，不复用历史生成新的执行许可。"""
    if authority is None:
        raise SpecialistDispatchDenied("SPECIALIST_BUDGET_AUTHORITY_UNAVAILABLE")
    wrote = False
    try:
        async with _transaction(factory, attempt) as session:
            reservation, ledger = await _reservation(session, attempt=attempt, call_id=call_id)
            _unconsumed(ledger)
            history = await read_specialist_dispatch_history(session, reservation=reservation)
            if history.record is not None or history.consumption is not None:
                raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_ALREADY_CLAIMED")
            prepared, approval = await _current(session, attempt=attempt, reservation=reservation,
                task=task, scope=scope, settings=settings, authority=authority, redaction_secrets=redaction_secrets)
            record = SpecialistDispatchRecord(tenant_id=attempt.tenant_id, conversation_id=attempt.conversation_id,
                run_id=attempt.run_id, attempt_id=attempt.attempt_id, generation=attempt.generation,
                call_id=call_id, owner_digest=_owner_digest(attempt), prepared_request_digest=contract_digest(prepared),
                model_input_digest=prepared.model_input_digest,
                claim=SpecialistDispatchClaim(dispatch_id=uuid5(call_id, "specialist-dispatch-v1"), task_id=task.task_id,
                    task_input_digest=reservation.task_input_digest,
                    authorization_scope_digest=reservation.authorization_scope_digest,
                    budget_scope_digest=contract_digest(reservation.budget), budget_reservation_digest=contract_digest(reservation),
                    governance_decision_digest=approval.governance_decision_digest, claimed_at=datetime.now(UTC),
                    expires_at=min(reservation.expires_at, task.deadline, approval.expires_at)))
            wrote = True
            await append_event(session, run_id=attempt.run_id, attempt_id=attempt.attempt_id,
                event_type="specialist.dispatch_claimed", data={"call_id": str(call_id),
                    "record": record.model_dump(mode="json"), "record_digest": contract_digest(record)})
            if datetime.now(UTC) >= record.claim.expires_at:
                raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_EXPIRED")
        return record
    except BaseException:
        if wrote:
            raise SpecialistDispatchUnconfirmed() from None
        raise


async def consume_specialist_dispatch(factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity,
    record: SpecialistDispatchRecord, task: SpecialistTaskInput, scope: SpecialistMaterialScope, settings: Settings,
    authority: SpecialistBudgetAuthority | None = None, redaction_secrets: tuple[str, ...] = (),
) -> SpecialistDispatchConsumption:
    """原入口消费提交成功才返回；无SDK/派发回调，返回记录本身不是可重用执行令牌。

    已消费/提交未知/取消/崩溃/超时不重新开放，保留原预算。真实执行器仍须紧邻原消费
    进入一次原SDK调用并持续围栏；当前未接执行器，不能以本函数返回证明请求已发送。
    """
    if authority is None:
        raise SpecialistDispatchDenied("SPECIALIST_BUDGET_AUTHORITY_UNAVAILABLE")
    wrote = False
    try:
        async with _transaction(factory, attempt) as session:
            reservation, ledger = await _reservation(session, attempt=attempt, call_id=record.call_id)
            _unconsumed(ledger)
            history = await read_specialist_dispatch_history(session, reservation=reservation)
            if history.record != record or history.consumption is not None or record.owner_digest != _owner_digest(attempt):
                raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_CLAIM_MISMATCH")
            if datetime.now(UTC) >= record.claim.expires_at:
                raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_EXPIRED")
            prepared, approval = await _current(session, attempt=attempt, reservation=reservation,
                task=task, scope=scope, settings=settings, authority=authority, redaction_secrets=redaction_secrets)
            if (contract_digest(prepared) != record.prepared_request_digest
                or datetime.now(UTC) >= min(record.claim.expires_at, approval.expires_at)):
                raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_REQUEST_CHANGED")
            consumption = SpecialistDispatchConsumption(record=record, record_digest=contract_digest(record),
                consumed_at=datetime.now(UTC), expires_at=min(record.claim.expires_at, approval.expires_at))
            # 在任何未来SDK调用前永久离开reserved。事件丢失/损坏也不能据此重新消费。
            ledger.status, ledger.error_code, ledger.finished_at = "unknown", DISPATCH_CONSUMED_ERROR, consumption.consumed_at
            wrote = True
            await append_event(session, run_id=attempt.run_id, attempt_id=attempt.attempt_id,
                event_type="specialist.dispatch_consumed", data={"call_id": str(record.call_id),
                    "consumption": consumption.model_dump(mode="json"), "consumption_digest": contract_digest(consumption)})
            if datetime.now(UTC) >= min(record.claim.expires_at, approval.expires_at):
                raise SpecialistDispatchDenied("SPECIALIST_DISPATCH_EXPIRED")
        return consumption
    except BaseException:
        if wrote:
            raise SpecialistDispatchUnconfirmed() from None
        raise
