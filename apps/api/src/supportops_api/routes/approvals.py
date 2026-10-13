"""指定非本人审批与显式 Mock 项目授权；列表权限过滤先于分页。"""

from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.approval_services import (
    approval_visibility, cancel_approval, decide_approval, live_identity, read_approval,
    request_action_approval, require_mock, set_mock_resource_grant,
    STAFF_ROLES, mock_project_id,
)
from supportops_core.action_proposal_services import get_action_for_user
from supportops_core.auth import IdentityContext
from supportops_core.models import ApprovalDecision, ApprovalRequest, MockResourceGrant, User, utc_now
from supportops_core.tool_policy import ToolPolicyError
from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    ApprovalCancelRequest, ApprovalCreateRequest, ApprovalDecisionRequest, ApprovalDetailResponse,
    ApprovalListResponse, ApprovalSummaryResponse, MockResourceGrantListResponse,
    MockResourceGrantRequest, MockResourceGrantResponse,
    ApprovalApproverResponse, ApprovalApproverListResponse,
)

router = APIRouter(prefix="/v1", tags=["approvals"])


@router.get("/actions/{action_id}/approvers", response_model=ApprovalApproverListResponse)
async def list_action_approvers(action_id: UUID, request: Request, context_run_id: UUID | None = Query(default=None),
    keywords: str = Query(default="", max_length=200), pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ApprovalApproverListResponse:
    require_mock(settings_from(request))
    action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    filters = [MockResourceGrant.tenant_id == action.tenant_id, MockResourceGrant.owner_user_id == action.proposed_by,
        MockResourceGrant.project_id == mock_project_id(action.tenant_id, action.proposed_by),
        MockResourceGrant.permission == "approve", MockResourceGrant.is_enabled.is_(True),
        User.tenant_id == action.tenant_id, User.status == "active", User.id != action.proposed_by,
        or_(*(User.roles.cast(JSONB).contains([role]) for role in sorted(STAFF_ROLES)))]
    if keywords.strip():
        escaped = keywords.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(User.display_name.ilike("%" + escaped + "%", escape="\\"))
    query = select(User.id, User.display_name).join(MockResourceGrant, MockResourceGrant.user_id == User.id).where(*filters)
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = (await session.execute(query.order_by(User.display_name, User.id).offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return ApprovalApproverListResponse(items=[ApprovalApproverResponse(user_id=row.id, display_name=row.display_name) for row in rows],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


async def _detail(session: AsyncSession, approval_id: UUID, identity: IdentityContext) -> ApprovalDetailResponse:
    approval, action = await read_approval(session, request_id=approval_id, identity=identity)
    decision = await session.scalar(select(ApprovalDecision).where(ApprovalDecision.request_id == approval.id,
        ApprovalDecision.tenant_id == approval.tenant_id))
    from datetime import UTC
    expires = approval.expires_at.replace(tzinfo=UTC) if approval.expires_at.tzinfo is None else approval.expires_at
    return ApprovalDetailResponse(**ApprovalSummaryResponse.model_validate(approval).model_dump(),
        parameters=action.parameters, risk_level=action.risk_level, action_status=action.status,
        decision_reason=decision.reason if decision is not None else None,
        can_decide=approval.status == "pending" and expires > utc_now() and approval.approver_id == identity.user.id and approval.applicant_id != identity.user.id,
        can_cancel=approval.status in {"pending", "approved"} and action.status in {"waiting_approval", "queued"} and approval.applicant_id == identity.user.id)


@router.post("/actions/{action_id}/approval", response_model=ApprovalDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_approval(action_id: UUID, payload: ApprovalCreateRequest, request: Request,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ApprovalDetailResponse:
    async with session.begin():
        approval = await request_action_approval(session, action_id=action_id, identity=identity, settings=settings_from(request),
            approver_id=payload.approver_id, expected_generation=payload.expected_generation,
            expected_parameters_digest=payload.expected_parameters_digest, expected_catalog_version=payload.expected_catalog_version,
            context_run_id=payload.context_run_id)
    return await _detail(session, approval.id, identity)


@router.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(request: Request, state: str | None = Query(default=None, max_length=20),
    action_id: UUID | None = Query(default=None), pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ApprovalListResponse:
    require_mock(settings_from(request))
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    filters = [approval_visibility(current)]
    if state is not None:
        filters.append(ApprovalRequest.status == state)
    if action_id is not None:
        filters.append(ApprovalRequest.action_id == action_id)
    total = int(await session.scalar(select(func.count()).select_from(ApprovalRequest).where(*filters)) or 0)
    items = (await session.scalars(select(ApprovalRequest).where(*filters).order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return ApprovalListResponse(items=[ApprovalSummaryResponse.model_validate(item) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/approvals/{approval_id}", response_model=ApprovalDetailResponse)
async def get_approval(approval_id: UUID, request: Request, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> ApprovalDetailResponse:
    require_mock(settings_from(request))
    return await _detail(session, approval_id, identity)


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalDetailResponse)
async def decision_approval(approval_id: UUID, payload: ApprovalDecisionRequest, request: Request,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ApprovalDetailResponse:
    async with session.begin():
        approval = await decide_approval(session, request_id=approval_id, identity=identity, settings=settings_from(request),
            expected_revision=payload.expected_revision, decision=payload.decision, reason=payload.reason)
    return await _detail(session, approval.id, identity)


@router.post("/approvals/{approval_id}/cancel", response_model=ApprovalDetailResponse)
async def cancel_waiting_approval(approval_id: UUID, payload: ApprovalCancelRequest, request: Request,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ApprovalDetailResponse:
    async with session.begin():
        approval = await cancel_approval(session, request_id=approval_id, identity=identity, settings=settings_from(request),
            expected_revision=payload.expected_revision)
    return await _detail(session, approval.id, identity)


@router.post("/admin/mock-resource-grants", response_model=MockResourceGrantResponse)
async def grant_mock_resource(payload: MockResourceGrantRequest, request: Request,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> MockResourceGrantResponse:
    async with session.begin():
        grant = await set_mock_resource_grant(session, identity=identity, settings=settings_from(request),
            owner_user_id=payload.owner_user_id, user_id=payload.user_id, permission=payload.permission,
            is_enabled=payload.is_enabled, expected_revision=payload.expected_revision, correlation_id=request.state.correlation_id)
    return MockResourceGrantResponse.model_validate(grant)


@router.get("/admin/mock-resource-grants", response_model=MockResourceGrantListResponse)
async def list_mock_grants(request: Request, owner_user_id: UUID | None = Query(default=None),
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> MockResourceGrantListResponse:
    require_mock(settings_from(request))
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    if "platform_admin" not in current.principal.roles:
        raise ToolPolicyError("MOCK_GRANT_ADMIN_REQUIRED", "仅租户管理员可读取授权控制面")
    filters = [MockResourceGrant.tenant_id == current.user.tenant_id]
    if owner_user_id is not None:
        filters.append(MockResourceGrant.owner_user_id == owner_user_id)
    total = int(await session.scalar(select(func.count()).select_from(MockResourceGrant).where(*filters)) or 0)
    items = (await session.scalars(select(MockResourceGrant).where(*filters).order_by(MockResourceGrant.updated_at.desc(), MockResourceGrant.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return MockResourceGrantListResponse(items=[MockResourceGrantResponse.model_validate(item) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)
