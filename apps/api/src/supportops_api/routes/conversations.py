from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import get_agent, get_usable_agent
from supportops_core.auth import IdentityContext
from supportops_core.models import Agent, AgentRun, AgentVersion, Conversation, RunAttempt, utc_now
from supportops_core.ticket_takeover_services import conversation_takeover_count
from supportops_api.schemas import ConversationSupportTakeoverResponse
from supportops_core.context_services import ContextSnapshot
from supportops_core.queue import RunQueue
from supportops_core.run_outbox_services import dispatch_run_notifications
from supportops_core.support_result_services import project_support_result
from supportops_core.runtime_errors import runtime_error_message
from supportops_core.services import (
    count_conversations,
    create_message_and_run,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_events_after,
    update_conversation,
)

from supportops_api.dependencies import current_identity, database_session, queue_from, session_factory_from, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    AgentSafeProfile,
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    ConversationRunState,
    ConversationSummaryResponse,
    ConversationUpdate,
    MessageAccepted,
    MessageCreate,
    MessageResponse,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


def _safe_agent(agent: Agent, *, fallback_version_id: UUID) -> AgentSafeProfile:
    return AgentSafeProfile(
        id=agent.id,
        slug=agent.slug,
        name=agent.name,
        logo_url=agent.logo_url,
        description=agent.description,
        welcome_message=agent.welcome_message,
        suggested_prompts=agent.suggested_prompts,
        active_version_id=agent.active_version_id or fallback_version_id,
    )


async def _conversation_response(
    session: AsyncSession, conversation: Conversation, identity: IdentityContext,
) -> ConversationResponse:
    agent = await get_agent(
        session,
        agent_id=conversation.agent_id,
        tenant_id=conversation.tenant_id,
    )
    agent_version = await session.scalar(
        select(AgentVersion).where(
            AgentVersion.id == conversation.agent_version_id,
            AgentVersion.agent_id == conversation.agent_id,
            AgentVersion.tenant_id == conversation.tenant_id,
        )
    )
    if agent_version is None:
        raise RuntimeError("会话固定的 Agent 版本不存在")
    # 与 append_event 的 Run 行锁配合，正文、状态和游标属于同一快照。
    # SSE 日志是完整续传序列，不能按普通列表分页截断。
    run = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.conversation_id == conversation.id,
            AgentRun.tenant_id == conversation.tenant_id,
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
        .with_for_update(read=True)
    )
    latest_run = None
    if run is not None:
        attempt = await session.get(RunAttempt, run.current_attempt_id) if run.current_attempt_id is not None else None
        context = ContextSnapshot.model_validate(attempt.context_snapshot) if attempt is not None and attempt.context_snapshot else None
        events = await list_events_after(session, run_id=run.id, sequence=0)
        content = ""
        for event in events:
            if event.event_type == "text.delta" and isinstance(event.data.get("delta"), str):
                content += event.data["delta"]
            elif event.event_type == "text.replace" and isinstance(event.data.get("content"), str):
                content = event.data["content"]
        latest_run = ConversationRunState(
            id=run.id,
            input_message_id=run.input_message_id,
            status=run.status,
            last_event_id=events[-1].sequence if events else 0,
            content=content,
            input_sequence=run.input_sequence,
            current_attempt_id=run.current_attempt_id,
            execution_generation=run.execution_generation,
            recovery_count=run.recovery_count,
            recovery_reason=run.recovery_reason,
            error_code=run.error_code,
            error_message=runtime_error_message(run.error_code),
            diagnostic_stages=context.diagnostic.stages if context is not None and context.diagnostic is not None else [],
        )
        if "messages" in conversation.__dict__:
            await session.refresh(conversation, attribute_names=["messages"])
    takeover_count = await conversation_takeover_count(session, tenant_id=conversation.tenant_id,
        owner_user_id=identity.user.id, conversation_id=conversation.id)
    return ConversationResponse(
        id=conversation.id,
        agent_id=conversation.agent_id,
        agent_version_id=conversation.agent_version_id,
        current_agent_version_id=agent.active_version_id,
        agent_version_number=agent_version.version_number,
        model_endpoint_version_id=agent_version.model_endpoint_version_id,
        agent=_safe_agent(agent, fallback_version_id=conversation.agent_version_id),
        title=conversation.title,
        is_pinned=conversation.is_pinned,
        status=conversation.status,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        latest_run=latest_run,
        support_takeover=ConversationSupportTakeoverResponse(active=takeover_count > 0, active_case_count=takeover_count, observed_at=utc_now()),
        messages=[MessageResponse.model_validate(message).model_copy(update={
            "support_result": await project_support_result(session, stored=message.support_result, identity=identity)
        }) for message in conversation.messages]
        if "messages" in conversation.__dict__
        else [],
    )


@router.get("/{conversation_id}/support-takeover", response_model=ConversationSupportTakeoverResponse)
async def read_support_takeover(conversation_id: UUID, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> ConversationSupportTakeoverResponse:
    conversation = await get_conversation(session, conversation_id=conversation_id, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    await get_usable_agent(session, agent_id=conversation.agent_id, identity=identity)
    count = await conversation_takeover_count(session, tenant_id=conversation.tenant_id, owner_user_id=identity.user.id, conversation_id=conversation.id)
    return ConversationSupportTakeoverResponse(active=count > 0, active_case_count=count, observed_at=utc_now())


@router.get("", response_model=ConversationListResponse)
async def read_conversations(
    agent_id: UUID = Query(),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> ConversationListResponse:
    conversations = await list_conversations(
        session,
        tenant_id=identity.principal.tenant_id,
        user_id=identity.user.id,
        agent_id=agent_id,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_conversations(
        session,
        tenant_id=identity.principal.tenant_id,
        user_id=identity.user.id,
        agent_id=agent_id,
    )
    metadata = pagination_metadata(total, pagination)
    return ConversationListResponse(
        items=[
            ConversationSummaryResponse.model_validate(conversation)
            for conversation in conversations
        ],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> ConversationResponse:
    async with session.begin():
        agent = await get_usable_agent(session, agent_id=payload.agent_id, identity=identity)
        if agent.active_version_id is None:
            raise RuntimeError("可用 Agent 缺少活动版本")
        conversation = Conversation(
            tenant_id=identity.principal.tenant_id,
            user_id=identity.user.id,
            agent_id=agent.id,
            agent_version_id=agent.active_version_id,
            title=payload.title,
        )
        session.add(conversation)
        await session.flush()
    return await _conversation_response(session, conversation, identity)


@router.patch("/{conversation_id}", response_model=ConversationSummaryResponse)
async def change_conversation(
    conversation_id: UUID,
    payload: ConversationUpdate,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> Conversation:
    async with session.begin():
        return await update_conversation(
            session,
            conversation_id=conversation_id,
            tenant_id=identity.principal.tenant_id,
            user_id=identity.user.id,
            title=payload.title,
            is_pinned=payload.is_pinned,
        )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_conversation(
    conversation_id: UUID,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> Response:
    async with session.begin():
        await delete_conversation(
            session,
            conversation_id=conversation_id,
            tenant_id=identity.principal.tenant_id,
            user_id=identity.user.id,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def read_conversation(
    conversation_id: UUID,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> ConversationResponse:
    conversation = await get_conversation(
        session,
        conversation_id=conversation_id,
        tenant_id=identity.principal.tenant_id,
        user_id=identity.user.id,
        include_messages=True,
    )
    return await _conversation_response(session, conversation, identity)


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_message(
    conversation_id: UUID,
    payload: MessageCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
    queue: RunQueue = Depends(queue_from),
) -> MessageAccepted:
    async with session.begin():
        conversation = await get_conversation(
            session,
            conversation_id=conversation_id,
            tenant_id=identity.principal.tenant_id,
            user_id=identity.user.id,
            lock=True,
        )
        await get_usable_agent(session, agent_id=conversation.agent_id, identity=identity)
        result = await create_message_and_run(
            session,
            conversation=conversation,
            user_id=identity.user.id,
            identity_roles=identity.principal.roles,
            content=payload.content.strip(),
            idempotency_key=idempotency_key,
            correlation_id=request.state.correlation_id,
            daily_run_limit=settings_from(request).tenant_run_limit_per_day,
        )
    if result.created:
        try:
            await dispatch_run_notifications(
                session_factory_from(request), queue=queue, settings=settings_from(request),
                run_id=result.run.id, limit=1,
            )
        except Exception:
            logger.exception("run_outbox_dispatch_failed", run_id=str(result.run.id))
    return MessageAccepted(
        message_id=result.message.id,
        run_id=result.run.id,
        status=result.run.status,
        events_url=f"/v1/runs/{result.run.id}/events",
        agent_id=result.run.agent_id,
        agent_version_id=result.run.agent_version_id,
        model_endpoint_version_id=result.run.model_endpoint_version_id,
    )
