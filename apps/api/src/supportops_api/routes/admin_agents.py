from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_config import agent_config_digest, parse_agent_config
from supportops_core.agent_services import (
    activate_agent_version,
    count_admin_agents,
    count_agent_audit_events,
    count_agent_grants,
    count_agent_versions,
    create_agent,
    disable_agent,
    get_agent,
    get_agent_draft,
    list_admin_agents,
    list_agent_audit_events,
    list_agent_grants,
    list_agent_versions,
    publish_agent_version,
    replace_agent_grants,
    require_platform_admin,
    update_agent_draft,
    update_agent_profile,
    validate_agent_config,
)
from supportops_core.auth import IdentityContext
from supportops_core.enums import AgentStatus
from supportops_core.models import Agent, AgentDraft, AgentVersion

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    AdminAgentCreate,
    AdminAgentListResponse,
    AdminAgentResponse,
    AgentDraftResponse,
    AgentDraftUpdate,
    AgentGrantListResponse,
    AgentGrantReplace,
    AgentGrantResponse,
    AgentProfileUpdate,
    AgentPublishRequest,
    AgentValidateRequest,
    AgentValidateResponse,
    AgentVersionListResponse,
    AgentVersionResponse,
    AuditEventListResponse,
    AuditEventResponse,
)

router = APIRouter(prefix="/v1/admin/agents", tags=["admin-agents"])


async def platform_admin_identity(
    identity: IdentityContext = Depends(current_identity),
) -> IdentityContext:
    require_platform_admin(identity)
    return identity


def _agent_response(agent: Agent) -> AdminAgentResponse:
    return AdminAgentResponse.model_validate(agent)


def _draft_response(draft: AgentDraft) -> AgentDraftResponse:
    config = parse_agent_config(draft.config)
    return AgentDraftResponse(
        agent_id=draft.agent_id,
        revision=draft.revision,
        schema_version=draft.schema_version,
        config=draft.config,
        config_digest=agent_config_digest(config),
        updated_at=draft.updated_at,
    )


@router.get("", response_model=AdminAgentListResponse)
async def read_agents(
    status_filter: AgentStatus | None = Query(default=None, alias="status"),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminAgentListResponse:
    agents = await list_admin_agents(
        session,
        tenant_id=identity.principal.tenant_id,
        status=status_filter,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_admin_agents(
        session, tenant_id=identity.principal.tenant_id, status=status_filter
    )
    revisions = {
        draft.agent_id: draft.revision
        for draft in (
            await session.scalars(
                select(AgentDraft).where(
                    AgentDraft.agent_id.in_([agent.id for agent in agents]),
                    AgentDraft.tenant_id == identity.principal.tenant_id,
                )
            )
        ).all()
    }
    items = [
        AdminAgentResponse.model_validate(agent).model_copy(
            update={"draft_revision": revisions.get(agent.id)}
        )
        for agent in agents
    ]
    metadata = pagination_metadata(total, pagination)
    return AdminAgentListResponse(
        items=items,
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.post("", response_model=AdminAgentResponse, status_code=status.HTTP_201_CREATED)
async def add_agent(
    payload: AdminAgentCreate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminAgentResponse:
    async with session.begin():
        agent = await create_agent(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            slug=payload.slug,
            name=payload.name,
            logo_url=payload.logo_url,
            description=payload.description,
            welcome_message=payload.welcome_message,
            suggested_prompts=payload.suggested_prompts,
            config=payload.config,
            correlation_id=request.state.correlation_id,
        )
    return _agent_response(agent)


@router.get("/{agent_id}", response_model=AdminAgentResponse)
async def read_agent(
    agent_id: UUID,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminAgentResponse:
    agent = await get_agent(session, agent_id=agent_id, tenant_id=identity.principal.tenant_id)
    active_version_config_digest = None
    active_version_published_at = None
    if agent.active_version_id is not None:
        active_version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.id == agent.active_version_id,
                AgentVersion.agent_id == agent.id,
                AgentVersion.tenant_id == identity.principal.tenant_id,
            )
        )
        if active_version is not None:
            active_version_config_digest = active_version.config_digest
            active_version_published_at = active_version.published_at
    return _agent_response(agent).model_copy(
        update={
            "active_version_config_digest": active_version_config_digest,
            "active_version_published_at": active_version_published_at,
        }
    )


@router.patch("/{agent_id}/profile", response_model=AdminAgentResponse)
async def change_agent_profile(
    agent_id: UUID,
    payload: AgentProfileUpdate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminAgentResponse:
    async with session.begin():
        agent = await update_agent_profile(
            session,
            agent_id=agent_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            name=payload.name,
            logo_url=payload.logo_url,
            description=payload.description,
            welcome_message=payload.welcome_message,
            suggested_prompts=payload.suggested_prompts,
            correlation_id=request.state.correlation_id,
        )
    return _agent_response(agent)


@router.get("/{agent_id}/draft", response_model=AgentDraftResponse)
async def read_agent_draft(
    agent_id: UUID,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentDraftResponse:
    draft = await get_agent_draft(
        session, agent_id=agent_id, tenant_id=identity.principal.tenant_id
    )
    return _draft_response(draft)


@router.patch("/{agent_id}/draft", response_model=AgentDraftResponse)
async def change_agent_draft(
    agent_id: UUID,
    payload: AgentDraftUpdate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentDraftResponse:
    async with session.begin():
        draft = await update_agent_draft(
            session,
            agent_id=agent_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            expected_revision=payload.expected_revision,
            config=payload.config,
            correlation_id=request.state.correlation_id,
        )
    return _draft_response(draft)


@router.post("/{agent_id}/draft/validate", response_model=AgentValidateResponse)
async def check_agent_config(
    agent_id: UUID,
    payload: AgentValidateRequest,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentValidateResponse:
    await get_agent(session, agent_id=agent_id, tenant_id=identity.principal.tenant_id)
    resolved = await validate_agent_config(
        session,
        tenant_id=identity.principal.tenant_id,
        config=payload.config,
        settings=settings_from(request),
    )
    return AgentValidateResponse(
        model_endpoint_version_id=resolved.model_version.id,
        credential_revision=resolved.credential_revision,
        tool_ids=resolved.tool_ids,
    )


@router.post("/{agent_id}/versions", response_model=AgentVersionResponse)
async def publish_agent(
    agent_id: UUID,
    payload: AgentPublishRequest,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentVersionResponse:
    async with session.begin():
        version = await publish_agent_version(
            session,
            agent_id=agent_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            expected_revision=payload.expected_revision,
            release_notes=payload.release_notes,
            settings=settings_from(request),
            correlation_id=request.state.correlation_id,
        )
        if payload.activate:
            await activate_agent_version(
                session,
                agent_id=agent_id,
                version_id=version.id,
                tenant_id=identity.principal.tenant_id,
                actor_user_id=identity.user.id,
                correlation_id=request.state.correlation_id,
            )
    return AgentVersionResponse.model_validate(version)


@router.get("/{agent_id}/versions", response_model=AgentVersionListResponse)
async def read_agent_versions(
    agent_id: UUID,
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentVersionListResponse:
    versions = await list_agent_versions(
        session,
        agent_id=agent_id,
        tenant_id=identity.principal.tenant_id,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_agent_versions(
        session, agent_id=agent_id, tenant_id=identity.principal.tenant_id
    )
    metadata = pagination_metadata(total, pagination)
    return AgentVersionListResponse(
        items=[AgentVersionResponse.model_validate(version) for version in versions],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.post("/{agent_id}/versions/{version_id}/activate", response_model=AdminAgentResponse)
async def activate_agent(
    agent_id: UUID,
    version_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminAgentResponse:
    async with session.begin():
        agent = await activate_agent_version(
            session,
            agent_id=agent_id,
            version_id=version_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            correlation_id=request.state.correlation_id,
        )
    return _agent_response(agent)


@router.post("/{agent_id}/disable", response_model=AdminAgentResponse)
async def turn_off_agent(
    agent_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminAgentResponse:
    async with session.begin():
        agent = await disable_agent(
            session,
            agent_id=agent_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            correlation_id=request.state.correlation_id,
        )
    return _agent_response(agent)


@router.get("/{agent_id}/grants", response_model=AgentGrantListResponse)
async def read_agent_grants(
    agent_id: UUID,
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentGrantListResponse:
    grants = await list_agent_grants(
        session,
        agent_id=agent_id,
        tenant_id=identity.principal.tenant_id,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_agent_grants(
        session, agent_id=agent_id, tenant_id=identity.principal.tenant_id
    )
    metadata = pagination_metadata(total, pagination)
    return AgentGrantListResponse(
        items=[AgentGrantResponse.model_validate(grant) for grant in grants],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.put("/{agent_id}/grants", response_model=AgentGrantListResponse)
async def change_agent_grants(
    agent_id: UUID,
    payload: AgentGrantReplace,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentGrantListResponse:
    async with session.begin():
        grants = await replace_agent_grants(
            session,
            agent_id=agent_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            grants=[(grant.subject_type, grant.subject_id) for grant in payload.grants],
            correlation_id=request.state.correlation_id,
        )
    metadata = pagination_metadata(
        len(grants), PaginationParams(page=1, page_size=max(len(grants), 1))
    )
    return AgentGrantListResponse(
        items=[AgentGrantResponse.model_validate(grant) for grant in grants],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.get("/{agent_id}/audit-events", response_model=AuditEventListResponse)
async def read_agent_audit(
    agent_id: UUID,
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AuditEventListResponse:
    events = await list_agent_audit_events(
        session,
        agent_id=agent_id,
        tenant_id=identity.principal.tenant_id,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_agent_audit_events(
        session, agent_id=agent_id, tenant_id=identity.principal.tenant_id
    )
    metadata = pagination_metadata(total, pagination)
    return AuditEventListResponse(
        items=[AuditEventResponse.model_validate(event) for event in events],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )
