"""路由SDK最小观察保存与具体完整历史读取；无上传、重放或默认执行入口。"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SupportAnswer
from supportops_core.config import Settings
from supportops_core.enums import MessageRole
from supportops_core.model_routing_accounting_contracts import RoutingAccountingView, RoutingExecutionUsageReceipt
from supportops_core.model_routing_circuit_contracts import RoutingCircuitPermit, RoutingCircuitRequest
from supportops_core.model_routing_circuit_store import RoutingCircuitAuthority, _permit
from supportops_core.model_routing_coordination_contracts import RoutingExecutionHistory
from supportops_core.model_routing_dispatch_contracts import RoutingDispatchConsumption
from supportops_core.model_routing_dispatch_history import read_routing_dispatch_history
from supportops_core.model_routing_execution_contracts import RoutingObservedOutcome, RoutingSDKObservationRecord
from supportops_core.model_routing_failover_contracts import RoutingFailoverAttempt, RoutingFailoverControls
from supportops_core.model_routing_failover_reservations import read_routing_failover_links
from supportops_core.model_routing_policy_contracts import RoutingPolicyVersionRecord
from supportops_core.model_routing_request_contracts import bounded_request_json
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan, RoutingReservationRecord
from supportops_core.model_routing_reservations import (
    RoutingReservationAuthority, _authorization, _current_context, _existing_record, read_routing_reservation,
)
from supportops_core.model_routing_settlements import read_routing_accounting, settle_routed_model_call
from supportops_core.models import AgentRun, BusinessAction, Message, ModelCallLedger, ModelRoutingPolicyVersion, RunEvent, ToolInvocation
from supportops_core.provider_sdk_transport import ProviderSDKObservation
from supportops_core.redaction import redact_sensitive_text
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import lock_run_scope
from supportops_core.services import append_event
from supportops_core.specialist_response_services import _json_object
from supportops_core.specialist_sdk_response import (
    SDK_RESPONSE_BYTES_MAX, SpecialistSDKReply, _frames, parse_specialist_sdk_response,
)


class RoutingExecutionDenied(ValueError):
    def __init__(self, code: str = "ROUTING_EXECUTION_HISTORY_UNCONFIRMED") -> None:
        super().__init__("原路由执行观察当前不可确认，请只读查询原call，勿重新派发")
        self.code = code


def _digest(record: RoutingSDKObservationRecord) -> str:
    return manifest_digest(record.model_dump(mode="json"))


def _failure_code(raw: bytes, *, streaming: bool, model: str) -> Literal["server_error", "rate_limit_exceeded", "unclassified"]:
    """仅白名单、无输出的完整Responses失败；不据HTTP状态/错误文案/部分chunk重试。"""
    try:
        terminal: dict[str, object] | None = None
        if streaming:
            identifier: object = None
            done = False
            for event, content in _frames(raw):
                if done:
                    raise ValueError()
                if content == "[DONE]":
                    if terminal is None:
                        raise ValueError()
                    done = True
                    continue
                value = _json_object(content, bytes_limit=SDK_RESPONSE_BYTES_MAX)
                kind, response = value.get("type"), value.get("response")
                if (event and event != kind or terminal is not None or not isinstance(response, dict)
                    or kind not in {"response.created", "response.queued", "response.in_progress", "response.failed"}
                    or response.get("model") != model or response.get("output") != []):
                    raise ValueError()
                if identifier is None:
                    identifier = response.get("id")
                if not isinstance(identifier, str) or response.get("id") != identifier:
                    raise ValueError()
                if kind == "response.failed":
                    terminal = response
        else:
            terminal = _json_object(raw.decode("utf-8"), bytes_limit=SDK_RESPONSE_BYTES_MAX)
        if (terminal is None or terminal.get("model") != model or terminal.get("status") != "failed"
            or terminal.get("output") != [] or terminal.get("incomplete_details") is not None
            or not isinstance(terminal.get("error"), dict)):
            raise ValueError()
        error = terminal["error"]
        if (not isinstance(error, dict) or not isinstance(error.get("message"), str)
            or error.get("misalignment") is not None):
            return "unclassified"
        code = error.get("code") if isinstance(error, dict) else None
        if code == "server_error":
            return "server_error"
        if code == "rate_limit_exceeded":
            return "rate_limit_exceeded"
    except Exception:
        pass
    return "unclassified"


def describe_routing_sdk_observation(observation: ProviderSDKObservation, *, consumption: RoutingDispatchConsumption,
    responses: bool, streaming: bool, model: str, secrets: tuple[str, ...],
) -> tuple[RoutingSDKObservationRecord, SupportAnswer | None]:
    """直接解析本次SDK原始观察，不接受调用者提供的usage、回答或错误分类。"""
    reservation = consumption.record.reservation
    now = datetime.now(UTC)
    if (observation.started_at is not None and not consumption.consumed_at <= observation.started_at <= now
        or observation.finished_at is not None and (observation.started_at is None
            or not observation.started_at <= observation.finished_at <= now)
        or observation.raw_body is not None and (observation.started_at is None
            or observation.finished_at is None or len(observation.raw_body) > SDK_RESPONSE_BYTES_MAX)):
        raise RoutingExecutionDenied("ROUTING_EXECUTION_OBSERVATION_INVALID")
    reply = (parse_specialist_sdk_response(observation.raw_body, responses=responses, streaming=streaming, model=model)
        if observation.raw_body is not None else SpecialistSDKReply("unknown"))
    answer: SupportAnswer | None = None
    classification: RoutingObservedOutcome = "unknown"
    contract: Literal["accepted", "rejected", "not_available"] = "not_available"
    code: Literal["server_error", "rate_limit_exceeded", "unclassified", "none"] = "none"
    if reply.usage is not None and observation.finished_at is not None:
        if reply.outcome == "completed":
            try:
                if reply.answer_json is None or redact_sensitive_text(reply.answer_json, secrets=secrets) != reply.answer_json:
                    raise ValueError()
                bounded_request_json(reply.answer_json)
                answer = SupportAnswer.model_validate_json(reply.answer_json, strict=True)
                classification, contract = "completed", "accepted"
            except Exception:
                classification, contract = "contract_failure", "rejected"
        elif reply.outcome == "failed":
            code = _failure_code(observation.raw_body, streaming=streaming, model=model) if responses and observation.raw_body is not None else "unclassified"
            classification = "transient_failure" if code == "server_error" else "rate_limited" if code == "rate_limit_exceeded" else "contract_failure"
    when = datetime.now(UTC)
    observation_id = uuid5(reservation.call_id, "routing-sdk-observation-v1")
    receipt = None if observation.started_at is None else RoutingExecutionUsageReceipt(
        observation_id=observation_id, tenant_id=reservation.tenant_id, call_id=reservation.call_id,
        run_id=reservation.run_id, attempt_id=reservation.attempt_id, generation=reservation.generation,
        reservation_digest=manifest_digest(reservation.model_dump(mode="json")), actual_model_version_id=reservation.actual_model_version_id,
        credential_revision=reservation.credential_revision, model_input_digest=reservation.model_input_digest,
        dispatch_id=consumption.record.dispatch_id, dispatch_evidence_digest=manifest_digest(consumption.model_dump(mode="json")),
        outcome=reply.outcome, usage=reply.usage, started_at=observation.started_at, finished_at=observation.finished_at, observed_at=when)
    record = RoutingSDKObservationRecord(observation_id=observation_id, tenant_id=reservation.tenant_id,
        run_id=reservation.run_id, attempt_id=reservation.attempt_id, generation=reservation.generation,
        call_id=reservation.call_id, root_call_id=consumption.record.root_call_id, root_plan_digest=consumption.record.root_plan_digest,
        reservation_digest=manifest_digest(reservation.model_dump(mode="json")), consumption_digest=manifest_digest(consumption.model_dump(mode="json")),
        prepared_request_digest=consumption.record.prepared_request_digest, receipt=receipt,
        receipt_digest=manifest_digest(receipt.model_dump(mode="json")) if receipt is not None else None,
        response_digest=hashlib.sha256(observation.raw_body).hexdigest() if observation.raw_body is not None else None,
        provider_failure_code=code, classification=classification, answer_contract=contract,
        answer_digest=manifest_digest(answer.model_dump(mode="json")) if answer is not None else None,
        io_stop_confirmed=observation.io_stop_confirmed, observed_at=when)
    return record, answer


async def read_routing_sdk_observation(session: AsyncSession, *, reservation: RoutingReservationRecord) -> RoutingSDKObservationRecord:
    """内部授权调用者持原Run锁/一致快照；不恢复任何运行许可，不补造旧记录。"""
    events = (await session.scalars(select(RunEvent).where(RunEvent.tenant_id == reservation.tenant_id,
        RunEvent.run_id == reservation.run_id, RunEvent.attempt_id == reservation.attempt_id,
        RunEvent.event_type == "routing.sdk_observed", RunEvent.data["call_id"].as_string() == str(reservation.call_id))
        .order_by(RunEvent.sequence).limit(2))).all()
    try:
        if len(events) != 1:
            raise ValueError()
        event = events[0]
        if (set(event.data) != {"call_id", "observation", "observation_digest"}
            or len(json.dumps(event.data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 20000):
            raise ValueError()
        record = RoutingSDKObservationRecord.model_validate(event.data["observation"])
        dispatch = await read_routing_dispatch_history(session, reservation=reservation)
        consumed = dispatch.consumption
        if (consumed is None or event.data["observation_digest"] != _digest(record)
            or event.data["call_id"] != str(reservation.call_id) or record.call_id != reservation.call_id
            or record.tenant_id != reservation.tenant_id or record.run_id != reservation.run_id
            or record.attempt_id != reservation.attempt_id or record.generation != reservation.generation
            or record.reservation_digest != manifest_digest(reservation.model_dump(mode="json"))
            or record.consumption_digest != manifest_digest(consumed.model_dump(mode="json"))
            or record.root_call_id != consumed.record.root_call_id or record.root_plan_digest != consumed.record.root_plan_digest
            or record.prepared_request_digest != consumed.record.prepared_request_digest
            or not consumed.consumed_at <= record.observed_at <= event.created_at <= datetime.now(UTC)
            or record.receipt is not None and (record.receipt.dispatch_id != consumed.record.dispatch_id
                or record.receipt.started_at < consumed.consumed_at
                or record.receipt.actual_model_version_id != reservation.actual_model_version_id
                or record.receipt.credential_revision != reservation.credential_revision
                or record.receipt.model_input_digest != reservation.model_input_digest)):
            raise ValueError()
        return record
    except Exception:
        raise RoutingExecutionDenied() from None


async def _capture_routing_sdk_observation(factory: async_sessionmaker[AsyncSession], *,
    consumption: RoutingDispatchConsumption, observation: RoutingSDKObservationRecord,
) -> None:
    """只供实际SDK观察写入；原主体失效仍可记既有消费，未知写入绝不重放。"""
    reservation = consumption.record.reservation
    try:
        async with asyncio.timeout(8):
            async with factory() as session, session.begin():
                if session.get_bind().dialect.name != "postgresql":
                    raise ValueError()
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                await lock_run_scope(session, reservation.run_id, tenant_id=reservation.tenant_id)
                original = await read_routing_reservation(session, tenant_id=reservation.tenant_id,
                    run_id=reservation.run_id, call_id=reservation.call_id)
                dispatch = await read_routing_dispatch_history(session, reservation=original)
                prior = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == reservation.tenant_id,
                    RunEvent.run_id == reservation.run_id, RunEvent.event_type == "routing.sdk_observed",
                    RunEvent.data["call_id"].as_string() == str(reservation.call_id)).limit(1))
                if (observation.schema_version != "routing_sdk_observation_v2"
                    or prior is not None or original != reservation or dispatch.consumption != consumption
                    or observation.consumption_digest != manifest_digest(consumption.model_dump(mode="json"))):
                    raise ValueError()
                if len(observation.model_dump_json().encode("utf-8")) > 16384:
                    raise ValueError()
                await append_event(session, run_id=reservation.run_id, attempt_id=reservation.attempt_id,
                    event_type="routing.sdk_observed", data={"call_id": str(reservation.call_id),
                        "observation": observation.model_dump(mode="json"), "observation_digest": _digest(observation)})
                if await read_routing_sdk_observation(session, reservation=reservation) != observation:
                    raise ValueError()
    except Exception:
        raise RoutingExecutionDenied("ROUTING_EXECUTION_CAPTURE_UNCONFIRMED") from None


class PostgresRoutingUsageReader:
    async def read_usage(self, session: AsyncSession, *, reservation: RoutingReservationRecord) -> RoutingExecutionUsageReceipt | None:
        return (await read_routing_sdk_observation(session, reservation=reservation)).receipt


async def capture_and_settle_routing_sdk(factory: async_sessionmaker[AsyncSession], *,
    consumption: RoutingDispatchConsumption, observation: RoutingSDKObservationRecord,
) -> RoutingAccountingView | None:
    await _capture_routing_sdk_observation(factory, consumption=consumption, observation=observation)
    if observation.receipt is None:
        return None
    reservation = consumption.record.reservation
    return await settle_routed_model_call(factory, tenant_id=reservation.tenant_id, run_id=reservation.run_id,
        call_id=reservation.call_id, usage_reader=PostgresRoutingUsageReader())


class PostgresRoutingExecutionHistoryReader:
    """从持久实际SDK观察、原账本及当前事件/工具/产品事实读取完整有限历史。"""

    def __init__(self, *, settings: Settings, authority: RoutingReservationAuthority) -> None:
        self._settings, self._authority = settings, authority

    async def observe(self, session: AsyncSession, *, root_plan: RoutingCallPlan,
        controls: RoutingFailoverControls, pending_call_id: UUID | None,
    ) -> RoutingExecutionHistory:
        try:
            async with asyncio.timeout(1):
                return await self._observe(session, root_plan=root_plan, controls=controls, pending_call_id=pending_call_id)
        except RoutingExecutionDenied:
            raise
        except Exception:
            raise RoutingExecutionDenied() from None

    async def _observe(self, session: AsyncSession, *, root_plan: RoutingCallPlan,
        controls: RoutingFailoverControls, pending_call_id: UUID | None,
    ) -> RoutingExecutionHistory:
        transaction = session.get_transaction()
        if transaction is None or not transaction.is_active or session.get_bind().dialect.name != "postgresql":
            raise RoutingExecutionDenied()
        authorization = await _authorization(session, plan=root_plan, authority=self._authority, settings=self._settings)
        await _current_context(session, plan=root_plan, settings=self._settings, authorization=authorization)
        links = await read_routing_failover_links(session, root_plan=root_plan)
        calls = (root_plan.call_id, *(row.call_id for row in links))
        if len(calls) > controls.max_physical_calls or pending_call_id in calls or pending_call_id is not None and len(calls) >= controls.max_physical_calls:
            raise RoutingExecutionDenied()
        policy = await session.scalar(select(ModelRoutingPolicyVersion.payload).where(
            ModelRoutingPolicyVersion.tenant_id == root_plan.requirements.tenant_id,
            ModelRoutingPolicyVersion.id == root_plan.requirements.policy_version_id))
        original_policy = RoutingPolicyVersionRecord.model_validate(policy)
        if (manifest_digest(original_policy.model_dump(mode="json")) != root_plan.requirements.policy_digest
            or original_policy.definition.fallback_policy_digest != manifest_digest(controls.model_dump(mode="json"))):
            raise RoutingExecutionDenied()
        ledgers = tuple((await session.scalars(select(ModelCallLedger).where(
            ModelCallLedger.tenant_id == root_plan.requirements.tenant_id, ModelCallLedger.run_id == root_plan.run_id)
            .order_by(ModelCallLedger.call_number, ModelCallLedger.id).limit(4))).all())
        permitted = set(calls)
        if pending_call_id is not None:
            pending = next((row for row in ledgers if row.id == pending_call_id), None)
            if pending is not None:
                bound = await _existing_record(session, plan=root_plan.model_copy(update={"call_id": pending_call_id}))
                dispatch = await read_routing_dispatch_history(session, reservation=bound) if bound is not None else None
                if (bound is None or bound.selection_reason != "first_eligible_failover_v1" or pending.status != "reserved"
                    or pending.finished_at is not None or pending.error_code is not None or dispatch is None
                    or pending.settled_cost_microusd is not None or any(getattr(pending, key) is not None for key in (
                        "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"))
                    or dispatch.record is not None or dispatch.consumption is not None):
                    raise RoutingExecutionDenied()
                permitted.add(pending_call_id)
        if {row.id for row in ledgers} != permitted or len(ledgers) != len(permitted):
            raise RoutingExecutionDenied("ROUTING_EXECUTION_FOREIGN_CALL_PRESENT")
        # 账本集合之外的派发/观察也会阻断；不能忽略缺账本、错attempt或未消费的观察。
        original_events = await session.scalar(select(RunEvent.id).where(
            RunEvent.tenant_id == root_plan.requirements.tenant_id, RunEvent.run_id == root_plan.run_id,
            RunEvent.event_type.in_(("routing.dispatch_claimed", "routing.dispatch_consumed", "routing.sdk_observed")),
            or_(RunEvent.attempt_id.is_(None), RunEvent.attempt_id != root_plan.attempt_id,
                RunEvent.data["call_id"].as_string().is_(None),
                RunEvent.data["call_id"].as_string().not_in(tuple(str(call_id) for call_id in calls))))
            .limit(1))
        if original_events is not None:
            raise RoutingExecutionDenied("ROUTING_EXECUTION_FOREIGN_CALL_PRESENT")
        tool = await session.scalar(select(ToolInvocation.id).where(ToolInvocation.tenant_id == root_plan.requirements.tenant_id,
            ToolInvocation.run_id == root_plan.run_id).limit(1))
        action = await session.scalar(select(BusinessAction.id).where(BusinessAction.tenant_id == root_plan.requirements.tenant_id,
            or_(BusinessAction.run_id == root_plan.run_id, BusinessAction.execution_run_id == root_plan.run_id)).limit(1))
        if tool is not None or action is not None:
            raise RoutingExecutionDenied("ROUTING_EXECUTION_TOOL_ACTIVITY_PRESENT")
        payload = RunEvent.data["delta"].as_string()
        replacement = RunEvent.data["content"].as_string()
        text_event = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == root_plan.requirements.tenant_id,
            RunEvent.run_id == root_plan.run_id, or_(
                and_(RunEvent.event_type == "text.delta", or_(payload.is_(None), payload != "")),
                and_(RunEvent.event_type == "text.replace", or_(replacement.is_(None), replacement != "")))).limit(1))
        product = await session.scalar(select(Message.id).where(Message.tenant_id == root_plan.requirements.tenant_id,
            Message.conversation_id == root_plan.conversation_id, Message.role == MessageRole.ASSISTANT,
            Message.turn_sequence == select(AgentRun.input_sequence).where(AgentRun.id == root_plan.run_id,
                AgentRun.tenant_id == root_plan.requirements.tenant_id).scalar_subquery()).limit(1))
        attempts: list[RoutingFailoverAttempt] = []
        sources: list[str] = []
        for ordinal, call_id in enumerate(calls, 1):
            accounting = await read_routing_accounting(session, tenant_id=root_plan.requirements.tenant_id,
                run_id=root_plan.run_id, call_id=call_id)
            record = await read_routing_sdk_observation(session, reservation=accounting.reservation)
            if (record.root_call_id != root_plan.call_id or record.root_plan_digest != manifest_digest(root_plan.model_dump(mode="json"))
                or record.receipt != accounting.accounting.receipt or not record.io_stop_confirmed):
                raise RoutingExecutionDenied()
            attempts.append(RoutingFailoverAttempt(root_call_id=root_plan.call_id, ordinal=ordinal,
                accounting=accounting, outcome=record.classification, public_text_emitted=text_event is not None or product is not None,
                business_tool_calls_started=0, tool_outcome="none"))
            sources.extend((_digest(record), accounting.accounting_digest))
        checked = datetime.now(UTC)
        if session.get_transaction() is not transaction or not transaction.is_active or checked >= min(root_plan.expires_at, authorization.expires_at):
            raise RoutingExecutionDenied()
        return RoutingExecutionHistory(root_plan_digest=manifest_digest(root_plan.model_dump(mode="json")),
            source_reference_digest=manifest_digest(sources), attempts=tuple(attempts), checked_at=checked,
            expires_at=min(checked + timedelta(seconds=1), root_plan.expires_at, authorization.expires_at))


class PostgresRoutingObservedCircuitAuthority:
    """将独立熔断批准与原SDK持久分类交叉核对；本适配器不能生成批准。

    read/claim_recovery的当前治理、完整恢复历史及明确恢复预算仍由注入的独立
    authority核实；observe另须与当前PG费用和实际SDK记录完全相符。
    """

    def __init__(self, *, approval_authority: RoutingCircuitAuthority) -> None:
        self._approval = approval_authority

    async def _observed(self, session: AsyncSession, *, request: RoutingCircuitRequest,
        accounting: RoutingAccountingView | None,
    ) -> RoutingSDKObservationRecord | None:
        if request.action != "observe":
            if accounting is not None:
                raise RoutingExecutionDenied("ROUTING_EXECUTION_CIRCUIT_SCOPE_INVALID")
            return None
        if request.run_id is None or request.call_id is None or accounting is None:
            raise RoutingExecutionDenied("ROUTING_EXECUTION_CIRCUIT_SCOPE_INVALID")
        current = await read_routing_accounting(session, tenant_id=request.scope.tenant_id,
            run_id=request.run_id, call_id=request.call_id)
        reservation = current.reservation
        record = await read_routing_sdk_observation(session, reservation=reservation)
        if (current != accounting or reservation.policy_version_id != request.scope.policy_version_id
            or reservation.policy_digest != request.scope.policy_digest
            or reservation.candidate_digest != manifest_digest(request.scope.candidate.model_dump(mode="json"))
            or record.receipt != current.accounting.receipt or record.classification != request.outcome
            or not record.io_stop_confirmed):
            raise RoutingExecutionDenied("ROUTING_EXECUTION_CIRCUIT_OBSERVATION_MISMATCH")
        return record

    async def authorize(self, session: AsyncSession, *, request: RoutingCircuitRequest,
        accounting: RoutingAccountingView | None,
    ) -> RoutingCircuitPermit:
        transaction = session.get_transaction()
        if transaction is None or not transaction.is_active or session.get_bind().dialect.name != "postgresql":
            raise RoutingExecutionDenied()
        request = RoutingCircuitRequest.model_validate_json(request.model_dump_json())
        before = await self._observed(session, request=request, accounting=accounting)
        permit = await _permit(session, self._approval, request, accounting)
        after = await self._observed(session, request=request, accounting=accounting)
        if (session.get_transaction() is not transaction or not transaction.is_active
            or before != after or datetime.now(UTC) >= permit.expires_at):
            raise RoutingExecutionDenied("ROUTING_EXECUTION_CIRCUIT_OBSERVATION_MISMATCH")
        return permit
