from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import (
    count_available_agents,
    get_usable_agent,
    list_available_agents,
)
from supportops_core.auth import IdentityContext
from supportops_core.models import Agent

from supportops_api.dependencies import current_identity, database_session
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import AgentCatalogResponse, AgentSafeProfile

router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _safe_profile(agent: Agent) -> AgentSafeProfile:
    if agent.active_version_id is None:
        raise RuntimeError("可用 Agent 缺少活动版本")
    return AgentSafeProfile(
        id=agent.id,
        slug=agent.slug,
        name=agent.name,
        logo_url=agent.logo_url,
        description=agent.description,
        welcome_message=agent.welcome_message,
        suggested_prompts=agent.suggested_prompts,
        active_version_id=agent.active_version_id,
    )


@router.get("", response_model=AgentCatalogResponse)
async def read_available_agents(
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentCatalogResponse:
    agents = await list_available_agents(
        session,
        identity=identity,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_available_agents(session, identity=identity)
    metadata = pagination_metadata(total, pagination)
    return AgentCatalogResponse(
        items=[_safe_profile(agent) for agent in agents],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.get("/{agent_id}", response_model=AgentSafeProfile)
async def read_available_agent(
    agent_id: UUID,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentSafeProfile:
    agent = await get_usable_agent(session, agent_id=agent_id, identity=identity)
    return _safe_profile(agent)
