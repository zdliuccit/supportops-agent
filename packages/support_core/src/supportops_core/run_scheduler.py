"""数据库持久化租户轮转；Redis 只唤醒消费者，不决定执行公平性。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.exc import IntegrityError

from supportops_core.config import Settings
from supportops_core.enums import RunStatus
from supportops_core.models import AgentRun, Conversation, RunAttempt, RunNotificationOutbox, RunSchedulerState, Tenant
from supportops_core.run_attempt_services import RunClaim, claim_run_attempt


async def ensure_scheduler_state(session: AsyncSession, *, queue_name: str) -> None:
    """只在启动初始化；唯一键竞争不会覆盖已保存的轮转游标。"""
    if await session.get(RunSchedulerState, queue_name) is not None:
        return
    try:
        async with session.begin_nested():
            session.add(RunSchedulerState(queue_name=queue_name))
            await session.flush()
    except IntegrityError:
        if await session.get(RunSchedulerState, queue_name) is None:
            raise


async def claim_next_fair_run(session: AsyncSession, *, settings: Settings, owner_id: str) -> RunClaim | None:
    """必须在调用方事务中使用；全局轮转锁之后仍按会话→Run→租户领取。

    启动时创建调度状态，运行时缺失应报错，不竞争插入或重置游标。
    只调度已成功通知且仍 queued 的事实，死信必须管理员显式恢复。
    """
    state = await session.scalar(select(RunSchedulerState).where(
        RunSchedulerState.queue_name == settings.redis_queue_name,
    ).execution_options(populate_existing=True).with_for_update(skip_locked=True))
    if state is None:
        if await session.get(RunSchedulerState, settings.redis_queue_name) is None:
            raise RuntimeError("持久化调度游标缺失，停止领取以避免重置公平顺序")
        return None
    previous = aliased(AgentRun)
    active_run = aliased(AgentRun)
    active_count = select(func.count()).select_from(RunAttempt).join(active_run, active_run.current_attempt_id == RunAttempt.id).where(
        RunAttempt.tenant_id == AgentRun.tenant_id, RunAttempt.status == "running", RunAttempt.lease_expires_at > datetime.now(UTC),
        active_run.tenant_id == AgentRun.tenant_id, active_run.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING)),
    ).correlate(AgentRun).scalar_subquery()
    eligible = (
        AgentRun.status == RunStatus.QUEUED, AgentRun.input_sequence.is_not(None),
        exists(select(Tenant.id).where(Tenant.id == AgentRun.tenant_id, Tenant.status == "active")),
        exists(select(Conversation.id).where(Conversation.id == AgentRun.conversation_id,
            Conversation.tenant_id == AgentRun.tenant_id, Conversation.active_run_id.is_(None), Conversation.deleted_at.is_(None))),
        exists(select(RunNotificationOutbox.id).where(RunNotificationOutbox.run_id == AgentRun.id,
            RunNotificationOutbox.tenant_id == AgentRun.tenant_id, RunNotificationOutbox.status == "delivered")),
        ~exists(select(previous.id).where(previous.tenant_id == AgentRun.tenant_id,
            previous.conversation_id == AgentRun.conversation_id, previous.id != AgentRun.id,
            or_(previous.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING)), and_(previous.status == RunStatus.QUEUED,
                or_(previous.input_sequence < AgentRun.input_sequence, previous.input_sequence.is_(None)))))).correlate(AgentRun),
        active_count < settings.tenant_max_active_runs,
    )
    tenant_query = select(AgentRun.tenant_id).where(*eligible).distinct().order_by(AgentRun.tenant_id).limit(1)
    tenant_id = None
    if state.last_tenant_id is not None:
        tenant_id = await session.scalar(tenant_query.where(AgentRun.tenant_id > state.last_tenant_id))
    if tenant_id is None:
        tenant_id = await session.scalar(tenant_query)
    if tenant_id is None:
        return None
    run_id = await session.scalar(select(AgentRun.id).where(*eligible, AgentRun.tenant_id == tenant_id)
        .order_by(AgentRun.created_at, AgentRun.id).limit(1))
    if run_id is None:
        return None
    # 拒绝授权的 Run 也让出当前轮次；异常事务则整体回滚，不吞掉调度状态。
    state.last_tenant_id, state.updated_at = tenant_id, datetime.now(UTC)
    return await claim_run_attempt(session, run_id, owner_id=owner_id, lease_seconds=settings.run_attempt_lease_seconds,
        max_tenant_active_runs=settings.tenant_max_active_runs)
