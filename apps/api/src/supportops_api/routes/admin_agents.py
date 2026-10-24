from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_config import agent_config_digest, parse_agent_config
from supportops_core.agent_services import (
    activate_agent_version,
    append_agent_audit,
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
from supportops_core.enums import AgentStatus, AuditAction
from supportops_core.models import Agent, AgentDraft, AgentVersion
from supportops_core.release_manifest_services import AgentReleaseComparison, compare_release_versions, project_release_manifest
from supportops_core.release_gate_services import (
    ReleaseReadiness, ReleaseActivationDenied, assess_release_readiness, list_release_evaluations,
)
from supportops_core.services import ResourceNotFoundError
from supportops_core.evaluation_failure_contracts import (
    EvaluationFailureDocument, EvaluationFailureReview, EvaluationFailureReviewInput,
)
from supportops_core.evaluation_failure_services import get_evaluation_failures, record_evaluation_failure_review
from supportops_core.release_rollout_contracts import (
    ReleaseRolloutCreate, ReleaseRolloutCommand, ReleaseRolloutDetail, ReleaseRolloutSummary,
)
from supportops_core.release_rollout_services import (
    create_release_rollout, command_release_rollout, get_release_rollout, release_rollout_detail,
    rollout_summary, list_release_rollouts, list_release_rollout_events,
)

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
    ReleaseEvaluationListResponse,
    ReleaseRolloutListResponse,
    ReleaseRolloutEventListResponse,
    ReleaseGateErrorResponse,
    ErrorResponse,
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
    keywords: str | None = Query(default=None, description="按 Agent 名称或 slug 搜索"),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminAgentListResponse:
    agents = await list_admin_agents(
        session,
        tenant_id=identity.principal.tenant_id,
        status=status_filter,
        keywords=keywords,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_admin_agents(
        session, tenant_id=identity.principal.tenant_id, status=status_filter, keywords=keywords
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


@router.post("/{agent_id}/versions", response_model=AgentVersionResponse,
    responses={409: {"model": ReleaseGateErrorResponse | ErrorResponse}})
async def publish_agent(
    agent_id: UUID,
    payload: AgentPublishRequest,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AgentVersionResponse:
    try:
        async with session.begin():
            version = await publish_agent_version(session, agent_id=agent_id,
                tenant_id=identity.principal.tenant_id, actor_user_id=identity.user.id,
                expected_revision=payload.expected_revision, release_notes=payload.release_notes,
                settings=settings_from(request), correlation_id=request.state.correlation_id)
            # 保留旧调用的原子契约；新管理台先保存候选，再显式激活。
            if payload.activate:
                await activate_agent_version(session, agent_id=agent_id, version_id=version.id,
                    tenant_id=identity.principal.tenant_id, actor_user_id=identity.user.id,
                    settings=settings_from(request), correlation_id=request.state.correlation_id)
    except ReleaseActivationDenied as exc:
        # 新候选与激活已一起回滚，审计不能引用不存在的版本外键。
        await _append_gate_denial(session, agent_id=agent_id, version_id=None, identity=identity,
            request=request, denied=exc)
        raise
    return _version_response(version)


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
        items=[_version_response(version) for version in versions],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


def _version_response(version: AgentVersion) -> AgentVersionResponse:
    manifest_status, manifest = project_release_manifest(version)
    return AgentVersionResponse(id=version.id, agent_id=version.agent_id, version_number=version.version_number,
        schema_version=version.schema_version, config_digest=version.config_digest,
        model_endpoint_version_id=version.model_endpoint_version_id, resolved_tool_ids=version.resolved_tool_ids,
        resolved_tool_versions=version.resolved_tool_versions, release_manifest=manifest,
        release_manifest_digest=version.release_manifest_digest if manifest_status == "recorded" else None,
        release_manifest_status=manifest_status, release_notes=version.release_notes, published_at=version.published_at,
        withdrawn_at=version.withdrawn_at, withdrawn_by=version.withdrawn_by)


@router.get("/{agent_id}/versions/{version_id}/comparison", response_model=AgentReleaseComparison)
async def read_release_comparison(agent_id: UUID, version_id: UUID, baseline_version_id: UUID | None = Query(default=None),
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> AgentReleaseComparison:
    """两个明确固定版本的有限元数据差异；聚合详情，不是普通分页列表。"""
    agent = await get_agent(session, agent_id=agent_id, tenant_id=identity.principal.tenant_id)
    version = await session.scalar(select(AgentVersion).where(AgentVersion.id == version_id,
        AgentVersion.agent_id == agent.id, AgentVersion.tenant_id == identity.principal.tenant_id))
    if version is None:
        raise ResourceNotFoundError("Agent 版本不存在")
    baseline_id = baseline_version_id or agent.active_version_id
    baseline = await session.scalar(select(AgentVersion).where(AgentVersion.id == baseline_id,
        AgentVersion.agent_id == agent.id, AgentVersion.tenant_id == identity.principal.tenant_id)) if baseline_id else None
    if baseline_id and baseline is None:
        raise ResourceNotFoundError("对比版本不存在")
    return compare_release_versions(version, baseline)


@router.post("/{agent_id}/versions/{version_id}/activate", response_model=AdminAgentResponse,
    responses={409: {"model": ReleaseGateErrorResponse | ErrorResponse}})
async def activate_agent(
    agent_id: UUID,
    version_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminAgentResponse:
    agent = await _activate_with_gate_audit(session, agent_id=agent_id, version_id=version_id,
        identity=identity, request=request)
    return _agent_response(agent)


async def _activate_with_gate_audit(session: AsyncSession, *, agent_id: UUID, version_id: UUID,
    identity: IdentityContext, request: Request,
) -> Agent:
    try:
        async with session.begin():
            return await activate_agent_version(session, agent_id=agent_id, version_id=version_id,
                tenant_id=identity.principal.tenant_id, actor_user_id=identity.user.id,
                settings=settings_from(request), correlation_id=request.state.correlation_id)
    except ReleaseActivationDenied as exc:
        # 原激活事务已经回滚，再追加最小拒绝事实，不保存报告/正文或任意理由。
        await _append_gate_denial(session, agent_id=agent_id, version_id=version_id, identity=identity,
            request=request, denied=exc)
        raise


async def _append_gate_denial(session: AsyncSession, *, agent_id: UUID, version_id: UUID | None,
    identity: IdentityContext, request: Request, denied: ReleaseActivationDenied,
) -> None:
    # 回滚会使 ORM 属性过期；审计身份取不可变 Principal，避免异步隐式刷新。
    async with session.begin():
        await append_agent_audit(session, tenant_id=identity.principal.tenant_id,
            agent_id=agent_id, actor_user_id=identity.principal.user_id, version_id=version_id,
            action=AuditAction.RELEASE_ACTIVATION_REJECTED, correlation_id=request.state.correlation_id,
            metadata={"policy_digest": denied.readiness.policy_digest,
                "evaluation_id": str(denied.readiness.evaluation_id) if denied.readiness.evaluation_id else None,
                "failed_codes": [check.code for check in denied.readiness.checks if check.status != "passed"]})


async def _fixed_version(session: AsyncSession, *, agent_id: UUID, version_id: UUID, tenant_id: UUID) -> AgentVersion:
    await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    version = await session.scalar(select(AgentVersion).where(AgentVersion.id == version_id,
        AgentVersion.agent_id == agent_id, AgentVersion.tenant_id == tenant_id))
    if version is None:
        raise ResourceNotFoundError("Agent 版本不存在")
    return version


@router.get("/{agent_id}/versions/{version_id}/release-readiness", response_model=ReleaseReadiness)
async def read_release_readiness(agent_id: UUID, version_id: UUID, request: Request,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ReleaseReadiness:
    """固定版本有限门禁详情，不执行模型/工具/评测，不是普通分页列表。"""
    version = await _fixed_version(session, agent_id=agent_id, version_id=version_id, tenant_id=identity.principal.tenant_id)
    return await assess_release_readiness(session, version=version, settings=settings_from(request))


@router.get("/{agent_id}/versions/{version_id}/evaluations", response_model=ReleaseEvaluationListResponse)
async def read_release_evaluations(agent_id: UUID, version_id: UUID,
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ReleaseEvaluationListResponse:
    version = await _fixed_version(session, agent_id=agent_id, version_id=version_id, tenant_id=identity.principal.tenant_id)
    items, total = await list_release_evaluations(session, version=version, offset=pagination.offset, page_size=pagination.page_size)
    metadata = pagination_metadata(total, pagination)
    return ReleaseEvaluationListResponse(items=items, total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{agent_id}/versions/{version_id}/evaluations/{evaluation_id}/failures",
    response_model=EvaluationFailureDocument, responses={409: {"model": ErrorResponse}})
async def read_evaluation_failures(
    agent_id: UUID, version_id: UUID, evaluation_id: UUID, response: Response,
    baseline_version_id: UUID | None = Query(default=None),
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> EvaluationFailureDocument:
    """固定报告的完整有界失败文档，不按普通资源列表分页；不运行评测。"""
    response.headers["Cache-Control"] = "no-store"
    version = await _fixed_version(session, agent_id=agent_id, version_id=version_id,
        tenant_id=identity.principal.tenant_id)
    return await get_evaluation_failures(session, version=version, evaluation_id=evaluation_id,
        baseline_version_id=baseline_version_id)


@router.post("/{agent_id}/versions/{version_id}/evaluations/{evaluation_id}/failures/{case_id}/reviews",
    response_model=EvaluationFailureReview, responses={409: {"model": ErrorResponse}})
async def review_evaluation_failure(
    agent_id: UUID, version_id: UUID, evaluation_id: UUID, case_id: str,
    payload: EvaluationFailureReviewInput, request: Request, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> EvaluationFailureReview:
    """追加人工诊断；保存修复/回滚决定不执行修复、回滚或放行发布。"""
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        version = await _fixed_version(session, agent_id=agent_id, version_id=version_id,
            tenant_id=identity.principal.tenant_id)
        result = await record_evaluation_failure_review(session, version=version, evaluation_id=evaluation_id,
            case_id=case_id, identity=identity, command=payload, correlation_id=request.state.correlation_id)
    return result


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


@router.get("/{agent_id}/rollouts", response_model=ReleaseRolloutListResponse)
async def read_release_rollouts(agent_id: UUID, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ReleaseRolloutListResponse:
    items, total = await list_release_rollouts(session, tenant_id=identity.principal.tenant_id,
        agent_id=agent_id, offset=pagination.offset, page_size=pagination.page_size)
    metadata = pagination_metadata(total, pagination)
    return ReleaseRolloutListResponse(items=items, total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages)


@router.post("/{agent_id}/rollouts", response_model=ReleaseRolloutSummary, status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}})
async def add_release_rollout(agent_id: UUID, payload: ReleaseRolloutCreate,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ReleaseRolloutSummary:
    async with session.begin():
        row = await create_release_rollout(session, tenant_id=identity.principal.tenant_id, agent_id=agent_id,
            actor_user_id=identity.user.id, payload=payload)
        result = rollout_summary(row)
    return result


@router.get("/{agent_id}/rollouts/{rollout_id}", response_model=ReleaseRolloutDetail,
    responses={409: {"model": ErrorResponse}})
async def read_release_rollout(agent_id: UUID, rollout_id: UUID, request: Request,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ReleaseRolloutDetail:
    row = await get_release_rollout(session, tenant_id=identity.principal.tenant_id, agent_id=agent_id, rollout_id=rollout_id)
    return await release_rollout_detail(session, rollout=row, settings=settings_from(request))


@router.post("/{agent_id}/rollouts/{rollout_id}/commands", response_model=ReleaseRolloutSummary,
    responses={409: {"model": ErrorResponse}})
async def change_release_rollout(agent_id: UUID, rollout_id: UUID, payload: ReleaseRolloutCommand, request: Request,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ReleaseRolloutSummary:
    async with session.begin():
        row = await command_release_rollout(session, tenant_id=identity.principal.tenant_id, agent_id=agent_id,
            rollout_id=rollout_id, actor_user_id=identity.user.id, payload=payload, settings=settings_from(request))
        result = rollout_summary(row)
    return result


@router.get("/{agent_id}/rollouts/{rollout_id}/events", response_model=ReleaseRolloutEventListResponse)
async def read_release_rollout_events(agent_id: UUID, rollout_id: UUID,
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ReleaseRolloutEventListResponse:
    row = await get_release_rollout(session, tenant_id=identity.principal.tenant_id, agent_id=agent_id, rollout_id=rollout_id)
    items, total = await list_release_rollout_events(session, rollout=row, offset=pagination.offset, page_size=pagination.page_size)
    metadata = pagination_metadata(total, pagination)
    return ReleaseRolloutEventListResponse(items=items, total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages)
