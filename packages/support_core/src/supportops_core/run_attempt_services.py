"""Run 领取、租约 fencing、输入执行许可和有限恢复的共同事务边界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import parse_agent_config, pinned_tool_version
from supportops_core.agent_services import get_usable_agent
from supportops_core.auth import IdentityContext, Principal
from supportops_core.config import Settings
from supportops_core.enums import RunStatus
from supportops_core.models import (
    AgentRun, AgentVersion, Conversation, ModelCallLedger, ModelEndpoint, ModelEndpointVersion, RunAttempt, RunNotificationOutbox, Tenant, ToolInvocation, User,
)
from supportops_core.services import ResourceNotFoundError, append_event, transition_run
from supportops_core.quota_services import active_tenant_attempt_count
from supportops_core.tool_services import ensure_builtin_tools

READ_ONLY_IMPLEMENTATIONS = frozenset({("current_identity_summary", 1), ("support_ticket_lookup", 1), ("support_ticket_lookup", 2), ("search_knowledge", 1),
    ("get_request_logs", 1), ("get_current_user_profile", 1), ("get_model_route_status", 1), ("list_active_incidents", 1)})


class RunLeaseLostError(RuntimeError):
    """该执行者已过期或不再拥有当前尝试，禁止继续提交。"""


class RunCancellationRequested(RuntimeError):
    """当前 Run 已进入取消流程。"""


class RuntimeAuthorizationDenied(RuntimeError):
    """当前用户/租户/Agent grant 不再允许执行。"""
    code = "RUNTIME_AUTHORIZATION_DENIED"


class ModelResourceUnavailable(RuntimeAuthorizationDenied):
    code = "MODEL_RESOURCE_UNAVAILABLE"


class RunRecoveryConflictError(ValueError):
    """恢复前提、generation 或副作用事实不允许处理。"""


@dataclass(frozen=True, slots=True)
class RunAttemptIdentity:
    tenant_id: UUID
    conversation_id: UUID
    run_id: UUID
    attempt_id: UUID
    generation: int
    owner_id: str


@dataclass(frozen=True, slots=True)
class RunClaim:
    run: AgentRun
    attempt: RunAttempt
    identity: RunAttemptIdentity


@dataclass(frozen=True, slots=True)
class OwnedRun:
    conversation: Conversation
    run: AgentRun
    attempt: RunAttempt


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def lock_run_scope(session: AsyncSession, run_id: UUID, *, tenant_id: UUID | None = None) -> tuple[Conversation, AgentRun]:
    query = select(AgentRun.tenant_id, AgentRun.conversation_id).where(AgentRun.id == run_id)
    if tenant_id is not None:
        query = query.where(AgentRun.tenant_id == tenant_id)
    scope = (await session.execute(query)).one_or_none()
    if scope is None:
        raise ResourceNotFoundError("Run 不存在")
    conversation = await session.scalar(select(Conversation).where(
        Conversation.id == scope.conversation_id, Conversation.tenant_id == scope.tenant_id,
    ).execution_options(include_deleted=True, populate_existing=True).with_for_update(key_share=True))
    run = await session.scalar(select(AgentRun).where(
        AgentRun.id == run_id, AgentRun.tenant_id == scope.tenant_id,
    ).execution_options(populate_existing=True).with_for_update())
    if conversation is None or run is None:
        raise ResourceNotFoundError("Run 不存在")
    return conversation, run


async def current_run_identity(session: AsyncSession, *, run: AgentRun, conversation: Conversation) -> IdentityContext:
    """固定历史角色仅供审计，权限始终重新从数据库解析。"""
    if conversation.deleted_at is not None:
        raise RunCancellationRequested("会话已删除")
    user = await session.scalar(select(User).where(User.id == conversation.user_id, User.tenant_id == run.tenant_id)
        .execution_options(populate_existing=True))
    tenant = await session.get(Tenant, run.tenant_id)
    if user is None or user.status != "active" or tenant is None or tenant.status != "active":
        raise RuntimeAuthorizationDenied("当前主体不允许执行")
    identity = IdentityContext(principal=Principal(
        user_id=user.id, tenant_id=user.tenant_id, roles=tuple(user.roles), display_name=user.display_name,
    ), user=user)
    try:
        await get_usable_agent(session, agent_id=run.agent_id, identity=identity)
    except ResourceNotFoundError as exc:
        raise RuntimeAuthorizationDenied("当前 Agent 授权不可用") from exc
    available_model = await session.scalar(select(ModelEndpointVersion.id).join(ModelEndpoint,
        ModelEndpoint.id == ModelEndpointVersion.endpoint_id).where(
        ModelEndpointVersion.id == run.model_endpoint_version_id, ModelEndpointVersion.tenant_id == run.tenant_id,
        ModelEndpoint.tenant_id == run.tenant_id, ModelEndpoint.is_enabled.is_(True),
    ))
    if available_model is None:
        raise ModelResourceUnavailable("固定模型端点已停用或不可用")
    return identity


async def owned_run(
    session: AsyncSession, identity: RunAttemptIdentity, *, allow_cancelling: bool = False,
) -> OwnedRun:
    """加锁后的 fencing 校验保护事件、续租和产品事务。"""
    conversation, run = await lock_run_scope(session, identity.run_id, tenant_id=identity.tenant_id)
    attempt = await session.scalar(select(RunAttempt).where(
        RunAttempt.id == identity.attempt_id, RunAttempt.run_id == identity.run_id,
        RunAttempt.tenant_id == identity.tenant_id,
    ).execution_options(populate_existing=True).with_for_update())
    if (
        run.conversation_id != identity.conversation_id
        or run.current_attempt_id != identity.attempt_id
        or run.execution_generation != identity.generation
        or conversation.active_run_id != identity.run_id
        or conversation.active_attempt_id != identity.attempt_id
        or attempt is None or attempt.owner_id != identity.owner_id
        or attempt.generation != identity.generation or attempt.status != "running"
        or _aware(attempt.lease_expires_at) <= datetime.now(UTC)
        or run.status not in {RunStatus.RUNNING, RunStatus.CANCELLING}
    ):
        raise RunLeaseLostError("执行尝试已失效")
    if not allow_cancelling and (run.status == RunStatus.CANCELLING or conversation.deleted_at is not None):
        raise RunCancellationRequested("Run 已请求取消")
    return OwnedRun(conversation=conversation, run=run, attempt=attempt)


def release_conversation(owned: OwnedRun) -> None:
    """只释放该尝试的许可，不能清除其他尝试的有效指针。"""
    if owned.conversation.active_attempt_id == owned.attempt.id:
        owned.conversation.active_run_id = None
        owned.conversation.active_attempt_id = None


async def close_unsettled_tools(session: AsyncSession, attempt: RunAttempt, *, code: str) -> None:
    """失效尝试的未回执调用保持 unknown，额度不退还，也不宣称成功/未执行。"""
    await session.execute(update(ToolInvocation).where(
        ToolInvocation.tenant_id == attempt.tenant_id, ToolInvocation.attempt_id == attempt.id,
        ToolInvocation.status == "running",
    ).values(status="unknown", error_code=code, finished_at=datetime.now(UTC)))
    await session.execute(update(ModelCallLedger).where(
        ModelCallLedger.tenant_id == attempt.tenant_id, ModelCallLedger.attempt_id == attempt.id,
        ModelCallLedger.status == "reserved",
    ).values(status="unknown", error_code=code, finished_at=datetime.now(UTC)))


async def claim_run_attempt(
    session: AsyncSession, run_id: UUID, *, owner_id: str, lease_seconds: float,
    max_tenant_active_runs: int = 16,
) -> RunClaim | None:
    try:
        conversation, run = await lock_run_scope(session, run_id)
    except ResourceNotFoundError:
        return None
    if run.status != RunStatus.QUEUED or conversation.deleted_at is not None:
        return None
    notification = await session.scalar(select(RunNotificationOutbox).where(
        RunNotificationOutbox.run_id == run.id, RunNotificationOutbox.tenant_id == run.tenant_id,
    ).with_for_update())
    if notification is not None and notification.status in {"dead_letter", "obsolete"}:
        return None
    if conversation.active_run_id is not None:
        return None
    blocker = await session.scalar(select(AgentRun.id).where(
        AgentRun.tenant_id == run.tenant_id, AgentRun.conversation_id == run.conversation_id,
        AgentRun.id != run.id,
        or_(AgentRun.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION)),
            and_(AgentRun.status == RunStatus.QUEUED, or_(
                AgentRun.input_sequence < run.input_sequence,
                AgentRun.input_sequence.is_(None),
            ))),
    ).limit(1))
    if blocker is not None:
        return None
    try:
        identity_context = await current_run_identity(session, run=run, conversation=conversation)
    except RuntimeAuthorizationDenied as exc:
        transition_run(run, RunStatus.FAILED)
        run.error_code = exc.code
        from supportops_core.runtime_errors import runtime_error_message
        await append_event(session, run_id=run.id, event_type="run.failed",
            data={"status": "failed", "code": run.error_code, "message": runtime_error_message(run.error_code)})
        return None
    await session.scalar(select(Tenant).where(Tenant.id == run.tenant_id).with_for_update(key_share=True))
    if await active_tenant_attempt_count(session, tenant_id=run.tenant_id) >= max_tenant_active_runs:
        return None
    await ensure_builtin_tools(session, tenant_id=run.tenant_id, actor_user_id=identity_context.user.id)
    if run.input_sequence is None:
        raise RunRecoveryConflictError("Run 缺少固定输入序号，需先应用迁移")
    version = await session.get(AgentVersion, run.agent_version_id)
    replay_policy = "manual"
    if version is not None and version.tenant_id == run.tenant_id:
        try:
            config = parse_agent_config(version.config)
            if all((binding.tool_id, pinned_tool_version(version.resolved_tool_versions, binding.tool_id))
                   in READ_ONLY_IMPLEMENTATIONS for binding in config.tools if binding.enabled):
                replay_policy = "read_only"
        except ValueError:
            pass
    now = datetime.now(UTC)
    attempt_id = uuid4()
    generation = run.execution_generation + 1
    attempt = RunAttempt(
        id=attempt_id, tenant_id=run.tenant_id, run_id=run.id, generation=generation,
        owner_id=owner_id, heartbeat_at=now, lease_expires_at=now + timedelta(seconds=lease_seconds),
        checkpoint_thread_id=f"tenant:{run.tenant_id}:conversation:{run.conversation_id}:run:{run.id}:attempt:{attempt_id}",
        replay_policy=replay_policy, side_effect_state="none", recovery_reason=run.recovery_reason,
    )
    session.add(attempt)
    await session.flush()
    run.execution_generation = generation
    run.current_attempt_id = attempt_id
    conversation.active_run_id = run.id
    conversation.active_attempt_id = attempt_id
    transition_run(run, RunStatus.RUNNING, now=now)
    await append_event(session, run_id=run.id, event_type="run.started", attempt_id=attempt.id, data={
        "status": "running", "attempt_id": str(attempt.id), "generation": generation,
        "recovery_count": run.recovery_count,
    })
    identity = RunAttemptIdentity(run.tenant_id, run.conversation_id, run.id, attempt.id, generation, owner_id)
    return RunClaim(run=run, attempt=attempt, identity=identity)


async def check_and_renew_attempt(
    session_factory: async_sessionmaker[AsyncSession], identity: RunAttemptIdentity, *, lease_seconds: float,
) -> None:
    async with session_factory() as session, session.begin():
        owned = await owned_run(session, identity)
        await current_run_identity(session, run=owned.run, conversation=owned.conversation)
        now = datetime.now(UTC)
        owned.attempt.heartbeat_at = now
        owned.attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)


async def reset_run_notification(session: AsyncSession, run: AgentRun, *, now: datetime) -> None:
    """有限执行恢复获准后重开通知窗口，保留通知累计尝试。"""
    notification = await session.scalar(select(RunNotificationOutbox).where(
        RunNotificationOutbox.run_id == run.id, RunNotificationOutbox.tenant_id == run.tenant_id,
    ).with_for_update())
    if notification is None:
        session.add(RunNotificationOutbox(tenant_id=run.tenant_id, run_id=run.id, event_key=f"run:{run.id}:queued"))
    else:
        notification.status = "pending"
        notification.cycle_attempt_count = 0
        notification.window_started_at = now
        notification.next_attempt_at = now
        notification.last_error_code = None
        notification.revision += 1


async def recover_expired_attempts(
    session_factory: async_sessionmaker[AsyncSession], *, settings: Settings, limit: int = 100,
) -> int:
    now = datetime.now(UTC)
    async with session_factory() as session:
        candidates = list((await session.scalars(select(RunAttempt.run_id)
            .join(AgentRun, AgentRun.current_attempt_id == RunAttempt.id)
            .where(RunAttempt.status == "running", RunAttempt.lease_expires_at <= now,
                   AgentRun.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING)))
            .order_by(RunAttempt.lease_expires_at, RunAttempt.id).limit(limit))).all())
    recovered = 0
    for run_id in candidates:
        async with session_factory() as session, session.begin():
            conversation, run = await lock_run_scope(session, run_id)
            attempt = await session.scalar(select(RunAttempt).where(
                RunAttempt.id == run.current_attempt_id, RunAttempt.run_id == run.id,
                RunAttempt.tenant_id == run.tenant_id,
            ).with_for_update())
            if attempt is None or attempt.status != "running" or _aware(attempt.lease_expires_at) > datetime.now(UTC):
                continue
            if run.status not in {RunStatus.RUNNING, RunStatus.CANCELLING}:
                continue
            attempt.finished_at = now
            attempt.error_code = "RUN_LEASE_EXPIRED"
            await close_unsettled_tools(session, attempt, code="RUN_LEASE_EXPIRED")
            owned = OwnedRun(conversation, run, attempt)
            if attempt.side_effect_state != "none" or attempt.replay_policy != "read_only":
                attempt.status = "quarantined"
                run.recovery_reason = "ACTION_RECONCILIATION_REQUIRED"
                await append_event(session, run_id=run.id, event_type="run.recovery_blocked", attempt_id=attempt.id, data={"code": run.recovery_reason})
                continue
            attempt.status = "expired"
            if run.status == RunStatus.CANCELLING or conversation.deleted_at is not None:
                if run.status == RunStatus.RUNNING:
                    transition_run(run, RunStatus.CANCELLING, now=now)
                transition_run(run, RunStatus.CANCELLED, now=now)
                attempt.status = "cancelled"
                await append_event(session, run_id=run.id, event_type="run.cancelled", attempt_id=attempt.id, data={"status": "cancelled", "reason": "expired_cancelled_attempt"})
            elif run.recovery_count >= settings.run_attempt_max_recoveries:
                transition_run(run, RunStatus.FAILED, now=now)
                run.error_code = "RUN_RECOVERY_EXHAUSTED"
                await append_event(session, run_id=run.id, event_type="run.failed", attempt_id=attempt.id, data={"status": "failed", "code": run.error_code})
            else:
                transition_run(run, RunStatus.QUEUED, now=now)
                run.current_attempt_id = None
                run.recovery_count += 1
                run.recovery_reason = "RUN_LEASE_EXPIRED"
                run.error_code = None
                await append_event(session, run_id=run.id, event_type="text.replace", attempt_id=attempt.id, data={"content": ""})
                await append_event(session, run_id=run.id, event_type="run.recovering", attempt_id=attempt.id, data={"status": "queued", "reason": run.recovery_reason, "recovery_count": run.recovery_count})
                await reset_run_notification(session, run, now=now)
                recovered += 1
            release_conversation(owned)
    return recovered


async def list_run_attempts(
    session: AsyncSession, *, tenant_id: UUID, run_id: UUID, page_size: int, offset: int,
) -> tuple[list[RunAttempt], int]:
    filters = (RunAttempt.tenant_id == tenant_id, RunAttempt.run_id == run_id)
    total = int(await session.scalar(select(func.count()).select_from(RunAttempt).where(*filters)) or 0)
    attempts = list((await session.scalars(select(RunAttempt).where(*filters)
        .order_by(RunAttempt.generation.desc(), RunAttempt.id.desc()).offset(offset).limit(page_size))).all())
    return attempts, total


async def recover_run_manually(
    session: AsyncSession, *, tenant_id: UUID, run_id: UUID, actor_user_id: UUID,
    expected_generation: int, confirm_legacy_executor_stopped: bool, cancel: bool,
    settings: Settings,
) -> AgentRun:
    """有权限管理员诊断后恢复；确认旧进程停止不等于批准未知副作用重放。"""
    conversation, run = await lock_run_scope(session, run_id, tenant_id=tenant_id)
    if run.execution_generation != expected_generation or run.status not in {RunStatus.RUNNING, RunStatus.CANCELLING}:
        raise RunRecoveryConflictError("Run generation 或状态已变化，请刷新后处理")
    now = datetime.now(UTC)
    attempt = None
    if run.current_attempt_id is None:
        if not confirm_legacy_executor_stopped:
            raise RunRecoveryConflictError("旧 Run 无可信 owner，必须确认旧执行者已停止")
        reason = "LEGACY_EXECUTOR_STOPPED_CONFIRMED"
    else:
        attempt = await session.scalar(select(RunAttempt).where(
            RunAttempt.id == run.current_attempt_id, RunAttempt.run_id == run.id,
            RunAttempt.tenant_id == tenant_id,
        ).with_for_update())
        if attempt is None or (attempt.status == "running" and _aware(attempt.lease_expires_at) > now):
            raise RunRecoveryConflictError("当前尝试仍有效，不能强制移交")
        if attempt.side_effect_state != "none" or attempt.replay_policy != "read_only":
            raise RunRecoveryConflictError("副作用或重放安全性未确认，必须通过动作对账处理")
        attempt.status = "expired"
        attempt.finished_at = now
        await close_unsettled_tools(session, attempt, code="MANUAL_EXPIRED_ATTEMPT_RECOVERY")
        reason = "MANUAL_EXPIRED_ATTEMPT_RECOVERY"
    if not cancel:
        if run.status == RunStatus.CANCELLING or conversation.deleted_at is not None:
            raise RunRecoveryConflictError("已请求取消或已删除会话不能恢复执行")
        await current_run_identity(session, run=run, conversation=conversation)
        version = await session.get(AgentVersion, run.agent_version_id)
        if version is None or version.tenant_id != tenant_id:
            raise RunRecoveryConflictError("固定 Agent 版本不可用")
        config = parse_agent_config(version.config)
        if any((binding.tool_id, pinned_tool_version(version.resolved_tool_versions, binding.tool_id))
               not in READ_ONLY_IMPLEMENTATIONS for binding in config.tools if binding.enabled):
            raise RunRecoveryConflictError("未证明只读的旧工具不能自动重放")
        if run.recovery_count >= settings.run_attempt_max_recoveries:
            raise RunRecoveryConflictError("执行恢复次数已达上限，不能反复扩大窗口")
        transition_run(run, RunStatus.QUEUED, now=now)
        run.recovery_count += 1
        run.error_code = None
        await reset_run_notification(session, run, now=now)
        await append_event(session, run_id=run.id, event_type="text.replace", attempt_id=attempt.id if attempt else None, data={"content": ""})
    else:
        if run.status == RunStatus.RUNNING:
            transition_run(run, RunStatus.CANCELLING, now=now)
        transition_run(run, RunStatus.CANCELLED, now=now)
        if attempt is not None:
            attempt.status = "cancelled"
    run.current_attempt_id = None
    conversation.active_run_id = None
    conversation.active_attempt_id = None
    run.recovery_reason = reason
    await append_event(session, run_id=run.id, event_type="run.cancelled" if cancel else "run.recovering",
        attempt_id=attempt.id if attempt else None,
        data={"status": run.status.value, "reason": reason, "actor_user_id": str(actor_user_id), "recovery_count": run.recovery_count})
    return run
