"""有限回退的共享账本预留；原请求窗口不续期，不调用模型或消费恢复领取。"""

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.model_routing_circuit_contracts import RoutingCircuitRequest
from supportops_core.model_routing_circuit_store import RoutingCircuitAuthority, _history, _permit
from supportops_core.model_routing_contracts import RoutingCandidateFilterReport
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader, filter_routing_candidates
from supportops_core.model_routing_failover import propose_routing_failover, replay_routing_circuit
from supportops_core.model_routing_failover_contracts import RoutingCircuitScope, RoutingCircuitSnapshot, RoutingFailoverDecision
from supportops_core.model_routing_failover_reservation_contracts import (
    RoutingFailoverObservation, RoutingFailoverReservationLink, RoutingFailoverReservationRequest,
)
from supportops_core.model_routing_policy_contracts import RoutingPolicyVersionRecord
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan, RoutingReservationRecord
from supportops_core.model_routing_reservations import (
    RoutingReservationAuthority, RoutingReservationDenied, _reserve_routed_model_call, read_routing_reservation,
)
from supportops_core.model_routing_settlements import read_routing_accounting
from supportops_core.models import ModelRoutingPolicyVersion, RunEvent
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.services import append_event


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


def _now() -> datetime:
    return datetime.now(UTC)


class RoutingFailoverHistoryReader(Protocol):
    """可信执行源证明原逻辑请求的完整已结束尝试，无遗漏的在途派发/正文/工具。

    核实原派发及真实错误分类，不据错误文案或客户端布尔推断；attempts按原物理顺序
    返回，回执/用量必须来自既有账本。缺证明/未知状态拒绝，不填零。预留事务中的
    request.call_id尚未派发，不列入先前尝试，但也不得漏掉任何其他已预留/在途调用。
    source_reference_digest固定原派发事实引用，不含本次读取时间；不能更换根调用重置计数。
    只读当前事务，不取密、联网、调用模型或提交/替换事务。无默认实现/上传接口。
    """

    async def read(self, session: AsyncSession, *, request: RoutingFailoverReservationRequest,
    ) -> RoutingFailoverObservation: ...


async def read_routing_failover_links(session: AsyncSession, *, root_plan: RoutingCallPlan,
) -> tuple[RoutingFailoverReservationLink, ...]:
    """内部已授权读取者提供原Run锁或一致只读快照；过期仍可查，摘要不认证来源。"""
    root = await read_routing_reservation(session, tenant_id=root_plan.requirements.tenant_id,
        run_id=root_plan.run_id, call_id=root_plan.call_id)
    if root.plan_digest != _digest(root_plan) or root.selection_reason != "first_eligible_priority_v1":
        raise RoutingReservationDenied("ROUTING_FAILOVER_ROOT_INVALID")
    events = (await session.scalars(select(RunEvent).where(
        RunEvent.tenant_id == root.tenant_id, RunEvent.run_id == root.run_id, RunEvent.attempt_id == root.attempt_id,
        RunEvent.event_type == "budget.model_failover_linked",
        RunEvent.data["root_call_id"].as_string() == str(root.call_id)).order_by(RunEvent.sequence).limit(3))).all()
    result: list[RoutingFailoverReservationLink] = []
    candidates = {root.candidate_id}
    calls = {root.call_id}
    accounting_digests: list[str] = []
    last_number, last_time = root.call_number, root.recorded_at
    try:
        if len(events) > 2:
            raise ValueError()
        for ordinal, event in enumerate(events, 2):
            data = event.data
            link = RoutingFailoverReservationLink.model_validate_json(json.dumps(data["link"], allow_nan=False))
            record = await read_routing_reservation(session, tenant_id=root.tenant_id, run_id=root.run_id, call_id=link.call_id)
            previous_call = result[-1].call_id if result else root.call_id
            previous_usage = await read_routing_accounting(session, tenant_id=root.tenant_id,
                run_id=root.run_id, call_id=previous_call)
            accounting_digests.append(previous_usage.accounting_digest)
            physical = root_plan.model_copy(update={"call_id": link.call_id})
            if (set(data) != {"root_call_id", "call_id", "link", "link_digest"}
                or data["root_call_id"] != str(root.call_id) or data["call_id"] != str(link.call_id)
                or data["link_digest"] != _digest(link) or link.ordinal != ordinal
                or link.root_call_id != root.call_id or link.root_plan_digest != _digest(root_plan)
                or link.previous_call_id != (result[-1].call_id if result else root.call_id)
                or link.previous_link_digest != (_digest(result[-1]) if result else None)
                or link.reservation_digest != _digest(record) or record.plan_digest != _digest(physical)
                or record.selection_reason != "first_eligible_failover_v1" or record.candidate_id != link.candidate_id
                or record.candidate_id in candidates or record.call_id in calls or record.call_number <= last_number
                or record.filter_report_digest != link.selection_report_digest
                or link.accounting_digests != tuple(accounting_digests)
                or previous_usage.accounting.status != "settled" or previous_usage.accounting.reservation_exceeded
                or previous_usage.accounting.error_code is not None or previous_usage.accounting.receipt.usage is None
                or previous_usage.accounting.receipt.outcome != "failed"
                or not last_time <= record.recorded_at <= link.recorded_at < root_plan.expires_at
                or link.recorded_at > _now()
                or result and (link.controls_digest != result[0].controls_digest
                    or link.accounting_digests[:-1] != result[-1].accounting_digests)):
                raise ValueError()
            result.append(link)
            candidates.add(record.candidate_id)
            calls.add(record.call_id)
            last_number, last_time = record.call_number, link.recorded_at
    except (KeyError, TypeError, ValueError):
        raise RoutingReservationDenied("ROUTING_FAILOVER_LINK_HISTORY_INVALID") from None
    return tuple(result)


@dataclass(frozen=True)
class _State:
    links: tuple[RoutingFailoverReservationLink, ...]
    observation: RoutingFailoverObservation
    circuits: tuple[RoutingCircuitSnapshot, ...]
    decision: RoutingFailoverDecision


class _FailoverSelection:
    def __init__(self, request: RoutingFailoverReservationRequest, *, reader: RoutingFailoverHistoryReader,
        circuit_authority: RoutingCircuitAuthority, boundary_reader: RoutingDataBoundaryReader,
    ) -> None:
        self.request, self.reader = request, reader
        self.circuit_authority, self.boundary_reader = circuit_authority, boundary_reader
        self.initial: _State | None = None
        self.report: RoutingCandidateFilterReport | None = None
        self.recorded = False

    async def _state(self, session: AsyncSession, report: RoutingCandidateFilterReport) -> _State:
        request, root = self.request, self.request.root_plan
        links = await read_routing_failover_links(session, root_plan=root)
        if (len(links) + 1 != request.expected_attempt_count
            or (_digest(links[-1]) if links else None) != request.expected_link_digest
            or links and links[-1].controls_digest != _digest(request.controls)):
            raise RoutingReservationDenied("ROUTING_FAILOVER_HEAD_CHANGED")
        calls = (root.call_id, *(row.call_id for row in links))
        accounting = tuple([await read_routing_accounting(session, tenant_id=root.requirements.tenant_id,
            run_id=root.run_id, call_id=call_id) for call_id in calls])
        for link in links:
            if link.accounting_digests != tuple(row.accounting_digest for row in accounting[:link.ordinal - 1]):
                raise RoutingReservationDenied("ROUTING_FAILOVER_ACCOUNTING_CHANGED")
        policy_row = await session.scalar(select(ModelRoutingPolicyVersion).where(
            ModelRoutingPolicyVersion.id == root.requirements.policy_version_id,
            ModelRoutingPolicyVersion.tenant_id == root.requirements.tenant_id))
        if policy_row is None:
            raise RoutingReservationDenied("ROUTING_FAILOVER_POLICY_UNAVAILABLE")
        policy = RoutingPolicyVersionRecord.model_validate_json(json.dumps(policy_row.payload, allow_nan=False))
        if policy_row.policy_digest != _digest(policy):
            raise RoutingReservationDenied("ROUTING_FAILOVER_POLICY_CHANGED")
        circuits: list[RoutingCircuitSnapshot] = []
        for candidate in root.candidates:
            scope = RoutingCircuitScope(tenant_id=root.requirements.tenant_id, policy_version_id=policy.version_id,
                policy_digest=root.requirements.policy_digest, candidate=candidate, controls_digest=_digest(request.controls))
            reading = RoutingCircuitRequest(action="read", scope=scope, controls=request.controls)
            permit = await _permit(session, self.circuit_authority, reading)
            history = await _history(session, reading)
            if _now() >= permit.expires_at:
                raise RoutingReservationDenied("ROUTING_FAILOVER_CIRCUIT_READ_EXPIRED")
            circuits.append(replay_routing_circuit(scope=scope, controls=request.controls,
                events=tuple(row.event for row in history), now=_now()))
        started, transaction = _now(), session.get_transaction()
        async with asyncio.timeout(1):
            observation = await self.reader.read(session, request=request)
        if (transaction is None or session.get_transaction() is not transaction or not transaction.is_active
            or not isinstance(observation, RoutingFailoverObservation)):
            raise RoutingReservationDenied("ROUTING_FAILOVER_OBSERVATION_UNCONFIRMED")
        observation = RoutingFailoverObservation.model_validate_json(observation.model_dump_json())
        if (observation.root_plan_digest != _digest(root)
            or not started <= observation.checked_at <= _now() < observation.expires_at <= root.expires_at
            or tuple(row.accounting.reservation.call_id for row in observation.attempts) != calls
            or tuple(_digest(row.accounting) for row in observation.attempts) != tuple(_digest(row) for row in accounting)):
            raise RoutingReservationDenied("ROUTING_FAILOVER_OBSERVATION_MISMATCH")
        decision = propose_routing_failover(plan=root, policy=policy, controls=request.controls,
            eligibility=report, circuits=tuple(circuits), attempts=observation.attempts, now=_now())
        if decision.status != "proposed" or decision.candidate_id is None:
            raise RoutingReservationDenied("ROUTING_FAILOVER_BLOCKED")
        return _State(links=links, observation=observation, circuits=tuple(circuits), decision=decision)

    async def choose(self, session: AsyncSession, *, plan: RoutingCallPlan,
        report: RoutingCandidateFilterReport,
    ) -> UUID:
        if self.initial is not None or self.recorded or plan != self.request.root_plan.model_copy(update={"call_id": self.request.call_id}):
            raise RoutingReservationDenied("ROUTING_FAILOVER_SELECTION_REUSED")
        self.initial = await self._state(session, report)
        self.report = report
        if self.initial.decision.candidate_id is None:
            raise RoutingReservationDenied("ROUTING_FAILOVER_BLOCKED")
        return self.initial.decision.candidate_id

    async def record(self, session: AsyncSession, *, reservation: RoutingReservationRecord) -> None:
        if self.initial is None or self.report is None or self.recorded or reservation.call_id != self.request.call_id:
            raise RoutingReservationDenied("ROUTING_FAILOVER_SELECTION_REUSED")
        self.recorded = True
        root = self.request.root_plan
        report = await filter_routing_candidates(session, requirements=root.requirements, candidates=root.candidates,
            boundary_reader=self.boundary_reader)
        final = await self._state(session, report)
        before, after = self.initial.observation, final.observation
        selected = next((row for row in report.candidates if row.candidate_id == reservation.candidate_id), None)
        if (final.decision.candidate_id != reservation.candidate_id
            or final.decision.recovery_trial_required != self.initial.decision.recovery_trial_required
            or before.attempts != after.attempts or before.source_reference_digest != after.source_reference_digest
            or selected is None or selected.estimated_cost_microusd != reservation.reserved_cost_microusd):
            raise RoutingReservationDenied("ROUTING_FAILOVER_SELECTION_CHANGED")
        link = RoutingFailoverReservationLink(root_call_id=root.call_id, root_plan_digest=_digest(root),
            call_id=reservation.call_id, ordinal=self.request.expected_attempt_count + 1,
            previous_call_id=final.links[-1].call_id if final.links else root.call_id,
            previous_link_digest=self.request.expected_link_digest, reservation_digest=_digest(reservation),
            controls_digest=_digest(self.request.controls), source_reference_digest=after.source_reference_digest,
            observation_digest=_digest(after), accounting_digests=tuple(row.accounting.accounting_digest for row in after.attempts),
            selection_report_digest=_digest(self.report), confirmation_report_digest=_digest(report),
            decision_digest=_digest(final.decision), circuit_heads_digest=manifest_digest([
                {"scope_digest": row.scope_digest, "sequence": row.event_count, "last_event_digest": row.last_event_digest}
                for row in sorted(final.circuits, key=lambda row: str(row.scope.candidate.candidate_id))]),
            candidate_id=reservation.candidate_id, recovery_required=final.decision.recovery_trial_required, recorded_at=_now())
        await append_event(session, run_id=root.run_id, attempt_id=root.attempt_id,
            event_type="budget.model_failover_linked", data={"root_call_id": str(root.call_id),
                "call_id": str(reservation.call_id), "link": link.model_dump(mode="json"), "link_digest": _digest(link)})
        if _now() >= after.expires_at:
            raise RoutingReservationDenied("ROUTING_FAILOVER_OBSERVATION_EXPIRED")


async def reserve_routing_failover_call(session_factory: async_sessionmaker[AsyncSession], *,
    request: RoutingFailoverReservationRequest, settings: Settings,
    authority: RoutingReservationAuthority | None = None, boundary_reader: RoutingDataBoundaryReader | None = None,
    history_reader: RoutingFailoverHistoryReader | None = None, circuit_authority: RoutingCircuitAuthority | None = None,
) -> RoutingReservationRecord:
    """独立显式入口，默认关闭；未知仅查原call/原链，不重试、续窗或退额。

    预留与链记录同PG事务；恢复候选还须另行唯一领取，再由未来可信派发者复检。
    当前没有默认提供者、SDK/Worker注册或执行许可，也不证明RunEvent的DDL隔离。
    """
    request = RoutingFailoverReservationRequest.model_validate_json(request.model_dump_json())
    if authority is None or boundary_reader is None or history_reader is None or circuit_authority is None:
        raise RoutingReservationDenied("ROUTING_FAILOVER_DEPENDENCIES_UNAVAILABLE")
    selection = _FailoverSelection(request, reader=history_reader, circuit_authority=circuit_authority,
        boundary_reader=boundary_reader)
    return await _reserve_routed_model_call(session_factory,
        plan=request.root_plan.model_copy(update={"call_id": request.call_id}), settings=settings,
        authority=authority, boundary_reader=boundary_reader, selection=selection)
