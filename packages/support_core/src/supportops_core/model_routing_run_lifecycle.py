"""原领取者的路由停止事务；只收敛原入口，不恢复、重派或采纳未知产品。"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid5

from sqlalchemy import Text, and_, case, cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.enums import MessageRole, RunStatus
from supportops_core.model_budget_services import apply_model_budget_summary, model_budget_summary
from supportops_core.model_routing_product_contracts import RoutingProductCommitRecord
from supportops_core.model_routing_product_services import read_routing_product_commit
from supportops_core.model_routing_request_contracts import (
    ROUTING_OUTCOME_EVENT_TYPES, RoutingExecutionOutcomeView, RoutingRuntimeStart,
)
from supportops_core.model_routing_request_services import (
    read_routing_runtime_start, read_routing_worker_start, routing_business_activity_exists,
)
from supportops_core.models import AgentRun, AgentVersion, Conversation, Message, ModelCallLedger, RunAttempt, RunEvent
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import (
    OwnedRun, RunAttemptIdentity, RunLeaseLostError, close_unsettled_tools, lock_run_scope, owned_run, release_conversation,
)
from supportops_core.runtime_errors import runtime_error_message
from supportops_core.services import ResourceNotFoundError, append_event, transition_run

RoutingStopReason = Literal["cancelled", "lease_lost", "execution_failed", "checkpoint_unconfirmed", "product_unconfirmed",
    "worker_unavailable", "worker_binding_invalid"]
_CODES: dict[str, str] = {
    "cancelled": "ROUTING_EXECUTION_CANCELLED",
    "lease_lost": "ROUTING_EXECUTION_LEASE_LOST",
    "execution_failed": "ROUTING_EXECUTION_FAILED",
    "checkpoint_unconfirmed": "ROUTING_CHECKPOINT_CAPTURE_UNCONFIRMED",
    "product_unconfirmed": "ROUTING_PRODUCT_COMMIT_UNCONFIRMED",
    "worker_unavailable": "ROUTING_WORKER_UNAVAILABLE",
    "worker_binding_invalid": "ROUTING_WORKER_BINDING_INVALID",
}
_EXECUTION_EVENTS = ("routing.execution_started", "routing.execution_stopped", "routing.dispatch_claimed",
    "routing.dispatch_consumed", "routing.sdk_observed", "budget.model_reserved", "budget.model_failover_linked")
_STOP_OUTCOME_EVENTS = tuple(kind for kind in ROUTING_OUTCOME_EVENT_TYPES
    if kind not in ("text.delta", "text.replace"))
_REJECTION_OUTCOME_EVENTS = ROUTING_OUTCOME_EVENT_TYPES


@dataclass(frozen=True, slots=True)
class RoutingOwnedRunResult:
    """内部收敛回执；status不是Run enum，not_started/fenced/unconfirmed不确认终态。"""

    status: Literal["completed", "failed", "cancelled", "quarantined", "fenced", "not_started", "unconfirmed"]
    code: str | None = None
    root_call_id: UUID | None = None
    product: RoutingProductCommitRecord | None = field(default=None, repr=False)


class RoutingOutcomeUnavailable(RuntimeError):
    code = "ROUTING_OUTCOME_UNAVAILABLE"

    def __init__(self) -> None:
        super().__init__("原路由结果暂不可查证，请稍后只读查询原Run/attempt，勿重新执行")


async def read_routing_execution_outcome(session: AsyncSession, *, tenant_id: UUID, run_id: UUID,
    attempt_id: UUID,
) -> RoutingExecutionOutcomeView:
    """调用者先复核管理员，且必须提供PG一致只读事务；本入口永不调用停止/恢复。"""
    transaction = session.get_transaction()
    if (session.get_bind().dialect.name != "postgresql" or transaction is None or not transaction.is_active
        or session.new or session.dirty or session.deleted
        or await session.scalar(text("SHOW transaction_isolation")) != "repeatable read"
        or await session.scalar(text("SHOW transaction_read_only")) != "on"):
        raise RoutingOutcomeUnavailable()
    run = await session.scalar(select(AgentRun).join(AgentVersion, AgentVersion.id == AgentRun.agent_version_id)
        .where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id, AgentVersion.tenant_id == tenant_id,
            AgentVersion.agent_id == AgentRun.agent_id, AgentVersion.schema_version == "3"))
    attempt = await session.scalar(select(RunAttempt).where(RunAttempt.id == attempt_id,
        RunAttempt.tenant_id == tenant_id, RunAttempt.run_id == run_id)) if run is not None else None
    entry = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == tenant_id,
        RunEvent.run_id == run_id, RunEvent.event_type.in_(("routing.worker_started", "routing.execution_started",
            "routing.execution_stopped", "routing.request_rejected", "routing.product_committed"))).limit(1)) if attempt is not None else None
    if run is None or attempt is None or entry is None:
        raise ResourceNotFoundError("原路由执行记录不存在")
    result = RoutingOwnedRunResult("unconfirmed", "ROUTING_EXECUTION_OUTCOME_UNCONFIRMED")
    root: UUID | None = None
    try:
        conversation = await session.scalar(select(Conversation).where(Conversation.id == run.conversation_id,
            Conversation.tenant_id == tenant_id))
        if (conversation is None or run.current_attempt_id != attempt.id
            or run.execution_generation != attempt.generation):
            raise ValueError("ROUTING_EXECUTION_SCOPE_CHANGED")
        identity = RunAttemptIdentity(tenant_id=tenant_id, conversation_id=run.conversation_id,
            run_id=run_id, attempt_id=attempt_id, generation=attempt.generation, owner_id=attempt.owner_id)
        worker = await read_routing_worker_start(session, attempt=identity)
        if worker is not None and (worker.agent_version_id != run.agent_version_id or worker.config_digest != run.config_digest):
            raise ValueError("ROUTING_WORKER_START_HISTORY_INVALID")
        owned = OwnedRun(conversation=conversation, run=run, attempt=attempt)
        start = await read_routing_runtime_start(session, identity)
        if start is None:
            rejected = await _read_request_rejection(session, identity=identity, owned=owned)
            if rejected is not None:
                result = rejected
        else:
            if (start.agent_version_id != run.agent_version_id or start.input_sequence != run.input_sequence
                or start.actor_user_id != conversation.user_id or start.checkpoint_thread_id != attempt.checkpoint_thread_id
                or attempt.replay_policy != "manual"):
                raise ValueError("ROUTING_RUNTIME_START_HISTORY_INVALID")
            root = start.root_call_id
            if run.status == RunStatus.COMPLETED:
                product = await read_routing_product_commit(session, tenant_id=tenant_id, run_id=run_id, root_call_id=root)
                if product is None or product.root_plan_digest != start.root_plan_digest:
                    raise ValueError("ROUTING_PRODUCT_HISTORY_UNCONFIRMED")
                result = RoutingOwnedRunResult("completed", root_call_id=root)
            else:
                stopped = await _read_execution_stop(session, identity=identity, owned=owned, start=start)
                if stopped is not None:
                    result = stopped
    except (ValueError, TypeError, KeyError, AttributeError, ResourceNotFoundError):
        # 损坏/不完整历史只报告未确认；不能落入会写入的停止事务。
        result = RoutingOwnedRunResult("unconfirmed", "ROUTING_EXECUTION_OUTCOME_UNCONFIRMED", root)
    if (session.get_transaction() is not transaction or not transaction.is_active or session.new or session.dirty or session.deleted
        or await session.scalar(text("SHOW transaction_read_only")) != "on"):
        raise RoutingOutcomeUnavailable()
    return RoutingExecutionOutcomeView.model_validate({"run_id": run_id, "attempt_id": attempt_id,
        "generation": attempt.generation, "status": result.status, "code": result.code,
        "root_call_id": root, "observed_at": datetime.now(UTC)})


@dataclass(frozen=True, slots=True)
class _RoutingTextState:
    latest_sequence: int | None
    unclear: bool
    cleared: bool


@dataclass(slots=True)
class _RoutingStopCommitGuard:
    """仅新写入绑定原租约；只读历史不能继承或续期运行许可。"""

    lease_expires_at: datetime | None = None
    deadline: float | None = None

    def protect(self, lease_expires_at: datetime) -> None:
        if self.lease_expires_at is not None:
            raise ValueError("ROUTING_EXECUTION_STOP_GUARD_ALREADY_BOUND")
        lease = lease_expires_at.replace(tzinfo=UTC) if lease_expires_at.tzinfo is None else lease_expires_at
        started = asyncio.get_running_loop().time()
        remaining = (lease - datetime.now(UTC)).total_seconds()
        self.lease_expires_at = lease
        self.deadline = started + max(0, remaining)
        if self.expired():
            raise RunLeaseLostError("原停止写入许可已超过租约")

    def expired(self) -> bool:
        if self.lease_expires_at is None:
            return False
        return (self.deadline is None or datetime.now(UTC) >= self.lease_expires_at
            or asyncio.get_running_loop().time() >= self.deadline)


@asynccontextmanager
async def _stop_transaction(factory: async_sessionmaker[AsyncSession], guard: _RoutingStopCommitGuard,
) -> AsyncIterator[AsyncSession]:
    """覆盖事务内提前返回；提交前过期回滚，提交后越界仅未确认，不重提。"""
    async with factory() as session:
        async with session.begin():
            yield session
            if guard.expired():
                raise RunLeaseLostError("原停止事务提交前已超过租约")
    if guard.expired():
        # 事务/连接退出后才判断；不能把可能已提交的终态谎称已回滚或自动重写。
        raise ValueError("ROUTING_EXECUTION_STOP_COMMIT_UNCONFIRMED")


async def _read_text_state(session: AsyncSession, identity: RunAttemptIdentity) -> _RoutingTextState:
    """调用者持原PG Run锁；仅查询序号/SQL判定，不读取正文或截断有问题的历史。"""
    scope = (RunEvent.tenant_id == identity.tenant_id, RunEvent.run_id == identity.run_id,
        RunEvent.event_type.in_(("text.delta", "text.replace")))
    payload = cast(RunEvent.data, JSONB)
    valid = or_(
        and_(RunEvent.event_type == "text.delta", func.jsonb_typeof(payload["delta"]) == "string",
            payload == func.jsonb_build_object("delta", payload["delta"])),
        and_(RunEvent.event_type == "text.replace", func.jsonb_typeof(payload["content"]) == "string",
            payload == func.jsonb_build_object("content", payload["content"])))
    latest = (await session.execute(select(RunEvent.sequence,
        and_(RunEvent.event_type == "text.replace", payload == func.jsonb_build_object("content", ""))
            .label("cleared")).where(*scope).order_by(RunEvent.sequence.desc()).limit(1))).one_or_none()
    unclear = await session.scalar(select(RunEvent.id).where(*scope,
        or_(RunEvent.attempt_id.is_(None), RunEvent.attempt_id != identity.attempt_id,
            valid.is_not(True))).limit(1))
    return _RoutingTextState(latest.sequence if latest is not None else None,
        unclear is not None, latest is not None and latest.cleared is True)


async def _read_execution_stop(session: AsyncSession, *, identity: RunAttemptIdentity,
    owned: OwnedRun, start: RoutingRuntimeStart,
) -> RoutingOwnedRunResult | None:
    """原锁内只读核对停止及相邻终态；不续租、不补写或释放后续许可。"""
    payload = case((func.octet_length(cast(RunEvent.data, Text())) <= 4096,
        RunEvent.data), else_=None).label("data")
    records = (await session.execute(select(RunEvent.attempt_id, RunEvent.sequence, RunEvent.created_at, payload)
        .where(RunEvent.tenant_id == identity.tenant_id, RunEvent.run_id == identity.run_id,
            RunEvent.event_type == "routing.execution_stopped").order_by(RunEvent.sequence).limit(2))).all()
    if not records:
        return None
    if len(records) != 1:
        raise ValueError("ROUTING_EXECUTION_STOP_HISTORY_INVALID")
    record, run, attempt, conversation = records[0], owned.run, owned.attempt, owned.conversation
    data = record.data
    if (not isinstance(data, dict) or set(data) != {"root_call_id", "start_digest", "status", "code"}
        or record.attempt_id != identity.attempt_id or data["root_call_id"] != str(start.root_call_id)
        or data["start_digest"] != manifest_digest(start.model_dump(mode="json"))
        or data["code"] not in (*_CODES.values(), "ROUTING_EXECUTION_RECONCILIATION_REQUIRED")
        or data["status"] not in ("failed", "cancelled", "quarantined")
        or attempt.status != data["status"] or attempt.error_code != data["code"]
        or attempt.finished_at is None or not start.created_at <= attempt.finished_at <= record.created_at <= datetime.now(UTC)
        or attempt.checkpoint_id is not None or conversation.checkpoint_thread_id == attempt.checkpoint_thread_id
        or conversation.checkpoint_input_sequence == run.input_sequence):
        raise ValueError("ROUTING_EXECUTION_STOP_HISTORY_INVALID")
    status, code = data["status"], data["code"]
    product = await session.scalar(select(Message.id).where(Message.tenant_id == identity.tenant_id,
        Message.conversation_id == identity.conversation_id, Message.turn_sequence == run.input_sequence,
        Message.role == MessageRole.ASSISTANT).limit(1))
    if product is not None:
        raise ValueError("ROUTING_EXECUTION_STOP_HISTORY_INVALID")
    if code == "ROUTING_EXECUTION_RECONCILIATION_REQUIRED":
        if (status != "quarantined" or run.status not in (RunStatus.RUNNING, RunStatus.CANCELLING)
            or run.recovery_reason != code or run.finished_at is not None
            or conversation.active_run_id != run.id or conversation.active_attempt_id != attempt.id):
            raise ValueError("ROUTING_EXECUTION_STOP_HISTORY_INVALID")
        terminal_type, terminal = "run.recovery_blocked", {"code": code}
    else:
        checkpoint_unknown = code in (_CODES["checkpoint_unconfirmed"], _CODES["product_unconfirmed"])
        if (status == "quarantined" and not checkpoint_unknown
            or status == "failed" and (checkpoint_unknown or code == _CODES["cancelled"])
            or run.status != (RunStatus.CANCELLED if status == "cancelled" else RunStatus.FAILED)
            or run.error_code != (None if status == "cancelled" else code)
            or run.finished_at != attempt.finished_at
            or conversation.active_run_id == run.id or conversation.active_attempt_id == attempt.id):
            raise ValueError("ROUTING_EXECUTION_STOP_HISTORY_INVALID")
        state = await _read_text_state(session, identity)
        if (state.unclear or attempt.side_effect_state != "none"
            or await routing_business_activity_exists(session, tenant_id=identity.tenant_id, run_id=identity.run_id)
            or state.latest_sequence is not None and (not state.cleared or state.latest_sequence != record.sequence - 2)):
            raise ValueError("ROUTING_EXECUTION_STOP_HISTORY_INVALID")
        terminal_status = "cancelled" if status == "cancelled" else "failed"
        terminal_type = f"run.{terminal_status}"
        terminal = {"status": terminal_status, **({"reason": code} if status == "cancelled"
            else {"code": code, "message": runtime_error_message(code)})}
    events = (await session.execute(select(RunEvent.attempt_id, RunEvent.sequence, RunEvent.created_at,
        RunEvent.event_type, payload).where(RunEvent.tenant_id == identity.tenant_id,
        RunEvent.run_id == identity.run_id, RunEvent.event_type.in_(_STOP_OUTCOME_EVENTS))
        .order_by(RunEvent.sequence).limit(3))).all()
    if ([event.event_type for event in events] != [terminal_type, "routing.execution_stopped"]
        or any(event.attempt_id != identity.attempt_id for event in events)
        or [event.sequence for event in events] != [record.sequence - 1, record.sequence]
        or events[0].data != terminal or events[1].data != data
        or not attempt.finished_at <= events[0].created_at <= record.created_at):
        raise ValueError("ROUTING_EXECUTION_STOP_HISTORY_INVALID")
    return RoutingOwnedRunResult(status, code, start.root_call_id)


async def stop_owned_routing_execution(
    factory: async_sessionmaker[AsyncSession], *, identity: RunAttemptIdentity, reason: RoutingStopReason,
    reject_unstarted: bool = False, worker_entry_id: UUID | None = None,
) -> RoutingOwnedRunResult:
    """只供可信原领取者；系统停止不要求已经撤销的业务授权重新批准。

    先锁原Run使在途产品事务落定，成功只能由原完整产品记录查证。无有效租约/入口、
    产品事实冲突或提交未知时不改正文/指针；不将协作取消或终态当作物理I/O停止证明。
    """
    root: UUID | None = None
    commit_guard = _RoutingStopCommitGuard()
    if reason not in _CODES:
        return RoutingOwnedRunResult("unconfirmed", "ROUTING_EXECUTION_STOP_UNCONFIRMED")
    try:
        async with asyncio.timeout(3):
            async with _stop_transaction(factory, commit_guard) as session:
                if session.get_bind().dialect.name != "postgresql":
                    raise ValueError("ROUTING_EXECUTION_POSTGRES_REQUIRED")
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '1s'"))
                conversation, run = await lock_run_scope(session, identity.run_id, tenant_id=identity.tenant_id)
                attempt = await session.scalar(select(RunAttempt).where(
                    RunAttempt.tenant_id == identity.tenant_id, RunAttempt.run_id == identity.run_id,
                    RunAttempt.id == identity.attempt_id).execution_options(populate_existing=True).with_for_update())
                if (attempt is None or attempt.owner_id != identity.owner_id or attempt.generation != identity.generation
                    or run.conversation_id != identity.conversation_id or run.current_attempt_id != identity.attempt_id
                    or run.execution_generation != identity.generation):
                    return RoutingOwnedRunResult("fenced", "ROUTING_EXECUTION_FENCED")
                worker = await read_routing_worker_start(session, attempt=identity)
                if (worker is None and worker_entry_id is not None
                    or worker is not None and (worker.entry_id != worker_entry_id
                        or worker.agent_version_id != run.agent_version_id or worker.config_digest != run.config_digest)):
                    return RoutingOwnedRunResult("unconfirmed", "ROUTING_WORKER_ALREADY_ENTERED")
                start = await read_routing_runtime_start(session, identity)
                if start is None:
                    rejected = await _read_request_rejection(session, identity=identity,
                        owned=OwnedRun(conversation=conversation, run=run, attempt=attempt))
                    if rejected is not None:
                        return rejected
                    if reject_unstarted is True:
                        return await _reject_unstarted(session, identity=identity, reason=reason,
                            commit_guard=commit_guard)
                    return RoutingOwnedRunResult("not_started", "ROUTING_EXECUTION_NOT_STARTED")
                root = start.root_call_id
                if (run.agent_version_id != start.agent_version_id or run.input_sequence != start.input_sequence
                    or conversation.user_id != start.actor_user_id or attempt.checkpoint_thread_id != start.checkpoint_thread_id
                    or attempt.replay_policy != "manual"):
                    raise ValueError("ROUTING_EXECUTION_SCOPE_CHANGED")
                if run.status == RunStatus.COMPLETED:
                    record = await read_routing_product_commit(session, tenant_id=identity.tenant_id,
                        run_id=identity.run_id, root_call_id=root)
                    if record is None or record.root_plan_digest != start.root_plan_digest:
                        raise ValueError("ROUTING_PRODUCT_HISTORY_UNCONFIRMED")
                    return RoutingOwnedRunResult("completed", root_call_id=root, product=record)
                prior = await _read_execution_stop(session, identity=identity, start=start,
                    owned=OwnedRun(conversation=conversation, run=run, attempt=attempt))
                if prior is not None:
                    return prior
                # 后续所有写入仍要求原owner/generation、会话许可和当前租约；不接管过期者。
                owned = await owned_run(session, identity, allow_cancelling=True)
                product = await session.scalar(select(Message.id).where(Message.tenant_id == identity.tenant_id,
                    Message.conversation_id == identity.conversation_id, Message.turn_sequence == run.input_sequence,
                    Message.role == MessageRole.ASSISTANT).limit(1))
                product_event = await session.scalar(select(RunEvent.id).where(
                    RunEvent.tenant_id == identity.tenant_id, RunEvent.run_id == identity.run_id,
                    RunEvent.event_type.in_(_STOP_OUTCOME_EVENTS)).limit(1))
                if (product is not None or product_event is not None or attempt.checkpoint_id is not None
                    or conversation.checkpoint_thread_id == attempt.checkpoint_thread_id
                    or conversation.checkpoint_input_sequence == run.input_sequence):
                    raise ValueError("ROUTING_PRODUCT_STATE_UNCONFIRMED")
                business_activity = await routing_business_activity_exists(session,
                    tenant_id=identity.tenant_id, run_id=identity.run_id)
                state = await _read_text_state(session, identity)
                commit_guard.protect(attempt.lease_expires_at)
                code, now = _CODES[reason], datetime.now(UTC)
                quarantined = attempt.side_effect_state != "none" or business_activity or state.unclear
                if quarantined:
                    code = "ROUTING_EXECUTION_RECONCILIATION_REQUIRED"
                    attempt.status, attempt.error_code, attempt.finished_at = "quarantined", code, now
                    run.recovery_reason = code
                    await append_event(session, run_id=run.id, attempt_id=attempt.id,
                        event_type="run.recovery_blocked", data={"code": code})
                    status = "quarantined"
                else:
                    # reserved保持费用占用转unknown；不触碰已消费/已结算原回执，不填零或退额。
                    await close_unsettled_tools(session, attempt, code=code)
                    budget = await model_budget_summary(session, tenant_id=identity.tenant_id, run_id=run.id)
                    apply_model_budget_summary(run, budget)
                    attempt.metrics = {**await model_budget_summary(session, tenant_id=identity.tenant_id,
                        run_id=run.id, attempt_id=attempt.id), "tool_call_count": 0}
                    run.tool_call_count = 0
                    # 本路径只允许缓存后采纳；异常出现的本attempt未验证正文可撤回，不能清别的attempt。
                    # 空delta并不撤回此前正文；只要存在原attempt文本就明确replace为空。
                    if state.latest_sequence is not None:
                        await append_event(session, run_id=run.id, attempt_id=attempt.id,
                            event_type="text.replace", data={"content": ""})
                    cancelled = reason == "cancelled" or run.status == RunStatus.CANCELLING or conversation.deleted_at is not None
                    if cancelled:
                        if run.status == RunStatus.RUNNING:
                            transition_run(run, RunStatus.CANCELLING, now=now)
                        transition_run(run, RunStatus.CANCELLED, now=now)
                        status = "cancelled"
                        run.error_code = None
                        await append_event(session, run_id=run.id, attempt_id=attempt.id,
                            event_type="run.cancelled", data={"status": status, "reason": code})
                    else:
                        transition_run(run, RunStatus.FAILED, now=now)
                        status = "failed"
                        run.error_code = code
                        await append_event(session, run_id=run.id, attempt_id=attempt.id,
                            event_type="run.failed", data={"status": status, "code": code,
                                "message": runtime_error_message(code)})
                    if not cancelled and reason in {"checkpoint_unconfirmed", "product_unconfirmed"}:
                        status = "quarantined"  # Run失败，但未采纳的checkpoint保持尝试隔离，不开放恢复。
                    attempt.status, attempt.error_code, attempt.finished_at = status, code, now
                    release_conversation(owned)
                await append_event(session, run_id=run.id, attempt_id=attempt.id,
                    event_type="routing.execution_stopped", data={"root_call_id": str(root),
                        "start_digest": manifest_digest(start.model_dump(mode="json")), "status": status, "code": code})
                # 提交前用历史读取契约复核本次写入；不把仅内存状态当作可确认回执。
                stopped = await _read_execution_stop(session, identity=identity, owned=owned, start=start)
                if stopped is None or stopped != RoutingOwnedRunResult(status, code, root):
                    raise ValueError("ROUTING_EXECUTION_STOP_HISTORY_INVALID")
            return stopped
    except asyncio.CancelledError:
        raise
    except RunLeaseLostError:
        return RoutingOwnedRunResult("fenced", "ROUTING_EXECUTION_FENCED", root)
    except Exception:
        return RoutingOwnedRunResult("unconfirmed", "ROUTING_EXECUTION_STOP_UNCONFIRMED", root)


async def _unstarted_resources_clear(session: AsyncSession, *, identity: RunAttemptIdentity, owned: OwnedRun) -> bool:
    """原锁内复用同一未执行证据；终态只读查证不借此领取当前执行许可。"""
    run, attempt, conversation = owned.run, owned.attempt, owned.conversation
    version = await session.scalar(select(AgentVersion.id).where(AgentVersion.id == run.agent_version_id,
        AgentVersion.tenant_id == identity.tenant_id, AgentVersion.agent_id == run.agent_id,
        AgentVersion.schema_version == "3", AgentVersion.config_digest == run.config_digest,
        AgentVersion.model_endpoint_version_id == run.model_endpoint_version_id))
    ledger = await session.scalar(select(ModelCallLedger.id).where(ModelCallLedger.tenant_id == identity.tenant_id,
        ModelCallLedger.run_id == identity.run_id).limit(1))
    business_activity = await routing_business_activity_exists(session,
        tenant_id=identity.tenant_id, run_id=identity.run_id)
    product = await session.scalar(select(Message.id).where(Message.tenant_id == identity.tenant_id,
        Message.conversation_id == identity.conversation_id, Message.turn_sequence == run.input_sequence,
        Message.role == MessageRole.ASSISTANT).limit(1))
    execution = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == identity.tenant_id,
        RunEvent.run_id == identity.run_id, RunEvent.event_type.in_(_EXECUTION_EVENTS)).limit(1))
    return (version is not None and ledger is None and not business_activity and product is None
        and execution is None and run.input_sequence is not None and attempt.side_effect_state == "none"
        and attempt.checkpoint_id is None and attempt.replay_policy == "manual"
        and conversation.checkpoint_thread_id != attempt.checkpoint_thread_id
        and conversation.checkpoint_input_sequence != run.input_sequence
        and conversation.agent_id == run.agent_id and conversation.agent_version_id == run.agent_version_id)


async def _read_request_rejection(session: AsyncSession, *, identity: RunAttemptIdentity,
    owned: OwnedRun,
) -> RoutingOwnedRunResult | None:
    """查原未开始拒绝；无需有效运行租约，不恢复许可或写历史。"""
    payload = case((func.octet_length(cast(RunEvent.data, Text())) <= 4096,
        RunEvent.data), else_=None).label("data")
    records = (await session.execute(select(RunEvent.attempt_id, RunEvent.sequence, RunEvent.created_at, payload)
        .where(RunEvent.tenant_id == identity.tenant_id, RunEvent.run_id == identity.run_id,
            RunEvent.event_type == "routing.request_rejected").order_by(RunEvent.sequence).limit(2))).all()
    if not records:
        return None
    if len(records) != 1:
        raise ValueError("ROUTING_REQUEST_REJECTION_HISTORY_INVALID")
    record, run, attempt, conversation = records[0], owned.run, owned.attempt, owned.conversation
    data = record.data
    if (not isinstance(data, dict) or set(data) != {"operation_id", "attempt_id", "generation", "status", "code"}
        or type(data["generation"]) is not int or data["generation"] != identity.generation
        or data["operation_id"] != str(uuid5(run.id, "routing-request-rejected-v1"))
        or data["attempt_id"] != str(identity.attempt_id) or record.attempt_id != identity.attempt_id
        or data["status"] not in ("failed", "cancelled")):
        raise ValueError("ROUTING_REQUEST_REJECTION_HISTORY_INVALID")
    status, code = data["status"], data["code"]
    permitted = (_CODES["cancelled"],) if status == "cancelled" else (
        "ROUTING_RUN_REQUEST_REJECTED", _CODES["worker_unavailable"], _CODES["worker_binding_invalid"])
    if (code not in permitted or attempt.status != status or attempt.error_code != code
        or run.status != (RunStatus.FAILED if status == "failed" else RunStatus.CANCELLED)
        or run.error_code != (code if status == "failed" else None)
        or run.finished_at is None or attempt.finished_at != run.finished_at
        or conversation.active_run_id == run.id or conversation.active_attempt_id == attempt.id
        or not run.finished_at <= record.created_at <= datetime.now(UTC)
        or not await _unstarted_resources_clear(session, identity=identity, owned=owned)):
        raise ValueError("ROUTING_REQUEST_REJECTION_HISTORY_INVALID")
    events = (await session.execute(select(RunEvent.attempt_id, RunEvent.sequence, RunEvent.created_at,
        RunEvent.event_type, payload).where(RunEvent.tenant_id == identity.tenant_id,
        RunEvent.run_id == identity.run_id,
        RunEvent.event_type.in_(_REJECTION_OUTCOME_EVENTS))
        .order_by(RunEvent.sequence).limit(3))).all()
    terminal = {"status": status, **({"code": code, "message": runtime_error_message(code)}
        if status == "failed" else {"reason": code})}
    if ([event.event_type for event in events] != [f"run.{status}", "routing.request_rejected"]
        or any(event.attempt_id != identity.attempt_id for event in events)
        or [event.sequence for event in events] != [record.sequence - 1, record.sequence]
        or events[0].data != terminal or events[1].data != data
        or not run.finished_at <= events[0].created_at <= record.created_at):
        raise ValueError("ROUTING_REQUEST_REJECTION_HISTORY_INVALID")
    return RoutingOwnedRunResult(status, code)


async def _reject_unstarted(session: AsyncSession, *, identity: RunAttemptIdentity,
    reason: RoutingStopReason, commit_guard: _RoutingStopCommitGuard,
) -> RoutingOwnedRunResult:
    """仅持久产品输入构造路径允许拒绝未开始请求；不用临时根ID补造执行入口。"""
    owned = await owned_run(session, identity, allow_cancelling=True)
    run, attempt = owned.run, owned.attempt
    prior = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == identity.tenant_id,
        RunEvent.run_id == identity.run_id, RunEvent.event_type.in_(_REJECTION_OUTCOME_EVENTS)).limit(1))
    if prior is not None or not await _unstarted_resources_clear(session, identity=identity, owned=owned):
        return RoutingOwnedRunResult("unconfirmed", "ROUTING_EXECUTION_STOP_UNCONFIRMED")
    commit_guard.protect(attempt.lease_expires_at)
    cancelled = reason == "cancelled" or run.status == RunStatus.CANCELLING or owned.conversation.deleted_at is not None
    code = ("ROUTING_EXECUTION_CANCELLED" if cancelled else _CODES[reason]
        if reason in {"worker_unavailable", "worker_binding_invalid"} else "ROUTING_RUN_REQUEST_REJECTED")
    now = datetime.now(UTC)
    if cancelled:
        if run.status == RunStatus.RUNNING:
            transition_run(run, RunStatus.CANCELLING, now=now)
        transition_run(run, RunStatus.CANCELLED, now=now)
        status = "cancelled"
        run.error_code = None
        await append_event(session, run_id=run.id, attempt_id=attempt.id, event_type="run.cancelled",
            data={"status": status, "reason": code})
    else:
        transition_run(run, RunStatus.FAILED, now=now)
        status = "failed"
        run.error_code = code
        await append_event(session, run_id=run.id, attempt_id=attempt.id, event_type="run.failed",
            data={"status": status, "code": code, "message": runtime_error_message(code)})
    apply_model_budget_summary(run, await model_budget_summary(session, tenant_id=identity.tenant_id, run_id=run.id))
    run.tool_call_count = 0
    attempt.metrics = {**await model_budget_summary(session, tenant_id=identity.tenant_id,
        run_id=run.id, attempt_id=attempt.id), "tool_call_count": 0}
    attempt.status, attempt.finished_at, attempt.error_code = status, now, code
    release_conversation(owned)
    await append_event(session, run_id=run.id, attempt_id=attempt.id, event_type="routing.request_rejected",
        data={"operation_id": str(uuid5(run.id, "routing-request-rejected-v1")), "attempt_id": str(attempt.id),
            "generation": identity.generation, "status": status, "code": code})
    # 与后续只读查证使用同一证据，不提交一份自己无法确认的拒绝历史。
    rejected = await _read_request_rejection(session, identity=identity, owned=owned)
    if rejected is None or rejected != RoutingOwnedRunResult(status, code):
        raise ValueError("ROUTING_REQUEST_REJECTION_HISTORY_INVALID")
    return rejected
