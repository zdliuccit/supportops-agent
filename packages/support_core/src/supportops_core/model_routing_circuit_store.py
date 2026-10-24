"""原范围持久熔断和唯一恢复领取；缺独立批准默认关闭，不派发或自动探测。"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.model_routing_accounting_contracts import RoutingAccountingView
from supportops_core.model_routing_circuit_contracts import (
    RoutingCircuitPermit, RoutingCircuitRequest, RoutingCircuitStoredEvent, RoutingCircuitView,
)
from supportops_core.model_routing_circuit_history import assert_routing_circuit_history
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader, filter_routing_candidates
from supportops_core.model_routing_failover import replay_routing_circuit
from supportops_core.model_routing_failover_contracts import RoutingCircuitEvent
from supportops_core.model_routing_policy_contracts import RoutingPolicyVersionRecord
from supportops_core.model_routing_reservation_contracts import RoutingBudgetAuthorization
from supportops_core.model_routing_reservations import (
    RoutingReservationAuthority, _authorization, _current_context, read_routing_reservation,
)
from supportops_core.model_routing_settlements import read_routing_accounting
from supportops_core.models import ModelCallLedger, ModelRoutingCircuitEvent, ModelRoutingPolicyVersion, Tenant
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import lock_run_scope


class RoutingCircuitDenied(ValueError):
    def __init__(self, code: str = "ROUTING_CIRCUIT_UNAVAILABLE") -> None:
        super().__init__("原路由熔断范围或操作许可暂不可确认")
        self.code = code


class RoutingCircuitUnconfirmed(RuntimeError):
    code = "ROUTING_CIRCUIT_COMMIT_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("熔断操作提交结果暂不可确认，请只读查询原事件ID，勿重复领取或派发")


class RoutingCircuitAuthority(Protocol):
    """独立可信源复检读取/留存/写入许可；不接受HTTP或模型上传的批准/分类。

    observe须从原唯一物理派发的可信持久错误事实确认本次分类，核对传入的原账本与
    回执，不因失败文案/调用者枚举推断暂时故障；撤权/取消后仍可记已发生的观察。
    claim_recovery另须确认W24.1/W19/W23、完整未遗漏的原逻辑尝试、物理次数、正文/
    工具尚未开始与明确恢复预算批准。读旧记录不恢复运行许可。提供者只读调用者
    事务，不提交/回滚/替换事务，不取密/联网/调模型；当前无默认实现或运行注册。
    """

    async def authorize(self, session: AsyncSession, *, request: RoutingCircuitRequest,
        accounting: RoutingAccountingView | None,
    ) -> RoutingCircuitPermit: ...


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


def _now() -> datetime:
    return datetime.now(UTC)


async def _permit(session: AsyncSession, authority: RoutingCircuitAuthority, request: RoutingCircuitRequest,
    accounting: RoutingAccountingView | None = None,
) -> RoutingCircuitPermit:
    started, transaction = _now(), session.get_transaction()
    async with asyncio.timeout(1):
        permit = await authority.authorize(session, request=request, accounting=accounting)
    if (transaction is None or session.get_transaction() is not transaction or not transaction.is_active
        or not isinstance(permit, RoutingCircuitPermit) or permit.request_digest != _digest(request)
        or not started <= permit.checked_at <= _now() < permit.expires_at):
        raise RoutingCircuitDenied("ROUTING_CIRCUIT_APPROVAL_UNCONFIRMED")
    return RoutingCircuitPermit.model_validate_json(permit.model_dump_json())


async def _history(session: AsyncSession, request: RoutingCircuitRequest) -> tuple[RoutingCircuitStoredEvent, ...]:
    await assert_routing_circuit_history(session)
    rows = (await session.scalars(select(ModelRoutingCircuitEvent).where(
        ModelRoutingCircuitEvent.scope_digest == _digest(request.scope))
        .order_by(ModelRoutingCircuitEvent.sequence).limit(257))).all()
    if len(rows) > 256:
        raise RoutingCircuitDenied("ROUTING_CIRCUIT_HISTORY_INVALID")
    results: list[RoutingCircuitStoredEvent] = []
    for row in rows:
        record = RoutingCircuitStoredEvent.model_validate_json(json.dumps(row.payload,
            ensure_ascii=False, sort_keys=True, allow_nan=False))
        event = record.event
        if (record.scope != request.scope or record.controls != request.controls
            or row.id != event.event_id or row.scope_digest != event.scope_digest
            or row.tenant_id != request.scope.tenant_id or row.policy_version_id != request.scope.policy_version_id
            or row.candidate_id != request.scope.candidate.candidate_id or row.run_id != record.run_id
            or row.call_id != event.call_id or row.kind != event.kind or row.sequence != event.sequence
            or row.previous_event_digest != event.previous_event_digest or row.event_digest != _digest(event)
            or row.record_digest != _digest(record) or row.created_at != event.recorded_at):
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_HISTORY_INVALID")
        results.append(record)
    replay_routing_circuit(scope=request.scope, controls=request.controls,
        events=tuple(row.event for row in results), now=_now())
    return tuple(results)


async def _policy_scope(session: AsyncSession, request: RoutingCircuitRequest) -> None:
    """读取也核实持久原范围；撤销/到期可查证，不能凭空造出可用的空历史。"""
    row = await session.scalar(select(ModelRoutingPolicyVersion).where(
        ModelRoutingPolicyVersion.id == request.scope.policy_version_id,
        ModelRoutingPolicyVersion.tenant_id == request.scope.tenant_id))
    if row is None:
        raise RoutingCircuitDenied("ROUTING_CIRCUIT_POLICY_UNAVAILABLE")
    policy = RoutingPolicyVersionRecord.model_validate_json(json.dumps(row.payload, allow_nan=False))
    if (_digest(policy) != row.policy_digest or row.policy_digest != request.scope.policy_digest
        or policy.version_id != row.id or policy.tenant_id != row.tenant_id
        or policy.definition.fallback_policy_digest != _digest(request.controls)
        or request.scope.candidate not in policy.definition.candidates):
        raise RoutingCircuitDenied("ROUTING_CIRCUIT_POLICY_MISMATCH")


class PostgresRoutingCircuitStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession], *, settings: Settings,
        authority: RoutingCircuitAuthority | None = None,
        recovery_authority: RoutingReservationAuthority | None = None,
        boundary_reader: RoutingDataBoundaryReader | None = None,
    ) -> None:
        self._factory, self._settings = factory, settings
        self._authority, self._recovery_authority, self._boundary_reader = authority, recovery_authority, boundary_reader

    async def read(self, request: RoutingCircuitRequest, *, event_id: UUID | None = None) -> RoutingCircuitView:
        """新的当前读取许可与一致快照；过期/撤销后的原ID仍可查证，不授予重领许可。"""
        request = RoutingCircuitRequest.model_validate_json(request.model_dump_json())
        if self._authority is None or request.action != "read":
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_AUTHORITY_UNAVAILABLE")
        try:
            async with asyncio.timeout(8):
                async with self._factory() as session, session.begin():
                    if session.get_bind().dialect.name != "postgresql":
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_POSTGRES_REQUIRED")
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                    await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                    permit = await _permit(session, self._authority, request)
                    await _policy_scope(session, request)
                    history = await _history(session, request)
                    record = next((row for row in history if row.event.event_id == event_id), None)
                    if event_id is not None and record is None:
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_EVENT_UNAVAILABLE")
                    snapshot = replay_routing_circuit(scope=request.scope, controls=request.controls,
                        events=tuple(row.event for row in history), now=_now())
                    await assert_routing_circuit_history(session)
                    if _now() >= permit.expires_at:
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_APPROVAL_EXPIRED")
                    result = RoutingCircuitView(snapshot=snapshot, event=record,
                        record_digest=_digest(record) if record else None)
                if _now() >= permit.expires_at:
                    raise RoutingCircuitDenied("ROUTING_CIRCUIT_APPROVAL_EXPIRED")
                return result
        except RoutingCircuitDenied:
            raise
        except Exception:
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_HISTORY_UNCONFIRMED") from None

    async def append(self, request: RoutingCircuitRequest) -> RoutingCircuitView:
        """每个固定event_id只允许首次追加；异常或取消后只查原ID，不自动重试。"""
        request = RoutingCircuitRequest.model_validate_json(request.model_dump_json())
        if self._authority is None or request.action == "read":
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_AUTHORITY_UNAVAILABLE")
        if request.run_id is None or request.call_id is None or request.event_id is None:
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_WRITE_SCOPE_INVALID")
        wrote = False
        try:
            async with asyncio.timeout(10):
                async with self._factory() as session, session.begin():
                    if session.get_bind().dialect.name != "postgresql":
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_POSTGRES_REQUIRED")
                    await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                    await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                    await lock_run_scope(session, request.run_id, tenant_id=request.scope.tenant_id)
                    tenant = await session.scalar(select(Tenant.id).where(Tenant.id == request.scope.tenant_id).with_for_update())
                    ledger = await session.scalar(select(ModelCallLedger).where(ModelCallLedger.id == request.call_id,
                        ModelCallLedger.tenant_id == request.scope.tenant_id, ModelCallLedger.run_id == request.run_id)
                        .execution_options(populate_existing=True).with_for_update())
                    if tenant is None or ledger is None:
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_CALL_UNAVAILABLE")
                    history = await _history(session, request)
                    last = history[-1].event if history else None
                    if (len(history) != request.expected_sequence
                        or (_digest(last) if last else None) != request.expected_event_digest
                        or any(row.event.event_id == request.event_id for row in history)
                        or any(row.event.call_id == request.call_id and row.event.kind == (
                            "observation" if request.action == "observe" else "recovery_claimed") for row in history)):
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_HEAD_CHANGED")
                    await _policy_scope(session, request)
                    reservation = await read_routing_reservation(session, tenant_id=request.scope.tenant_id,
                        run_id=request.run_id, call_id=request.call_id)
                    if (reservation.policy_version_id != request.scope.policy_version_id
                        or reservation.policy_digest != request.scope.policy_digest
                        or reservation.candidate_digest != _digest(request.scope.candidate)):
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_CANDIDATE_MISMATCH")
                    accounting: RoutingAccountingView | None = None
                    recovery: RoutingBudgetAuthorization | None = None
                    if request.action == "observe":
                        accounting = await read_routing_accounting(session, tenant_id=request.scope.tenant_id,
                            run_id=request.run_id, call_id=request.call_id)
                    else:
                        recovery = await self._recovery(session, request, reservation.plan_digest, ledger.status)
                    permit = await _permit(session, self._authority, request, accounting)
                    if recovery is not None and permit.actor_user_id != recovery.actor_user_id:
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_ACTOR_MISMATCH")
                    receipt = accounting.accounting.receipt if accounting else None
                    complete = receipt is not None and receipt.usage is not None
                    event = RoutingCircuitEvent(event_id=request.event_id, scope_digest=_digest(request.scope),
                        sequence=len(history) + 1, previous_event_digest=request.expected_event_digest,
                        kind="observation" if accounting else "recovery_claimed", call_id=request.call_id,
                        outcome=request.outcome, receipt=receipt,
                        accounting_digest=accounting.accounting_digest if accounting and complete else None,
                        accounting_within_bounds=(accounting.accounting.status == "settled"
                            and not accounting.accounting.reservation_exceeded and accounting.accounting.error_code is None)
                            if accounting and complete else None, recorded_at=_now())
                    snapshot = replay_routing_circuit(scope=request.scope, controls=request.controls,
                        events=(*tuple(row.event for row in history), event), now=_now())
                    if recovery is not None and (snapshot.phase != "half_open" or snapshot.recovery_until is None
                        or snapshot.recovery_until > recovery.expires_at):
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_RECOVERY_WINDOW_UNAVAILABLE")
                    record = RoutingCircuitStoredEvent(scope=request.scope, controls=request.controls, event=event,
                        run_id=request.run_id, request=request, request_digest=_digest(request), permit=permit,
                        source_accounting_digest=accounting.accounting_digest if accounting else None)
                    if len(json.dumps(record.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")) > 30000:
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_METADATA_TOO_LARGE")
                    session.add(ModelRoutingCircuitEvent(id=event.event_id, scope_digest=event.scope_digest,
                        tenant_id=request.scope.tenant_id, policy_version_id=request.scope.policy_version_id,
                        candidate_id=request.scope.candidate.candidate_id, run_id=request.run_id, call_id=request.call_id,
                        sequence=event.sequence, previous_event_digest=event.previous_event_digest, kind=event.kind,
                        event_digest=_digest(event), record_digest=_digest(record), payload=record.model_dump(mode="json"),
                        created_at=event.recorded_at))
                    wrote = True
                    await session.flush()
                    await assert_routing_circuit_history(session)
                    if recovery is not None:
                        if request.plan is None:
                            raise RoutingCircuitDenied("ROUTING_CIRCUIT_RECOVERY_SCOPE_INVALID")
                        await _current_context(session, plan=request.plan, settings=self._settings, authorization=recovery)
                        await self._eligible_recovery(session, request)
                    if (_now() >= permit.expires_at or recovery is not None and (
                        _now() >= recovery.expires_at or snapshot.recovery_until is None
                        or _now() >= snapshot.recovery_until)):
                        raise RoutingCircuitDenied("ROUTING_CIRCUIT_APPROVAL_EXPIRED")
                if _now() >= permit.expires_at or recovery is not None and _now() >= recovery.expires_at:
                    raise RoutingCircuitUnconfirmed()
                return RoutingCircuitView(snapshot=snapshot, event=record, record_digest=_digest(record),
                    recovery_claimed=recovery is not None)
        except BaseException as exc:
            if wrote:
                raise RoutingCircuitUnconfirmed() from None
            if isinstance(exc, (RoutingCircuitDenied, asyncio.CancelledError)):
                raise
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_WRITE_UNCONFIRMED") from None

    async def _recovery(self, session: AsyncSession, request: RoutingCircuitRequest,
        plan_digest: str, ledger_status: str,
    ) -> RoutingBudgetAuthorization:
        plan = request.plan
        if (plan is None or self._recovery_authority is None or self._boundary_reader is None
            or ledger_status != "reserved" or _digest(plan) != plan_digest):
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_RECOVERY_AUTHORITY_UNAVAILABLE")
        authorization = await _authorization(session, plan=plan, authority=self._recovery_authority, settings=self._settings)
        await _current_context(session, plan=plan, settings=self._settings, authorization=authorization)
        await self._eligible_recovery(session, request)
        return authorization

    async def _eligible_recovery(self, session: AsyncSession, request: RoutingCircuitRequest) -> None:
        plan = request.plan
        if plan is None or self._boundary_reader is None:
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_RECOVERY_AUTHORITY_UNAVAILABLE")
        report = await filter_routing_candidates(session, requirements=plan.requirements,
            candidates=plan.candidates, boundary_reader=self._boundary_reader)
        selected = next((row for row in report.candidates if row.candidate_id == request.scope.candidate.candidate_id), None)
        if (selected is None or selected.status != "eligible" or selected.latest_probe_id is None
            or selected.estimated_cost_microusd is None or selected.estimated_cost_microusd > plan.requirements.max_call_cost_microusd
            or not 0 <= (_now() - report.observed_at).total_seconds() <= request.controls.observation_max_age_seconds):
            raise RoutingCircuitDenied("ROUTING_CIRCUIT_RECOVERY_CANDIDATE_EXCLUDED")
