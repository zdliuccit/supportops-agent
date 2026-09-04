from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import get_agent, get_usable_agent
from supportops_core.auth import IdentityContext
from supportops_core.models import Agent, AgentVersion, Conversation
from supportops_core.queue import RunQueue
from supportops_core.services import (
    create_message_and_run,
    delete_conversation,
    get_conversation,
    list_conversations,
    update_conversation,
)

from supportops_api.dependencies import current_identity, database_session, queue_from
from supportops_api.schemas import (
    AgentSafeProfile,
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
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
    session: AsyncSession, conversation: Conversation
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
        messages=[MessageResponse.model_validate(message) for message in conversation.messages]
        if "messages" in conversation.__dict__
        else [],
    )


@router.get("", response_model=ConversationListResponse)
async def read_conversations(
    agent_id: UUID = Query(),
    limit: int = Query(default=100, ge=1, le=200),
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> ConversationListResponse:
    conversations = await list_conversations(
        session,
        tenant_id=identity.principal.tenant_id,
        user_id=identity.user.id,
        agent_id=agent_id,
        limit=limit,
    )
    return ConversationListResponse(
        items=[
            ConversationSummaryResponse.model_validate(conversation)
            for conversation in conversations
        ]
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
    return await _conversation_response(session, conversation)


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
    return await _conversation_response(session, conversation)


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
        )
    if result.created:
        try:
            await queue.enqueue(result.run.id)
        except Exception:
            logger.exception("run_enqueue_failed", run_id=str(result.run.id))
    return MessageAccepted(
        message_id=result.message.id,
        run_id=result.run.id,
        status=result.run.status,
        events_url=f"/v1/runs/{result.run.id}/events",
        agent_id=result.run.agent_id,
        agent_version_id=result.run.agent_version_id,
        model_endpoint_version_id=result.run.model_endpoint_version_id,
    )
