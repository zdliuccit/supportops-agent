"""固定原输入的一至三次路由协调；所有真实批准/历史提供者显式注入，无Worker注册。"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.model_routing_circuit_contracts import RoutingCircuitRequest
from supportops_core.model_routing_circuit_store import PostgresRoutingCircuitStore, RoutingCircuitAuthority
from supportops_core.model_routing_coordination_contracts import RoutingExecutionHistory
from supportops_core.model_routing_dispatch_contracts import RoutingDispatchRequest
from supportops_core.model_routing_dispatch_services import claim_routing_dispatch
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader
from supportops_core.model_routing_execution_source import (
    PostgresRoutingExecutionHistoryReader, PostgresRoutingObservedCircuitAuthority,
)
from supportops_core.model_routing_failover_contracts import RoutingCircuitScope, RoutingFailoverAttempt, RoutingFailoverControls
from supportops_core.model_routing_failover_reservation_contracts import RoutingFailoverObservation, RoutingFailoverReservationRequest
from supportops_core.model_routing_failover_reservations import read_routing_failover_links, reserve_routing_failover_call
from supportops_core.model_routing_policy_contracts import RoutingPolicyVersionRecord
from supportops_core.model_routing_request_services import PreparedRoutingDispatchAuthority, RoutingPreparedApprovalReader
from supportops_core.model_routing_request_contracts import RoutingLogicalInput
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan, RoutingReservationRecord
from supportops_core.model_routing_reservations import (
    RoutingReservationAuthority, _authorization, _current_context, read_routing_reservation, reserve_routed_model_call,
)
from supportops_core.model_routing_runtime_input import RoutingRuntimeInput
from supportops_core.model_routing_sdk_executor import PostgresRoutingSDKExecutor, RoutingSDKResult
from supportops_core.model_routing_settlements import read_routing_accounting
from supportops_core.models import ModelCallLedger, ModelRoutingPolicyVersion
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.secrets import SecretProvider
from supportops_core.outbound_http import OutboundHttpPolicy


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


def _now() -> datetime:
    return datetime.now(UTC)


class RoutingCoordinationDenied(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__("原有限路由协调当前不可确认，请只读查询原call/事件，勿重新派发")
        self.code = code


class RoutingExecutionHistoryReader(Protocol):
    """独立可信完整执行源，不从协调器SDK摘要/布尔或错误文案推导可重试。

    核对原根和全部真实物理派发、原输入/attempt/generation、无遗漏在途/已预留调用、
    当前公开正文与业务工具事实和真实结构化错误分类。只有pending_call_id可作为当前
    预留事务尚未派发的下一call排除，不能排除其他未知调用或换根重置次数。
    完整费用须来自原账本；source_reference_digest固定原事实引用，不含读取时间或
    pending ID。只读原事务，不取密、联网、提交/替换事务或通过上传元数据自证。
    """

    async def observe(self, session: AsyncSession, *, root_plan: RoutingCallPlan,
        controls: RoutingFailoverControls, pending_call_id: UUID | None,
    ) -> RoutingExecutionHistory: ...


@dataclass(frozen=True, slots=True)
class RoutingCoordinationResult:
    """completed仅表示模型观察完整；产品采纳/业务终态仍由原结果流水线决定。"""

    root_call_id: UUID
    root_plan_digest: str
    attempts: tuple[RoutingSDKResult, ...] = field(repr=False)
    status: Literal["completed", "blocked"]
    reason: Literal["completed", "call_limit", "unsafe_outcome", "usage_unknown", "public_text_started", "tool_started"]
    product_adoption_permitted: Literal[False] = False


class _BoundHistory:
    """适配既有回退预留reader，并将独立完整观察与本轮实际SDK/PG消费交叉核对。"""

    def __init__(self, root: RoutingCallPlan, controls: RoutingFailoverControls, source: RoutingExecutionHistoryReader) -> None:
        self.root, self.controls, self.source = root, controls, source
        self.results: dict[UUID, RoutingSDKResult] = {}
        self.baseline: tuple[RoutingFailoverAttempt, ...] = ()
        self.reference: str | None = None

    async def observe(self, session: AsyncSession, *, pending: UUID | None = None) -> RoutingExecutionHistory:
        links = await read_routing_failover_links(session, root_plan=self.root)
        calls = (self.root.call_id, *(row.call_id for row in links))
        if set(self.results) != set(calls) or pending in calls or len(calls) > self.controls.max_physical_calls:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_CALLS_UNCONFIRMED")
        transaction, started = session.get_transaction(), _now()
        async with asyncio.timeout(1):
            observed = await self.source.observe(session, root_plan=self.root, controls=self.controls, pending_call_id=pending)
        if (transaction is None or session.get_transaction() is not transaction or not transaction.is_active
            or not isinstance(observed, RoutingExecutionHistory)):
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_HISTORY_UNCONFIRMED")
        observed = RoutingExecutionHistory.model_validate_json(observed.model_dump_json())
        if (observed.root_plan_digest != _digest(self.root)
            or not started <= observed.checked_at <= _now() < observed.expires_at <= self.root.expires_at
            or tuple(row.accounting.reservation.call_id for row in observed.attempts) != calls
            or observed.attempts[:len(self.baseline)] != self.baseline
            or len(observed.attempts) == len(self.baseline) and observed.source_reference_digest != self.reference):
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_HISTORY_CHANGED")
        for ordinal, row in enumerate(observed.attempts, 1):
            result = self.results[calls[ordinal - 1]]
            current = await read_routing_accounting(session, tenant_id=self.root.requirements.tenant_id,
                run_id=self.root.run_id, call_id=calls[ordinal - 1])
            if (result.accounting is None or row.root_call_id != self.root.call_id or row.ordinal != ordinal
                or row.accounting != current or result.accounting != current
                or result.reservation != current.reservation or result.consumption.record.reservation != current.reservation
                or current.accounting.receipt.dispatch_id != result.consumption.record.dispatch_id
                or current.accounting.receipt.dispatch_evidence_digest != _digest(result.consumption)
                or not result.io_stop_confirmed
                or result.outcome in {"completed", "contract_failure", "unknown"} and row.outcome != result.outcome
                or result.outcome == "completed" and result.answer is None
                or result.outcome == "failed" and row.outcome not in {
                    "transient_failure", "rate_limited", "authentication_failure", "contract_failure"}):
                raise RoutingCoordinationDenied("ROUTING_COORDINATION_SDK_HISTORY_MISMATCH")
        if _now() >= observed.expires_at:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_HISTORY_EXPIRED")
        self.baseline, self.reference = observed.attempts, observed.source_reference_digest
        return observed

    async def read(self, session: AsyncSession, *, request: RoutingFailoverReservationRequest) -> RoutingFailoverObservation:
        if request.root_plan != self.root or request.controls != self.controls:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_ROOT_CHANGED")
        observed = await self.observe(session, pending=request.call_id)
        if len(observed.attempts) != request.expected_attempt_count:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_HEAD_CHANGED")
        return RoutingFailoverObservation(root_plan_digest=observed.root_plan_digest,
            source_reference_digest=observed.source_reference_digest, attempts=observed.attempts,
            checked_at=observed.checked_at, expires_at=observed.expires_at)


class PostgresRoutingCoordinator:
    """只接受真实middleware作用域，一次驱动原计划；无默认批准或启用入口。

    首次保留原first_eligible_priority_v1选择规则，所选熔断不可用则停止，不补造失败
    跳过或重排原候选；后续由既有有限建议/原子预留选择，不重复同模型或重放工具。
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession], *, settings: Settings,
        authority: RoutingReservationAuthority | None = None, approval_reader: RoutingPreparedApprovalReader | None = None,
        boundary_reader: RoutingDataBoundaryReader | None = None, circuit_authority: RoutingCircuitAuthority | None = None,
        history_reader: RoutingExecutionHistoryReader | None = None, secret_provider: SecretProvider | None = None,
        candidate_policies: Mapping[UUID, OutboundHttpPolicy] | None = None, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        if (authority is None or approval_reader is None or boundary_reader is None or circuit_authority is None
            or secret_provider is None or candidate_policies is None
            or not 1 <= len(candidate_policies) <= 8
            or any(not isinstance(key, UUID) or not isinstance(value, OutboundHttpPolicy) for key, value in candidate_policies.items())):
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_DEPENDENCIES_UNAVAILABLE")
        self._factory, self._settings, self._authority = factory, settings, authority
        self._approval, self._boundary = approval_reader, boundary_reader
        self._circuit = PostgresRoutingObservedCircuitAuthority(approval_authority=circuit_authority)
        self._history = history_reader if history_reader is not None else PostgresRoutingExecutionHistoryReader(settings=settings, authority=authority)
        self._provider, self._policies, self._redaction = secret_provider, dict(candidate_policies), redaction_secrets
        self._store = PostgresRoutingCircuitStore(factory, settings=settings, authority=self._circuit,
            recovery_authority=authority, boundary_reader=boundary_reader)
        self._used = False
        self._calls: tuple[UUID, ...] = ()
        self._reserved: tuple[UUID, ...] = ()
        self._events: tuple[UUID, ...] = ()
        self._input: RoutingRuntimeInput | None = None

    @property
    def call_ids(self) -> tuple[UUID, ...]:
        """只供未知结果按原ID查询；登记ID不代表预留/派发已发生。"""
        return self._calls

    @property
    def circuit_event_ids(self) -> tuple[UUID, ...]:
        return self._events

    async def read(self, session: AsyncSession, *, plan: RoutingCallPlan) -> RoutingLogicalInput:
        """真实输入reader再加原Run账本排他性，不能换根/attempt重置物理次数。"""
        if self._input is None:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_INPUT_CLOSED")
        logical = await self._input.read(session, plan=plan)
        calls = tuple((await session.scalars(select(ModelCallLedger.id).where(
            ModelCallLedger.tenant_id == plan.requirements.tenant_id, ModelCallLedger.run_id == plan.run_id)
            .order_by(ModelCallLedger.call_number, ModelCallLedger.id).limit(4))).all())
        if calls != self._reserved:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_FOREIGN_CALL_PRESENT")
        return logical

    async def _bootstrap(self, input: RoutingRuntimeInput, root: RoutingCallPlan, controls: RoutingFailoverControls) -> None:
        if set(self._policies) != {row.candidate_id for row in root.candidates}:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_POLICIES_INCOMPLETE")
        async with self._factory() as session, session.begin():
            if session.get_bind().dialect.name != "postgresql":
                raise RoutingCoordinationDenied("ROUTING_COORDINATION_POSTGRES_REQUIRED")
            await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            await input.read(session, plan=root)
            authorization = await _authorization(session, plan=root, authority=self._authority, settings=self._settings)
            await _current_context(session, plan=root, settings=self._settings, authorization=authorization)
            policy = await session.scalar(select(ModelRoutingPolicyVersion).where(
                ModelRoutingPolicyVersion.id == root.requirements.policy_version_id,
                ModelRoutingPolicyVersion.tenant_id == root.requirements.tenant_id))
            if policy is None:
                raise RoutingCoordinationDenied("ROUTING_COORDINATION_POLICY_UNAVAILABLE")
            original = RoutingPolicyVersionRecord.model_validate(policy.payload)
            if (policy.policy_digest != _digest(original) or _digest(original) != root.requirements.policy_digest
                or original.definition.fallback_policy_digest != _digest(controls)):
                raise RoutingCoordinationDenied("ROUTING_COORDINATION_CONTROLS_CHANGED")
            prior = await session.scalar(select(ModelCallLedger.id).where(
                ModelCallLedger.tenant_id == root.requirements.tenant_id, ModelCallLedger.run_id == root.run_id).limit(1))
            if prior is not None or await session.scalar(select(ModelCallLedger.id).where(ModelCallLedger.id == root.call_id)) is not None:
                raise RoutingCoordinationDenied("ROUTING_COORDINATION_ROOT_ALREADY_RESERVED")

    def _scope(self, root: RoutingCallPlan, controls: RoutingFailoverControls, reservation: RoutingReservationRecord) -> RoutingCircuitScope:
        candidate = next((row for row in root.candidates if row.candidate_id == reservation.candidate_id), None)
        if candidate is None or _digest(candidate) != reservation.candidate_digest:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_CANDIDATE_CHANGED")
        return RoutingCircuitScope(tenant_id=root.requirements.tenant_id, policy_version_id=root.requirements.policy_version_id,
            policy_digest=root.requirements.policy_digest, candidate=candidate, controls_digest=_digest(controls))

    async def _entry(self, root: RoutingCallPlan, controls: RoutingFailoverControls,
        reservation: RoutingReservationRecord, link_digest: str | None, recovery_required: bool,
    ) -> RoutingDispatchRequest:
        scope = self._scope(root, controls, reservation)
        current = await self._store.read(RoutingCircuitRequest(action="read", scope=scope, controls=controls))
        recovery_id: UUID | None = None
        if current.snapshot.phase == "open" and current.snapshot.open_until is not None and _now() >= current.snapshot.open_until:
            recovery_id = uuid5(reservation.call_id, "routing-coordinator-recovery-v1")
            self._events = (*self._events, recovery_id)
            recovered = await self._store.append(RoutingCircuitRequest(action="claim_recovery", scope=scope,
                controls=controls, event_id=recovery_id, run_id=root.run_id, call_id=reservation.call_id,
                expected_sequence=current.snapshot.event_count, expected_event_digest=current.snapshot.last_event_digest,
                plan=root.model_copy(update={"call_id": reservation.call_id})))
            if not recovered.recovery_claimed or recovered.event is None or recovered.event.event.event_id != recovery_id:
                raise RoutingCoordinationDenied("ROUTING_COORDINATION_RECOVERY_UNCONFIRMED")
        elif current.snapshot.phase != "closed" or recovery_required:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_CIRCUIT_BLOCKED")
        return RoutingDispatchRequest(plan=root.model_copy(update={"call_id": reservation.call_id}), root_plan=root,
            controls=controls, reservation_digest=_digest(reservation), failover_link_digest=link_digest, recovery_event_id=recovery_id)

    async def _observe(self, input: RoutingRuntimeInput, history: _BoundHistory,
        controls: RoutingFailoverControls, result: RoutingSDKResult,
    ) -> RoutingExecutionHistory:
        root = input.plan
        async with self._factory() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            await self.read(session, plan=root)
            observed = await history.observe(session)
        scope = self._scope(root, controls, result.reservation)
        current = await self._store.read(RoutingCircuitRequest(action="read", scope=scope, controls=controls))
        if _now() >= observed.expires_at:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_HISTORY_EXPIRED")
        event_id = uuid5(result.reservation.call_id, "routing-coordinator-observation-v1")
        self._events = (*self._events, event_id)
        await self._store.append(RoutingCircuitRequest(action="observe", scope=scope, controls=controls,
            event_id=event_id, run_id=root.run_id, call_id=result.reservation.call_id,
            expected_sequence=current.snapshot.event_count, expected_event_digest=current.snapshot.last_event_digest,
            outcome=observed.attempts[-1].outcome))
        return observed

    async def execute(self, input: RoutingRuntimeInput, *, controls: RoutingFailoverControls) -> RoutingCoordinationResult:
        if self._used:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_ALREADY_ENTERED")
        self._used = True
        self._input = input
        try:
            root = input.plan
            controls = RoutingFailoverControls.model_validate_json(controls.model_dump_json())
            remaining = (root.expires_at - _now()).total_seconds()
            if remaining <= 0:
                raise RoutingCoordinationDenied("ROUTING_COORDINATION_EXPIRED")
            async with asyncio.timeout(remaining):
                await self._bootstrap(input, root, controls)
                return await self._execute(input, root, controls)
        except asyncio.CancelledError:
            raise
        except RoutingCoordinationDenied:
            raise
        except Exception:
            raise RoutingCoordinationDenied("ROUTING_COORDINATION_UNCONFIRMED") from None
        finally:
            self._input = None

    async def _execute(self, input: RoutingRuntimeInput, root: RoutingCallPlan,
        controls: RoutingFailoverControls,
    ) -> RoutingCoordinationResult:
        self._calls = (root.call_id,)
        history = _BoundHistory(root, controls, self._history)
        reservation = await reserve_routed_model_call(self._factory, plan=root, settings=self._settings,
            authority=self._authority, boundary_reader=self._boundary)
        self._reserved = (root.call_id,)
        results: list[RoutingSDKResult] = []
        link_digest: str | None = None
        recovery_required = False
        for ordinal in range(1, controls.max_physical_calls + 1):
            request = await self._entry(root, controls, reservation, link_digest, recovery_required)
            policy = self._policies[reservation.candidate_id]
            dispatch = PreparedRoutingDispatchAuthority(settings=self._settings, authority=self._authority,
                input_reader=self, approval_reader=self._approval, boundary_reader=self._boundary,
                policies={reservation.call_id: policy}, redaction_secrets=self._redaction)
            record = await claim_routing_dispatch(self._factory, request=request, settings=self._settings,
                authority=self._authority, dispatch_authority=dispatch, boundary_reader=self._boundary, circuit_authority=self._circuit)
            sdk = PostgresRoutingSDKExecutor(self._factory, settings=self._settings, input_reader=self,
                authority=self._authority, approval_reader=self._approval, boundary_reader=self._boundary,
                circuit_authority=self._circuit, secret_provider=self._provider, policy=policy, redaction_secrets=self._redaction)
            result = await sdk.execute(request=request, record=record)
            results.append(result)
            history.results[reservation.call_id] = result
            if sdk.accounting_unconfirmed or result.accounting is None:
                return RoutingCoordinationResult(root.call_id, _digest(root), tuple(results), "blocked", "usage_unknown")
            observed = await self._observe(input, history, controls, result)
            last = observed.attempts[-1]
            reason: Literal["completed", "call_limit", "unsafe_outcome", "usage_unknown", "public_text_started", "tool_started"]
            if last.public_text_emitted:
                reason = "public_text_started"
            elif last.business_tool_calls_started or last.tool_outcome != "none":
                reason = "tool_started"
            elif (result.accounting.accounting.status != "settled" or result.accounting.accounting.reservation_exceeded
                or result.accounting.accounting.error_code is not None or result.accounting.accounting.receipt.usage is None):
                reason = "usage_unknown"
            elif last.outcome == "completed" and result.answer is not None:
                reason = "completed"
            elif last.outcome not in {"transient_failure", "rate_limited"}:
                reason = "unsafe_outcome"
            elif ordinal >= controls.max_physical_calls:
                reason = "call_limit"
            else:
                next_id = uuid5(root.call_id, f"routing-coordinator-physical-{ordinal + 1}-v1")
                self._calls = (*self._calls, next_id)
                reservation = await reserve_routing_failover_call(self._factory, settings=self._settings,
                    request=RoutingFailoverReservationRequest(root_plan=root, controls=controls, call_id=next_id,
                        expected_attempt_count=ordinal, expected_link_digest=link_digest),
                    authority=self._authority, boundary_reader=self._boundary, history_reader=history, circuit_authority=self._circuit)
                self._reserved = (*self._reserved, next_id)
                async with self._factory() as session, session.begin():
                    await self.read(session, plan=root.model_copy(update={"call_id": next_id}))
                    links = await read_routing_failover_links(session, root_plan=root)
                    persisted = await read_routing_reservation(session, tenant_id=root.requirements.tenant_id,
                        run_id=root.run_id, call_id=next_id)
                    if (len(links) != ordinal or links[-1].call_id != next_id or persisted != reservation
                        or links[-1].controls_digest != _digest(controls)):
                        raise RoutingCoordinationDenied("ROUTING_COORDINATION_FAILOVER_CHANGED")
                    link_digest, recovery_required = _digest(links[-1]), links[-1].recovery_required
                continue
            return RoutingCoordinationResult(root.call_id, _digest(root), tuple(results),
                "completed" if reason == "completed" else "blocked", reason)
        raise RoutingCoordinationDenied("ROUTING_COORDINATION_LIMIT_REACHED")
