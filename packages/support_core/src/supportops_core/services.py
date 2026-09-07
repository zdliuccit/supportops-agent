"""Conversation、Message、AgentRun 和 RunEvent 的核心事务服务。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from supportops_core.enums import ALLOWED_RUN_TRANSITIONS, MessageRole, RunStatus
from supportops_core.models import (
    AgentRun,
    AgentVersion,
    Conversation,
    Message,
    ModelCredential,
    ModelEndpointVersion,
    RunEvent,
    utc_now,
)


class ResourceNotFoundError(LookupError):
    pass


class IdempotencyConflictError(ValueError):
    pass


class InvalidRunTransitionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MessageRunResult:
    """同一事务内创建的用户消息与异步 Run。"""

    message: Message
    run: AgentRun
    created: bool


def content_digest(content: str) -> str:
    """计算消息正文摘要，用于检测幂等键载荷冲突。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def get_conversation(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    agent_id: UUID | None = None,
    include_messages: bool = False,
) -> Conversation:
    """按租户和用户读取会话，可选一次性加载产品消息历史。"""
    query = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
        Conversation.user_id == user_id,
    )
    if agent_id is not None:
        query = query.where(Conversation.agent_id == agent_id)
    if include_messages:
        query = query.options(selectinload(Conversation.messages))
    conversation = await session.scalar(query)
    if conversation is None:
        raise ResourceNotFoundError("会话不存在")
    return conversation


async def list_conversations(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    agent_id: UUID,
    page_size: int = 20,
    offset: int = 0,
) -> list[Conversation]:
    return list(
        (
            await session.scalars(
                select(Conversation)
                .where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.user_id == user_id,
                    Conversation.agent_id == agent_id,
                )
                .order_by(
                    Conversation.is_pinned.desc(),
                    Conversation.updated_at.desc(),
                    Conversation.id.desc(),
                )
                .limit(page_size)
                .offset(offset)
            )
        ).all()
    )


async def count_conversations(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    agent_id: UUID,
) -> int:
    """统计当前用户在指定 Agent 下可见的会话总数。"""

    total = await session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.user_id == user_id,
            Conversation.agent_id == agent_id,
        )
    )
    return int(total or 0)


async def update_conversation(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    title: str | None = None,
    is_pinned: bool | None = None,
) -> Conversation:
    conversation = await get_conversation(
        session,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if title is not None:
        conversation.title = title
    if is_pinned is not None:
        conversation.is_pinned = is_pinned
    conversation.updated_at = utc_now()
    await session.flush()
    return conversation


async def delete_conversation(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
) -> None:
    """按依赖顺序删除会话的事件、Run、消息和会话记录。"""
    conversation = await get_conversation(
        session,
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    run_ids = select(AgentRun.id).where(AgentRun.conversation_id == conversation.id)
    await session.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
    await session.execute(delete(AgentRun).where(AgentRun.conversation_id == conversation.id))
    await session.execute(delete(Message).where(Message.conversation_id == conversation.id))
    await session.delete(conversation)
    await session.flush()


async def create_message_and_run(
    session: AsyncSession,
    *,
    conversation: Conversation,
    user_id: UUID,
    identity_roles: tuple[str, ...],
    content: str,
    idempotency_key: str,
    correlation_id: str,
) -> MessageRunResult:
    """原子保存用户消息并复制会话的固定版本创建 Run。"""
    agent_version = await session.scalar(
        select(AgentVersion).where(
            AgentVersion.id == conversation.agent_version_id,
            AgentVersion.agent_id == conversation.agent_id,
            AgentVersion.tenant_id == conversation.tenant_id,
        )
    )
    if agent_version is None:
        raise ResourceNotFoundError("会话固定的 Agent 版本不存在")
    model_version = await session.scalar(
        select(ModelEndpointVersion).where(
            ModelEndpointVersion.id == agent_version.model_endpoint_version_id,
            ModelEndpointVersion.tenant_id == conversation.tenant_id,
        )
    )
    if model_version is None:
        raise ResourceNotFoundError("Agent 固定的模型端点版本不存在")
    credential = await session.scalar(
        select(ModelCredential).where(
            ModelCredential.id == model_version.credential_id,
            ModelCredential.tenant_id == conversation.tenant_id,
        )
    )
    if credential is None:
        raise ResourceNotFoundError("Agent 模型凭据不存在")
    digest = content_digest(content)
    existing = await session.scalar(
        select(Message).where(
            Message.tenant_id == conversation.tenant_id,
            Message.user_id == user_id,
            Message.conversation_id == conversation.id,
            Message.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.content_hash != digest:
            raise IdempotencyConflictError("幂等键已用于不同的消息内容")
        run = await session.scalar(select(AgentRun).where(AgentRun.input_message_id == existing.id))
        if run is None:
            raise RuntimeError("幂等消息缺少关联 Run")
        return MessageRunResult(message=existing, run=run, created=False)

    message = Message(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        user_id=user_id,
        role=MessageRole.USER,
        content=content,
        content_hash=digest,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    session.add(message)
    await session.flush()

    run = AgentRun(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        model_endpoint_version_id=agent_version.model_endpoint_version_id,
        credential_revision=credential.revision,
        config_digest=agent_version.config_digest,
        identity_roles=list(identity_roles),
        input_message_id=message.id,
        status=RunStatus.QUEUED,
        correlation_id=correlation_id,
    )
    session.add(run)
    conversation.updated_at = utc_now()
    await session.flush()
    await append_event(
        session,
        run_id=run.id,
        event_type="run.queued",
        data={"status": RunStatus.QUEUED.value},
    )
    return MessageRunResult(message=message, run=run, created=True)


async def get_run_for_user(
    session: AsyncSession,
    *,
    run_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    lock: bool = False,
) -> AgentRun:
    query = (
        select(AgentRun)
        .join(Conversation, Conversation.id == AgentRun.conversation_id)
        .where(
            AgentRun.id == run_id,
            AgentRun.tenant_id == tenant_id,
            Conversation.user_id == user_id,
        )
    )
    if lock:
        query = query.with_for_update()
    run = await session.scalar(query)
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    return run


def transition_run(run: AgentRun, target: RunStatus, *, now: datetime | None = None) -> None:
    """只允许状态机声明的转换，并同步开始或终止时间。"""
    current = RunStatus(run.status)
    if target not in ALLOWED_RUN_TRANSITIONS[current]:
        raise InvalidRunTransitionError(f"不允许从 {current.value} 转为 {target.value}")
    timestamp = now or utc_now()
    run.status = target
    run.updated_at = timestamp
    if target == RunStatus.RUNNING:
        run.started_at = timestamp
    if target in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        run.finished_at = timestamp


async def append_event(
    session: AsyncSession,
    *,
    run_id: UUID,
    event_type: str,
    data: dict[str, Any],
) -> RunEvent:
    """在 Run 行锁保护下追加严格递增的事件序号。"""
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    last_sequence = await session.scalar(
        select(func.coalesce(func.max(RunEvent.sequence), 0)).where(RunEvent.run_id == run_id)
    )
    event = RunEvent(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=int(last_sequence or 0) + 1,
        event_type=event_type,
        data=data,
        correlation_id=run.correlation_id,
    )
    session.add(event)
    await session.flush()
    return event


async def list_events_after(
    session: AsyncSession, *, run_id: UUID, sequence: int
) -> list[RunEvent]:
    return list(
        (
            await session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.sequence > sequence)
                .order_by(RunEvent.sequence)
            )
        ).all()
    )


async def cancel_run(session: AsyncSession, run: AgentRun) -> AgentRun:
    current = RunStatus(run.status)
    if current in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        return run
    if current == RunStatus.QUEUED:
        transition_run(run, RunStatus.CANCELLED)
        await append_event(
            session,
            run_id=run.id,
            event_type="run.cancelled",
            data={"status": RunStatus.CANCELLED.value},
        )
    elif current == RunStatus.RUNNING:
        transition_run(run, RunStatus.CANCELLING)
        await append_event(
            session,
            run_id=run.id,
            event_type="run.cancelling",
            data={"status": RunStatus.CANCELLING.value},
        )
    return run


async def claim_run(session: AsyncSession, run_id: UUID) -> AgentRun | None:
    """幂等领取 queued Run；重复投递不会再次执行。"""
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None or RunStatus(run.status) != RunStatus.QUEUED:
        return None
    transition_run(run, RunStatus.RUNNING)
    await append_event(
        session,
        run_id=run.id,
        event_type="run.started",
        data={"status": RunStatus.RUNNING.value},
    )
    return run


async def recover_queued_runs(
    session_factory: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> list[UUID]:
    """扫描持久化 queued Run，供 Worker 重启后重新入队。"""
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(AgentRun.id)
                    .where(AgentRun.status == RunStatus.QUEUED)
                    .order_by(AgentRun.created_at)
                    .limit(limit)
                )
            ).all()
        )
