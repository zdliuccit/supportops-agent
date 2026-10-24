"""独立路由执行集成的最终产品事务；无默认批准/checkpoint reader或Worker注册。"""

import asyncio
import json
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid5

from pydantic import BaseModel
from sqlalchemy import Text, and_, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.model_budget_services import apply_model_budget_summary, model_budget_summary
from supportops_core.model_routing_coordinator import RoutingCoordinationResult
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader, filter_routing_candidates
from supportops_core.model_routing_failover_contracts import RoutingFailoverControls
from supportops_core.model_routing_execution_source import read_routing_sdk_observation
from supportops_core.model_routing_product_contracts import (
    RoutingProductCheckpoint, RoutingProductCommitPermit, RoutingProductCommitRecord, RoutingProductCommitRequest,
)
from supportops_core.model_routing_request_contracts import ROUTING_PRODUCT_CONFLICT_EVENT_TYPES
from supportops_core.model_routing_request_services import read_routing_runtime_start, routing_business_activity_exists
from supportops_core.model_routing_reservations import RoutingReservationAuthority, _identity, read_routing_reservation
from supportops_core.model_routing_result_services import (
    PreparedRoutingSupportResult, RoutingResultDenied,
    assert_routing_product_unpublished, prepare_routing_support_result,
)
from supportops_core.model_routing_runtime_input import RoutingRuntimeInput
from supportops_core.models import AgentRun, Conversation, Message, RunAttempt, RunEvent
from supportops_core.product_message_services import persist_support_message
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import RunAttemptIdentity, owned_run, release_conversation
from supportops_core.services import append_event, content_digest, transition_run
from supportops_core.support_result_services import SupportResult

_PRODUCT_BYTES_MAX = 1_048_576


def _product_message_in_bounds() -> ColumnElement[bool]:
    """SQL先限原正文及结构化结果；不截断或解释超限材料。"""
    return and_(func.octet_length(Message.content) <= _PRODUCT_BYTES_MAX,
        func.octet_length(cast(Message.support_result, Text())) <= _PRODUCT_BYTES_MAX)


class RoutingProductAuthority(Protocol):
    """独立复查W24.1/W19/W23发布/产品采纳及当前主体/数据处理批准。

    必须查证原实际checkpoint与精确结果/历史，不据传入DTO/false字段自批；只读
    调用者事务，不提交/替换事务、取密或调用模型/工具。当前无默认提供者。
    """

    async def authorize(self, session: AsyncSession, *, request: RoutingProductCommitRequest,
    ) -> RoutingProductCommitPermit: ...


class RoutingProductCheckpointReader(Protocol):
    """只读核对实际完成的原attempt独占checkpoint及最终规范结果。

    须核对真实原输入、当前namespace/指针、原图执行已结束且不会继续写入、完整
    已结束物理历史和最终结构化结果，没有遗漏正文/工具；不写checkpoint、不从
    预算/传入摘要或时间补造完成事实。
    不提交调用者事务/联网调模型；返回完整当前证明，缺任何实际材料返回None。
    """

    async def read(self, session: AsyncSession, *, input: RoutingRuntimeInput,
        prepared: PreparedRoutingSupportResult,
    ) -> RoutingProductCheckpoint | None: ...


class RoutingProductUnconfirmed(RuntimeError):
    code = "ROUTING_PRODUCT_COMMIT_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("原路由产品提交结果暂不可确认，请只读查询原root call，勿重派或重提")


def _digest(value: BaseModel) -> str:
    return manifest_digest(value.model_dump(mode="json"))


def _now() -> datetime:
    return datetime.now(UTC)


async def commit_routing_support_result(
    factory: async_sessionmaker[AsyncSession], *, input: RoutingRuntimeInput,
    coordination: RoutingCoordinationResult, controls: RoutingFailoverControls, settings: Settings,
    reservation_authority: RoutingReservationAuthority | None = None,
    boundary_reader: RoutingDataBoundaryReader | None = None,
    product_authority: RoutingProductAuthority | None = None,
    checkpoint_reader: RoutingProductCheckpointReader | None = None,
    redaction_secrets: tuple[str, ...] = (),
) -> RoutingProductCommitRecord:
    """独立终结入口；不能作为仍须返回ModelResponse的原middleware执行回调。

    原输入仍存活、实际最终checkpoint已独立保存时才可调用；原图执行器不得在
    此方法完成后继续图节点/原Worker终结。当前没有默认集成或默认reader注册。
    缺依赖在建事务前拒绝；只写产品事实，不派发或重试模型/工具。
    """
    if (reservation_authority is None or boundary_reader is None
        or product_authority is None or checkpoint_reader is None):
        raise RoutingResultDenied("ROUTING_PRODUCT_DEPENDENCIES_UNAVAILABLE")
    root = input.plan
    input.claim_product_commit()
    wrote = False
    try:
        async with asyncio.timeout(min(8, max(0, (root.expires_at - _now()).total_seconds()))):
            async with factory() as session, session.begin():
                if session.get_bind().dialect.name != "postgresql":
                    raise RoutingResultDenied("ROUTING_PRODUCT_POSTGRES_REQUIRED")
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                prepared = await prepare_routing_support_result(session, input=input, coordination=coordination,
                    controls=controls, settings=settings, authority=reservation_authority,
                    boundary_reader=boundary_reader, redaction_secrets=redaction_secrets)
                owned = await owned_run(session, _identity(root))
                if (owned.attempt.side_effect_state != "none" or owned.run.input_sequence is None
                    or owned.attempt.checkpoint_id is not None):
                    raise RoutingResultDenied("ROUTING_PRODUCT_ATTEMPT_UNSAFE")
                existing = await session.scalar(select(Message.id).where(
                    Message.tenant_id == root.requirements.tenant_id,
                    Message.conversation_id == root.conversation_id, Message.role == MessageRole.ASSISTANT,
                    Message.turn_sequence == owned.run.input_sequence).limit(1))
                prior = await session.scalar(select(RunEvent.id).where(
                    RunEvent.tenant_id == root.requirements.tenant_id, RunEvent.run_id == root.run_id,
                    RunEvent.event_type == "routing.product_committed").limit(1))
                if existing is not None or prior is not None:
                    raise RoutingResultDenied("ROUTING_PRODUCT_ALREADY_RECORDED")
                transaction, started = session.get_transaction(), _now()
                async with asyncio.timeout(0.5):
                    checkpoint = await checkpoint_reader.read(session, input=input, prepared=prepared)
                if (not isinstance(checkpoint, RoutingProductCheckpoint)
                    or not started <= checkpoint.checked_at <= _now() < checkpoint.expires_at
                    or session.get_transaction() is not transaction or transaction is None or not transaction.is_active):
                    raise RoutingResultDenied("ROUTING_PRODUCT_CHECKPOINT_UNCONFIRMED")
                checkpoint = RoutingProductCheckpoint.model_validate_json(checkpoint.model_dump_json())
                if (checkpoint.checkpoint_thread_id != owned.attempt.checkpoint_thread_id
                    or checkpoint.input_sequence != owned.run.input_sequence):
                    raise RoutingResultDenied("ROUTING_PRODUCT_CHECKPOINT_MISMATCH")
                request = RoutingProductCommitRequest(operation_id=uuid5(root.call_id, "routing-product-commit-v1"),
                    message_id=uuid5(root.call_id, "routing-product-message-v1"), plan=root,
                    prepared=prepared.binding, checkpoint=checkpoint)
                started = _now()
                async with asyncio.timeout(0.5):
                    permit = await product_authority.authorize(session, request=request)
                if (not isinstance(permit, RoutingProductCommitPermit) or permit.request_digest != _digest(request)
                    or permit.actor_user_id != prepared.binding.actor_user_id
                    or not started <= permit.checked_at <= _now() < permit.expires_at
                    or permit.expires_at > min(root.expires_at, prepared.binding.expires_at, checkpoint.expires_at)
                    or session.get_transaction() is not transaction or not transaction.is_active):
                    raise RoutingResultDenied("ROUTING_PRODUCT_APPROVAL_UNCONFIRMED")
                permit = RoutingProductCommitPermit.model_validate_json(permit.model_dump_json())
                await input.read(session, plan=root)
                final = await read_routing_reservation(session, tenant_id=root.requirements.tenant_id,
                    run_id=root.run_id, call_id=prepared.binding.final_call_id)
                report = await filter_routing_candidates(session, requirements=root.requirements,
                    candidates=root.candidates, boundary_reader=boundary_reader)
                selected = next((row for row in report.candidates if row.candidate_id == final.candidate_id), None)
                if (selected is None or selected.status != "eligible" or selected.latest_probe_id is None
                    or selected.estimated_cost_microusd != final.reserved_cost_microusd
                    or not 0 <= (_now() - report.observed_at).total_seconds() <= 1):
                    raise RoutingResultDenied("ROUTING_PRODUCT_CANDIDATE_CHANGED")
                # 提供者看过的正文是可变对象，写入前重新核对全部准备摘要。
                if (_digest(prepared.answer) != prepared.binding.canonical_answer_digest
                    or _digest(prepared.support_result) != prepared.binding.support_result_digest
                    or _now() >= min(permit.expires_at, prepared.binding.expires_at, checkpoint.expires_at)):
                    raise RoutingResultDenied("ROUTING_PRODUCT_PREPARED_CHANGED")
                product = SupportResult.model_validate_json(prepared.support_result.model_dump_json())
                if _digest(product) != prepared.binding.support_result_digest:
                    raise RoutingResultDenied("ROUTING_PRODUCT_PREPARED_CHANGED")
                await assert_routing_product_unpublished(session, tenant_id=root.requirements.tenant_id, run_id=root.run_id)
                if _now() >= min(permit.expires_at, prepared.binding.expires_at, checkpoint.expires_at):
                    raise RoutingResultDenied("ROUTING_PRODUCT_PREPARED_CHANGED")
                if owned.attempt.context_snapshot is not None and product.evidence_conflicts:
                    snapshot = ContextSnapshot.model_validate(owned.attempt.context_snapshot)
                    owned.attempt.context_snapshot = snapshot.model_copy(update={
                        "conflicts": list(dict.fromkeys([*snapshot.conflicts, "KNOWLEDGE_EVIDENCE_CONFLICT"])),
                        "unresolved_issues": [*snapshot.unresolved_issues, "KNOWLEDGE_CONCLUSION_UNCONFIRMED"],
                    }).model_dump(mode="json")
                wrote = True
                await persist_support_message(session, owned=owned, result=product,
                    published_answer=None, message_id=request.message_id)
                readable = await session.scalar(select(_product_message_in_bounds()).where(
                    Message.id == request.message_id, Message.tenant_id == root.requirements.tenant_id,
                    Message.conversation_id == root.conversation_id))
                if readable is not True:
                    raise RoutingProductUnconfirmed()
                await append_event(session, run_id=root.run_id, attempt_id=root.attempt_id,
                    event_type="stage.completed", data={"stage": "agent.execute"})
                run, attempt, conversation = owned.run, owned.attempt, owned.conversation
                transition_run(run, RunStatus.COMPLETED)
                run.error_code = attempt.error_code = None
                finished = run.finished_at or _now()
                apply_model_budget_summary(run, await model_budget_summary(session,
                    tenant_id=root.requirements.tenant_id, run_id=root.run_id))
                run.tool_call_count = 0  # 完整历史已拒绝全Run任何工具/动作。
                run.finish_reason = run.provider_request_id = None
                run.time_to_first_token_ms = None  # 未观测客户端/模型首文本，不以最终replace时间补造。
                run.queue_latency_ms = _milliseconds(run.created_at, run.started_at)
                run.execution_latency_ms = _milliseconds(attempt.started_at, finished)
                run.end_to_end_latency_ms = _milliseconds(run.created_at, finished)
                attempt.status, attempt.finished_at, attempt.checkpoint_id = "completed", finished, checkpoint.checkpoint_id
                attempt.metrics = {**await model_budget_summary(session, tenant_id=root.requirements.tenant_id,
                    run_id=root.run_id, attempt_id=root.attempt_id), "tool_call_count": 0}
                conversation.checkpoint_thread_id, conversation.checkpoint_id = checkpoint.checkpoint_thread_id, checkpoint.checkpoint_id
                conversation.checkpoint_input_sequence = run.input_sequence
                release_conversation(owned)
                record = RoutingProductCommitRecord(operation_id=request.operation_id, message_id=request.message_id,
                    tenant_id=root.requirements.tenant_id, run_id=root.run_id, attempt_id=root.attempt_id,
                    generation=root.generation, root_call_id=root.call_id, final_call_id=prepared.binding.final_call_id,
                    actual_model_version_id=final.actual_model_version_id,
                    actor_user_id=permit.actor_user_id, root_plan_digest=_digest(root), prepared_digest=_digest(prepared.binding),
                    support_result_digest=prepared.binding.support_result_digest, checkpoint_digest=_digest(checkpoint),
                    checkpoint_thread_id=checkpoint.checkpoint_thread_id, checkpoint_id=checkpoint.checkpoint_id,
                    request_digest=_digest(request), permit_digest=_digest(permit), recorded_at=_now())
                await append_event(session, run_id=root.run_id, attempt_id=root.attempt_id,
                    event_type="run.completed", data={"status": "completed", "answer_ready": True,
                        "attempt_id": str(root.attempt_id), "generation": root.generation,
                        "agent_id": str(run.agent_id), "agent_version_id": str(run.agent_version_id),
                        "model_endpoint_version_id": str(run.model_endpoint_version_id),
                        "actual_model_endpoint_version_id": str(record.actual_model_version_id)})
                await append_event(session, run_id=root.run_id, attempt_id=root.attempt_id,
                    event_type="routing.product_committed", data={"root_call_id": str(root.call_id),
                        "record": record.model_dump(mode="json"), "record_digest": _digest(record)})
                if input.plan != root or _now() >= min(permit.expires_at, prepared.binding.expires_at, checkpoint.expires_at):
                    raise RoutingProductUnconfirmed()
            if input.plan != root or _now() >= min(permit.expires_at, prepared.binding.expires_at, checkpoint.expires_at):
                raise RoutingProductUnconfirmed()
            return record
    except BaseException as exc:
        if wrote:
            raise RoutingProductUnconfirmed() from None
        if isinstance(exc, (RoutingResultDenied, asyncio.CancelledError)):
            raise
        raise RoutingResultDenied("ROUTING_PRODUCT_UNAVAILABLE") from None


def _milliseconds(start: datetime | None, finish: datetime | None) -> int | None:
    if start is None or finish is None:
        return None
    return max(0, int((finish - start).total_seconds() * 1000))


async def read_routing_product_commit(session: AsyncSession, *, tenant_id: UUID, run_id: UUID,
    root_call_id: UUID,
) -> RoutingProductCommitRecord | None:
    """可信同租户读取者先授权/提供一致快照；不要求原Run仍running，不恢复运行许可。"""
    # 不按root/payload预过滤：另一root或损坏记录不能被忽略后报告本次完成。
    bounded_record = case((func.octet_length(cast(RunEvent.data, Text())) <= 16384,
        RunEvent.data), else_=None).label("data")
    events = (await session.execute(select(RunEvent.attempt_id, RunEvent.sequence, RunEvent.created_at,
        bounded_record).where(RunEvent.tenant_id == tenant_id, RunEvent.run_id == run_id,
        RunEvent.event_type == "routing.product_committed").order_by(RunEvent.sequence).limit(2))).all()
    if not events:
        return None
    try:
        if len(events) != 1:
            raise ValueError()
        event = events[0]
        if (not isinstance(event.data, dict) or set(event.data) != {"root_call_id", "record", "record_digest"}
            or event.data["root_call_id"] != str(root_call_id)):
            raise ValueError()
        if len(json.dumps(event.data, allow_nan=False).encode("utf-8")) > 16384:
            raise ValueError()
        record = RoutingProductCommitRecord.model_validate_json(json.dumps(event.data["record"], allow_nan=False))
        readable = _product_message_in_bounds()
        message = (await session.execute(select(Message.id, Message.tenant_id, Message.conversation_id,
            Message.user_id, Message.role, Message.turn_sequence, Message.content_hash, Message.idempotency_key,
            Message.created_at, case((readable, Message.content), else_=None).label("content"),
            case((readable, Message.support_result), else_=None).label("support_result"))
            .where(Message.id == record.message_id, Message.tenant_id == tenant_id))).one_or_none()
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id))
        attempt = await session.scalar(select(RunAttempt).where(RunAttempt.id == record.attempt_id,
            RunAttempt.run_id == run_id, RunAttempt.tenant_id == tenant_id))
        if (record.tenant_id != tenant_id or record.run_id != run_id or record.root_call_id != root_call_id
            or event.attempt_id != record.attempt_id or event.data["record_digest"] != _digest(record)
            or not record.recorded_at <= event.created_at <= _now()
            or run is None or run.status != RunStatus.COMPLETED or attempt is None or attempt.status != "completed"
            or run.error_code is not None or attempt.error_code is not None
            or run.finished_at is None or run.finished_at != attempt.finished_at
            or not attempt.started_at <= run.finished_at <= record.recorded_at
            or attempt.side_effect_state != "none" or attempt.replay_policy != "manual"
            or run.current_attempt_id != record.attempt_id or run.execution_generation != record.generation
            or attempt.generation != record.generation or attempt.checkpoint_id != record.checkpoint_id
            or attempt.checkpoint_thread_id != record.checkpoint_thread_id or message is None
            or message.tenant_id != tenant_id or message.conversation_id != run.conversation_id
            or message.role != MessageRole.ASSISTANT or message.turn_sequence != run.input_sequence
            or message.user_id != record.actor_user_id
            or message.idempotency_key != f"run:{run_id}:assistant"
            or not isinstance(message.content, str) or not isinstance(message.support_result, dict)
            or manifest_digest(message.support_result) != record.support_result_digest
            or message.content != message.support_result.get("answer") or message.content_hash != content_digest(message.content)):
            raise ValueError()
        conversation = (await session.execute(select(Conversation.user_id, Conversation.agent_id,
            Conversation.agent_version_id, Conversation.active_run_id, Conversation.active_attempt_id)
            .where(Conversation.id == run.conversation_id, Conversation.tenant_id == tenant_id))).one_or_none()
        if (conversation is None or conversation.user_id != record.actor_user_id
            or conversation.agent_id != run.agent_id or conversation.agent_version_id != run.agent_version_id
            or conversation.active_run_id == run.id or conversation.active_attempt_id == attempt.id
            or await routing_business_activity_exists(session, tenant_id=tenant_id, run_id=run_id)):
            raise ValueError()
        identity = RunAttemptIdentity(tenant_id=tenant_id, conversation_id=run.conversation_id,
            run_id=run_id, attempt_id=attempt.id, generation=attempt.generation, owner_id=attempt.owner_id)
        start = await read_routing_runtime_start(session, identity)
        if (start is None or start.root_call_id != root_call_id or start.root_plan_digest != record.root_plan_digest
            or start.actor_user_id != record.actor_user_id or start.agent_version_id != run.agent_version_id
            or start.input_sequence != run.input_sequence or start.checkpoint_thread_id != record.checkpoint_thread_id
            or not attempt.started_at <= start.created_at <= message.created_at
            or not record.recorded_at < start.expires_at):
            raise ValueError()
        result = SupportResult.model_validate(message.support_result)
        if result.run_id != run_id or result.attempt_id != record.attempt_id:
            raise ValueError()
        final = await read_routing_reservation(session, tenant_id=tenant_id, run_id=run_id, call_id=record.final_call_id)
        original = final if final.call_id == root_call_id else await read_routing_reservation(
            session, tenant_id=tenant_id, run_id=run_id, call_id=root_call_id)
        if original.plan_digest != start.root_plan_digest:
            raise ValueError()
        for reservation in (original, final):
            if (reservation.conversation_id != run.conversation_id or reservation.attempt_id != attempt.id
                or reservation.generation != record.generation or reservation.agent_version_id != run.agent_version_id
                or reservation.run_original_model_version_id != run.model_endpoint_version_id
                or reservation.model_input_digest != start.model_input_digest
                or reservation.context_snapshot_digest != start.context_snapshot_digest
                or not start.created_at <= reservation.recorded_at <= record.recorded_at):
                raise ValueError()
        observed = await read_routing_sdk_observation(session, reservation=final)
        if (final.actual_model_version_id != record.actual_model_version_id or final.attempt_id != record.attempt_id
            or observed.root_call_id != root_call_id or observed.root_plan_digest != record.root_plan_digest
            or observed.schema_version != "routing_sdk_observation_v2" or observed.classification != "completed"):
            raise ValueError()
        # 核对全Run产品及隔离/停止/拒绝冲突，不隐藏其他attempt或提交后的迟到事实。
        # 正常四条必须紧接原产品记录；只返回五条即可识别多余事件，SQL先限制负载大小。
        bounded_payload = case((func.octet_length(cast(RunEvent.data, Text())) <= _PRODUCT_BYTES_MAX,
            RunEvent.data), else_=None).label("data")
        final_events = (await session.execute(select(RunEvent.attempt_id, RunEvent.sequence, RunEvent.created_at,
            RunEvent.event_type, bounded_payload).where(RunEvent.tenant_id == tenant_id, RunEvent.run_id == run_id,
            RunEvent.event_type.in_(ROUTING_PRODUCT_CONFLICT_EVENT_TYPES))
            .order_by(RunEvent.sequence).limit(5))).all()
        if ([row.event_type for row in final_events] != ["text.replace", "result.ready", "stage.completed", "run.completed"]
            or any(row.attempt_id != record.attempt_id for row in final_events)
            or [row.sequence for row in final_events] != list(range(event.sequence - 4, event.sequence))
            or not attempt.started_at <= message.created_at <= final_events[0].created_at <= final_events[1].created_at
                <= final_events[2].created_at <= run.finished_at <= record.recorded_at
                <= final_events[3].created_at <= event.created_at
            or final_events[0].data != {"content": message.content}
            or final_events[1].data != {"message_id": str(message.id), "schema_version": "1"}
            or final_events[2].data != {"stage": "agent.execute"}
            or final_events[3].data != {"status": "completed", "answer_ready": True,
                "attempt_id": str(record.attempt_id), "generation": record.generation,
                "agent_id": str(run.agent_id), "agent_version_id": str(run.agent_version_id),
                "model_endpoint_version_id": str(run.model_endpoint_version_id),
                "actual_model_endpoint_version_id": str(record.actual_model_version_id)}):
            raise ValueError()
        return record
    except Exception:
        raise RoutingResultDenied("ROUTING_PRODUCT_HISTORY_UNCONFIRMED") from None
