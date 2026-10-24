"""路由原预留的持久领取/一次消费；无SDK、模型请求、默认批准或Worker注册。"""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator, Protocol
from uuid import uuid5

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.model_routing_circuit_contracts import RoutingCircuitRequest
from supportops_core.model_routing_circuit_store import RoutingCircuitAuthority, _history, _permit, _policy_scope
from supportops_core.model_routing_dispatch_contracts import (
    ROUTING_DISPATCH_CONSUMED_ERROR, RoutingDispatchConsumption, RoutingDispatchPermit, RoutingDispatchRecord, RoutingDispatchRequest,
)
from supportops_core.model_routing_dispatch_history import read_routing_dispatch_history
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader, filter_routing_candidates
from supportops_core.model_routing_failover import replay_routing_circuit
from supportops_core.model_routing_failover_reservations import read_routing_failover_links
from supportops_core.model_routing_failover_contracts import RoutingCircuitScope, RoutingCircuitSnapshot
from supportops_core.model_routing_reservation_contracts import RoutingReservationRecord
from supportops_core.model_routing_reservations import (
    RoutingReservationAuthority, RoutingReservationDenied, _authorization, _check_budget, _current_context,
    _existing_record, _identity, _utc,
)
from supportops_core.model_routing_settlements import _pricing
from supportops_core.models import ModelCallLedger, Tenant
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import owned_run
from supportops_core.services import append_event


class RoutingDispatchDenied(RoutingReservationDenied):
    def __init__(self, code: str = "ROUTING_DISPATCH_UNAVAILABLE") -> None:
        super().__init__(code)


class RoutingDispatchUnconfirmed(RuntimeError):
    code = "ROUTING_DISPATCH_COMMIT_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("原路由派发入口提交结果暂不可确认，请只读查询原call，勿重新领取或发送")


class RoutingDispatchAuthority(Protocol):
    """独立可信派发层只读准备实际完整请求，核实v3/W24.1/W19/W23和原一次入口。

    核对原根/全部物理尝试、无遗漏在途调用，回退前无正文或业务工具；SDK隐式重试0，
    无工具/正文重放。prepared_request_digest绑定实际选中模型/原凭据revision、完整实际
    SDK请求与出站边界/超时策略，model_input_digest仍绑定原逻辑材料，不用前者替换后者。
    不接受客户端/模型上传摘要自证。不得提交/替换调用者事务、取密、联网或发模型请求。
    当前无默认实现或真实请求准备/SDK接线；返回记录不证明已经发送。
    """

    async def authorize(self, session: AsyncSession, *, request: RoutingDispatchRequest,
        reservation: RoutingReservationRecord,
    ) -> RoutingDispatchPermit: ...


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


def _now() -> datetime:
    return datetime.now(UTC)


def _owner(request: RoutingDispatchRequest) -> str:
    return hashlib.sha256(request.plan.owner_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Scope:
    session: AsyncSession
    ledger: ModelCallLedger
    reservation: RoutingReservationRecord
    lease_until: datetime


@dataclass(frozen=True)
class _Current:
    prepared_digest: str
    recovery_digest: str | None
    circuit: RoutingCircuitSnapshot
    expires_at: datetime
    observation_until: datetime


@asynccontextmanager
async def _transaction(factory: async_sessionmaker[AsyncSession], request: RoutingDispatchRequest) -> AsyncIterator[_Scope]:
    async with asyncio.timeout(10):
        async with factory() as session, session.begin():
            if session.get_bind().dialect.name != "postgresql":
                raise RoutingDispatchDenied("ROUTING_DISPATCH_POSTGRES_REQUIRED")
            await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            owned = await owned_run(session, _identity(request.plan))
            tenant = await session.scalar(select(Tenant).where(Tenant.id == request.plan.requirements.tenant_id)
                .execution_options(populate_existing=True).with_for_update())
            ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == request.plan.call_id,
                ModelCallLedger.tenant_id == request.plan.requirements.tenant_id, ModelCallLedger.run_id == request.plan.run_id)
                .execution_options(populate_existing=True).with_for_update())
            if tenant is None or tenant.status != "active" or ledger is None:
                raise RoutingDispatchDenied("ROUTING_DISPATCH_SCOPE_UNAVAILABLE")
            reservation = await _existing_record(session, plan=request.plan)
            if reservation is None or _digest(reservation) != request.reservation_digest:
                raise RoutingDispatchDenied("ROUTING_DISPATCH_RESERVATION_CHANGED")
            yield _Scope(session, ledger, reservation, _utc(owned.attempt.lease_expires_at))


def _unconsumed(ledger: ModelCallLedger) -> None:
    if (ledger.status != "reserved" or ledger.error_code is not None or ledger.finished_at is not None
        or ledger.settled_cost_microusd is not None or any(getattr(ledger, name) is not None for name in (
            "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"))):
        raise RoutingDispatchDenied("ROUTING_DISPATCH_ALREADY_CONSUMED")


async def _current(scope: _Scope, request: RoutingDispatchRequest, *, settings: Settings,
    authority: RoutingReservationAuthority, dispatch_authority: RoutingDispatchAuthority,
    boundary_reader: RoutingDataBoundaryReader, circuit_authority: RoutingCircuitAuthority,
) -> _Current:
    session, reservation, plan = scope.session, scope.reservation, request.plan
    authorization = await _authorization(session, plan=plan, authority=authority, settings=settings)
    if authorization.approval_reference_digest != reservation.approval_reference_digest:
        raise RoutingDispatchDenied("ROUTING_DISPATCH_APPROVAL_CHANGED")
    await _current_context(session, plan=plan, settings=settings, authorization=authorization)
    links = await read_routing_failover_links(session, root_plan=request.root_plan)
    recovery_required = False
    if reservation.selection_reason == "first_eligible_priority_v1":
        if links or request.failover_link_digest is not None or request.root_plan != plan:
            raise RoutingDispatchDenied("ROUTING_DISPATCH_ROOT_CHANGED")
    elif (not links or links[-1].call_id != reservation.call_id
        or _digest(links[-1]) != request.failover_link_digest or links[-1].controls_digest != _digest(request.controls)):
        raise RoutingDispatchDenied("ROUTING_DISPATCH_FAILOVER_HEAD_CHANGED")
    else:
        recovery_required = links[-1].recovery_required
    if recovery_required and request.recovery_event_id is None:
        raise RoutingDispatchDenied("ROUTING_DISPATCH_RECOVERY_REQUIRED")
    transaction, started = session.get_transaction(), _now()
    async with asyncio.timeout(1):
        permit = await dispatch_authority.authorize(session, request=request, reservation=reservation)
    if (transaction is None or session.get_transaction() is not transaction or not transaction.is_active
        or not isinstance(permit, RoutingDispatchPermit)):
        raise RoutingDispatchDenied("ROUTING_DISPATCH_APPROVAL_UNCONFIRMED")
    permit = RoutingDispatchPermit.model_validate_json(permit.model_dump_json())
    if (permit.request_digest != _digest(request) or permit.reservation_digest != _digest(reservation)
        or permit.actor_user_id != authorization.actor_user_id or permit.approval_reference_digest != reservation.approval_reference_digest
        or permit.model_input_digest != reservation.model_input_digest
        or not started <= permit.checked_at <= _now() < permit.expires_at <= plan.expires_at):
        raise RoutingDispatchDenied("ROUTING_DISPATCH_APPROVAL_UNCONFIRMED")
    candidate = next(row for row in plan.candidates if row.candidate_id == reservation.candidate_id)
    reading = RoutingCircuitRequest(action="read", controls=request.controls, scope=RoutingCircuitScope(
        tenant_id=reservation.tenant_id, policy_version_id=reservation.policy_version_id,
        policy_digest=reservation.policy_digest, candidate=candidate, controls_digest=_digest(request.controls)))
    circuit_permit = await _permit(session, circuit_authority, reading)
    await _policy_scope(session, reading)
    history = await _history(session, reading)
    circuit = replay_routing_circuit(scope=reading.scope, controls=request.controls,
        events=tuple(row.event for row in history), now=_now())
    recovery_digest: str | None = None
    until = min(plan.expires_at, authorization.expires_at, permit.expires_at, scope.lease_until)
    if circuit.phase == "closed" and request.recovery_event_id is None:
        pass
    elif (circuit.phase == "half_open" and circuit.recovery_call_id == reservation.call_id
        and circuit.recovery_until is not None and request.recovery_event_id is not None):
        recovery = next((row for row in history if row.event.event_id == request.recovery_event_id), None)
        if (recovery is None or recovery.event.kind != "recovery_claimed" or recovery.event.call_id != reservation.call_id
            or recovery.request.plan != plan or recovery.run_id != reservation.run_id
            or recovery.permit.actor_user_id != authorization.actor_user_id):
            raise RoutingDispatchDenied("ROUTING_DISPATCH_RECOVERY_MISMATCH")
        recovery_digest, until = _digest(recovery), min(until, circuit.recovery_until)
    else:
        raise RoutingDispatchDenied("ROUTING_DISPATCH_CIRCUIT_BLOCKED")
    await _pricing(session, reservation)
    await _check_budget(session, plan=plan, authorization=authorization,
        cost=reservation.reserved_cost_microusd, settings=settings, already_reserved=True)
    await _current_context(session, plan=plan, settings=settings, authorization=authorization)
    report = await filter_routing_candidates(session, requirements=plan.requirements,
        candidates=plan.candidates, boundary_reader=boundary_reader)
    selected = next((row for row in report.candidates if row.candidate_id == reservation.candidate_id), None)
    if (selected is None or selected.status != "eligible" or selected.latest_probe_id is None
        or selected.estimated_cost_microusd != reservation.reserved_cost_microusd
        or not 0 <= (_now() - report.observed_at).total_seconds() <= request.controls.observation_max_age_seconds):
        raise RoutingDispatchDenied("ROUTING_DISPATCH_CANDIDATE_EXCLUDED")
    observation_until = min(circuit_permit.expires_at,
        report.observed_at + timedelta(seconds=request.controls.observation_max_age_seconds))
    if _now() >= min(until, observation_until):
        raise RoutingDispatchDenied("ROUTING_DISPATCH_EXPIRED")
    return _Current(permit.prepared_request_digest, recovery_digest, circuit, until, observation_until)


async def claim_routing_dispatch(factory: async_sessionmaker[AsyncSession], *, request: RoutingDispatchRequest,
    settings: Settings, authority: RoutingReservationAuthority | None = None,
    dispatch_authority: RoutingDispatchAuthority | None = None, boundary_reader: RoutingDataBoundaryReader | None = None,
    circuit_authority: RoutingCircuitAuthority | None = None,
) -> RoutingDispatchRecord:
    """原call首次领取；重复/未知只读原历史，默认无独立提供者时拒绝。"""
    request = RoutingDispatchRequest.model_validate_json(request.model_dump_json())
    if authority is None or dispatch_authority is None or boundary_reader is None or circuit_authority is None:
        raise RoutingDispatchDenied("ROUTING_DISPATCH_DEPENDENCIES_UNAVAILABLE")
    wrote = False
    try:
        async with _transaction(factory, request) as scope:
            _unconsumed(scope.ledger)
            history = await read_routing_dispatch_history(scope.session, reservation=scope.reservation)
            if history.record is not None or history.consumption is not None:
                raise RoutingDispatchDenied("ROUTING_DISPATCH_ALREADY_CLAIMED")
            current = await _current(scope, request, settings=settings, authority=authority,
                dispatch_authority=dispatch_authority, boundary_reader=boundary_reader, circuit_authority=circuit_authority)
            record = RoutingDispatchRecord(dispatch_id=uuid5(request.plan.call_id, "routing-dispatch-v1"),
                reservation=scope.reservation, request_digest=_digest(request), owner_digest=_owner(request),
                root_call_id=request.root_plan.call_id, root_plan_digest=_digest(request.root_plan),
                controls_digest=_digest(request.controls), failover_link_digest=request.failover_link_digest,
                recovery_record_digest=current.recovery_digest, circuit_scope_digest=current.circuit.scope_digest,
                circuit_head_digest=current.circuit.last_event_digest, prepared_request_digest=current.prepared_digest,
                claimed_at=_now(), expires_at=current.expires_at)
            wrote = True
            await append_event(scope.session, run_id=request.plan.run_id, attempt_id=request.plan.attempt_id,
                event_type="routing.dispatch_claimed", data={"call_id": str(request.plan.call_id),
                    "record": record.model_dump(mode="json"), "record_digest": _digest(record)})
            if _now() >= min(record.expires_at, current.observation_until):
                raise RoutingDispatchDenied("ROUTING_DISPATCH_EXPIRED")
        if _now() >= min(record.expires_at, current.observation_until):
            raise RoutingDispatchUnconfirmed()
        return record
    except BaseException:
        if wrote:
            raise RoutingDispatchUnconfirmed() from None
        raise


async def consume_routing_dispatch(factory: async_sessionmaker[AsyncSession], *, request: RoutingDispatchRequest,
    record: RoutingDispatchRecord, settings: Settings, authority: RoutingReservationAuthority | None = None,
    dispatch_authority: RoutingDispatchAuthority | None = None, boundary_reader: RoutingDataBoundaryReader | None = None,
    circuit_authority: RoutingCircuitAuthority | None = None,
) -> RoutingDispatchConsumption:
    """提交一次消费后返回；不发送SDK。未知/崩溃/取消不重开、不退额或再次派发。

    未来可信执行者须在expires_at入口新鲜界限前紧邻本次成功提交进入唯一SDK调用，
    已进入调用仍受record.expires_at原窗口和持续围栏限制，不能重放或因入口到期再派发。
    """
    request = RoutingDispatchRequest.model_validate_json(request.model_dump_json())
    record = RoutingDispatchRecord.model_validate_json(record.model_dump_json())
    if authority is None or dispatch_authority is None or boundary_reader is None or circuit_authority is None:
        raise RoutingDispatchDenied("ROUTING_DISPATCH_DEPENDENCIES_UNAVAILABLE")
    wrote = False
    try:
        async with _transaction(factory, request) as scope:
            _unconsumed(scope.ledger)
            history = await read_routing_dispatch_history(scope.session, reservation=scope.reservation)
            if (history.record != record or history.consumption is not None or record.request_digest != _digest(request)
                or record.owner_digest != _owner(request) or _now() >= record.expires_at):
                raise RoutingDispatchDenied("ROUTING_DISPATCH_CLAIM_MISMATCH")
            current = await _current(scope, request, settings=settings, authority=authority,
                dispatch_authority=dispatch_authority, boundary_reader=boundary_reader, circuit_authority=circuit_authority)
            if (current.prepared_digest != record.prepared_request_digest or current.recovery_digest != record.recovery_record_digest
                or current.circuit.scope_digest != record.circuit_scope_digest):
                raise RoutingDispatchDenied("ROUTING_DISPATCH_REQUEST_CHANGED")
            consumption = RoutingDispatchConsumption(record=record, record_digest=_digest(record),
                confirmation_circuit_digest=current.circuit.last_event_digest, consumed_at=_now(),
                expires_at=min(record.expires_at, current.expires_at, current.observation_until))
            scope.ledger.status, scope.ledger.error_code = "unknown", ROUTING_DISPATCH_CONSUMED_ERROR
            scope.ledger.finished_at = consumption.consumed_at
            wrote = True
            await append_event(scope.session, run_id=request.plan.run_id, attempt_id=request.plan.attempt_id,
                event_type="routing.dispatch_consumed", data={"call_id": str(request.plan.call_id),
                    "consumption": consumption.model_dump(mode="json"), "consumption_digest": _digest(consumption)})
            await scope.session.flush()
            if _now() >= consumption.expires_at:
                raise RoutingDispatchDenied("ROUTING_DISPATCH_EXPIRED")
        if _now() >= consumption.expires_at:
            raise RoutingDispatchUnconfirmed()
        return consumption
    except BaseException:
        if wrote:
            raise RoutingDispatchUnconfirmed() from None
        raise
