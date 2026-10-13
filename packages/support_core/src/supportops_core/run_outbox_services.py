"""Run 通知至少一次分发、有限退避与死信重试；不重建业务输入。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from supportops_core.config import Settings
from supportops_core.enums import RunStatus
from supportops_core.models import AgentRun, Conversation, RunNotificationOutbox
from supportops_core.queue import RunQueue
from supportops_core.services import ResourceNotFoundError, append_event
from supportops_core.quota_services import active_tenant_attempt_count

NotificationStatus = Literal["pending", "delivered", "dead_letter", "obsolete"]


class RunNotificationConflictError(ValueError):
    """状态或 revision 不允许重新分发。"""


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def dispatch_run_notifications(
    session_factory: async_sessionmaker[AsyncSession], *, queue: RunQueue,
    settings: Settings, run_id: UUID | None = None, limit: int | None = None,
) -> int:
    """每条通知独立事务持锁投递；Redis 成功但提交失败时允许重复同一 Run。"""
    delivered = 0
    for _ in range(limit if limit is not None else settings.run_outbox_batch_size):
        now = datetime.now(UTC)
        async with session_factory() as session, session.begin():
            query = select(RunNotificationOutbox).where(
                RunNotificationOutbox.status.in_(("pending", "delivered")),
                RunNotificationOutbox.next_attempt_at <= now,
            )
            if run_id is not None:
                query = query.where(RunNotificationOutbox.run_id == run_id)
            notification = await session.scalar(query.order_by(
                RunNotificationOutbox.next_attempt_at, RunNotificationOutbox.id,
            ).limit(1))
            if notification is None:
                break
            run = await session.scalar(select(AgentRun).where(
                AgentRun.id == notification.run_id, AgentRun.tenant_id == notification.tenant_id,
            ).with_for_update())
            # 与恢复/产品提交一致，先锁 Run 再锁通知，避免互相反向等待。
            notification = await session.scalar(select(RunNotificationOutbox).where(
                RunNotificationOutbox.id == notification.id,
                RunNotificationOutbox.status.in_(("pending", "delivered")),
                RunNotificationOutbox.next_attempt_at <= now,
            ).execution_options(populate_existing=True).with_for_update(skip_locked=True))
            if notification is None:
                continue
            conversation = None if run is None else await session.scalar(
                select(Conversation).where(
                    Conversation.id == run.conversation_id, Conversation.tenant_id == run.tenant_id,
                ).execution_options(include_deleted=True)
            )
            if run is None or conversation is None or conversation.deleted_at is not None or run.status != RunStatus.QUEUED:
                notification.status = "obsolete"
                notification.revision += 1
                notification.last_error_code = "RUN_NOTIFICATION_NO_LONGER_REQUIRED"
                continue

            # 前一轮仍在执行/排队时不消耗本轮投递额度，也不反复占用 Redis。
            previous = aliased(AgentRun)
            blocked = await session.scalar(select(previous.id).where(
                previous.tenant_id == run.tenant_id,
                previous.conversation_id == run.conversation_id,
                previous.id != run.id,
                or_(previous.status.in_((RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION)),
                    and_(previous.status == RunStatus.QUEUED, or_(
                        previous.input_sequence < run.input_sequence,
                        previous.input_sequence.is_(None),
                    ))),
            ).limit(1))
            if blocked is not None:
                notification.next_attempt_at = now + timedelta(seconds=settings.run_outbox_interval_seconds)
                continue
            if await active_tenant_attempt_count(session, tenant_id=run.tenant_id) >= settings.tenant_max_active_runs:
                notification.next_attempt_at = now + timedelta(seconds=settings.run_outbox_interval_seconds)
                continue

            age = (now - _aware(notification.window_started_at)).total_seconds()
            if notification.cycle_attempt_count >= settings.run_outbox_max_attempts or age >= settings.run_outbox_max_age_seconds:
                notification.status = "dead_letter"
                notification.revision += 1
                notification.last_error_code = "RUN_NOTIFICATION_EXHAUSTED"
                run.recovery_reason = "RUN_NOTIFICATION_EXHAUSTED"
                await append_event(session, run_id=run.id, event_type="run.dispatch_failed", data={
                    "code": notification.last_error_code, "notification_id": str(notification.id),
                })
                continue

            notification.attempt_count += 1
            notification.cycle_attempt_count += 1
            notification.revision += 1
            try:
                async with asyncio.timeout(settings.run_outbox_enqueue_timeout_seconds):
                    await queue.enqueue(run.id)
            except Exception:
                notification.status = "pending"
                notification.last_error_code = "RUN_QUEUE_UNAVAILABLE"
                delay = min(settings.run_outbox_retry_max_seconds,
                            settings.run_outbox_retry_base_seconds * 2 ** min(notification.cycle_attempt_count - 1, 20))
                notification.next_attempt_at = now + timedelta(seconds=delay)
            else:
                notification.status = "delivered"
                notification.delivered_at = now
                notification.last_error_code = None
                notification.next_attempt_at = now + timedelta(seconds=settings.run_outbox_redelivery_seconds)
                delivered += 1
    return delivered


async def list_run_notifications(
    session: AsyncSession, *, tenant_id: UUID, status: NotificationStatus | None,
    page_size: int, offset: int,
) -> tuple[list[RunNotificationOutbox], int]:
    filters = [RunNotificationOutbox.tenant_id == tenant_id]
    if status is not None:
        filters.append(RunNotificationOutbox.status == status)
    total = int(await session.scalar(select(func.count()).select_from(RunNotificationOutbox).where(*filters)) or 0)
    items = list((await session.scalars(select(RunNotificationOutbox).where(*filters)
        .order_by(RunNotificationOutbox.created_at.desc(), RunNotificationOutbox.id.desc())
        .offset(offset).limit(page_size))).all())
    return items, total


async def retry_run_notification(
    session: AsyncSession, *, tenant_id: UUID, notification_id: UUID,
    expected_revision: int, actor_user_id: UUID,
) -> RunNotificationOutbox:
    """管理员只可恢复本租户未执行的死信；保留原 Run、输入与累计次数。"""
    notification = await session.scalar(select(RunNotificationOutbox).where(
        RunNotificationOutbox.id == notification_id,
        RunNotificationOutbox.tenant_id == tenant_id,
    ))
    if notification is None:
        raise ResourceNotFoundError("运行通知不存在")
    run = await session.scalar(select(AgentRun).where(
        AgentRun.id == notification.run_id, AgentRun.tenant_id == tenant_id,
    ).with_for_update())
    notification = await session.scalar(select(RunNotificationOutbox).where(
        RunNotificationOutbox.id == notification_id, RunNotificationOutbox.tenant_id == tenant_id,
    ).execution_options(populate_existing=True).with_for_update())
    if notification is None:
        raise ResourceNotFoundError("运行通知不存在")
    if notification.revision != expected_revision or notification.status != "dead_letter":
        raise RunNotificationConflictError("运行通知状态已变化或不是死信，请刷新后重试")
    if run is None or run.status != RunStatus.QUEUED:
        raise RunNotificationConflictError("Run 已执行或已结束，不能重新分发")
    conversation = await session.scalar(select(Conversation).where(
        Conversation.id == run.conversation_id, Conversation.tenant_id == tenant_id,
    ))
    if conversation is None:
        raise RunNotificationConflictError("会话已不可用，不能重新分发")
    now = datetime.now(UTC)
    notification.status = "pending"
    notification.cycle_attempt_count = 0
    notification.retry_count += 1
    notification.revision += 1
    notification.window_started_at = now
    notification.next_attempt_at = now
    notification.last_error_code = None
    if run.recovery_reason == "RUN_NOTIFICATION_EXHAUSTED":
        run.recovery_reason = None
    await append_event(session, run_id=run.id, event_type="run.dispatch_retried", data={
        "notification_id": str(notification.id), "actor_user_id": str(actor_user_id),
        "retry_count": notification.retry_count, "attempt_count": notification.attempt_count,
    })
    return notification
