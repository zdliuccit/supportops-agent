"""本人资源的冻结动作提案与独立执行意图；不接受任意工具/主体/参数。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import exists, func, select
from supportops_core.action_proposal_services import (
    authorized_action_source, get_action_for_user, propose_mock_ticket, request_action_execution,
    resume_action_reconciliation,
)
from supportops_core.auth import IdentityContext
from supportops_core.models import ActionCompensationProposal, ActionReceipt, BusinessAction, RunEvent, TicketEscalationPackage
from supportops_core.compensation_contracts import CompensationCreate, CompensationDetail, CompensationReadiness
from supportops_core.compensation_services import (compensation_detail, compensation_readiness, compensation_summary,
    create_compensation_proposal, get_compensation_proposal)
from supportops_core.escalation_services import project_escalation_package
from supportops_core.services import ResourceNotFoundError

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.schemas import ActionExecutionRequest, ActionProposalRequest, BusinessActionResponse, BusinessActionListResponse, ActionReceiptListResponse, ActionReceiptResponse, ActionRecoveryRequest
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import EscalationPackageSummaryResponse, EscalationPackageListResponse, EscalationPackageDetailResponse
from supportops_api.schemas import ActionAmendRequest
from supportops_api.schemas import CompensationListResponse
from supportops_core.approval_services import replace_action_proposal

router = APIRouter(prefix="/v1/actions", tags=["actions"])


@router.get("/{action_id}/compensation-readiness", response_model=CompensationReadiness)
async def read_compensation_readiness(action_id: UUID, request: Request, response: Response, context_run_id: UUID = Query(),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> CompensationReadiness:
    response.headers["Cache-Control"] = "no-store"
    return await compensation_readiness(session, action_id=action_id, context_run_id=context_run_id,
        identity=identity, settings=settings_from(request))


@router.post("/{action_id}/compensation-proposals", response_model=CompensationDetail, status_code=201)
async def propose_compensation(action_id: UUID, payload: CompensationCreate, request: Request, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> CompensationDetail:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        row = await create_compensation_proposal(session, action_id=action_id, payload=payload,
            identity=identity, settings=settings_from(request))
    return await compensation_detail(session, row=row, context_run_id=payload.context_run_id,
        identity=identity, settings=settings_from(request))


@router.get("/{action_id}/compensation-proposals", response_model=CompensationListResponse)
async def list_compensation_proposals(action_id: UUID, response: Response, context_run_id: UUID = Query(),
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> CompensationListResponse:
    response.headers["Cache-Control"] = "no-store"
    await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    filters = [ActionCompensationProposal.tenant_id == identity.principal.tenant_id,
        ActionCompensationProposal.subject_user_id == identity.principal.user_id, ActionCompensationProposal.action_id == action_id]
    total = int(await session.scalar(select(func.count()).select_from(ActionCompensationProposal).where(*filters)) or 0)
    rows = (await session.scalars(select(ActionCompensationProposal).where(*filters)
        .order_by(ActionCompensationProposal.created_at.desc(), ActionCompensationProposal.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    page = pagination_metadata(total, pagination)
    return CompensationListResponse(items=[compensation_summary(row) for row in rows], total=page.total,
        page=page.page, page_size=page.page_size, pages=page.pages)


@router.get("/{action_id}/compensation-proposals/{proposal_id}", response_model=CompensationDetail)
async def read_compensation_proposal(action_id: UUID, proposal_id: UUID, request: Request, response: Response,
    context_run_id: UUID = Query(), identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> CompensationDetail:
    response.headers["Cache-Control"] = "no-store"
    row = await get_compensation_proposal(session, action_id=action_id, proposal_id=proposal_id,
        identity=identity, context_run_id=context_run_id)
    return await compensation_detail(session, row=row, context_run_id=context_run_id, identity=identity, settings=settings_from(request))


@router.post("/{action_id}/amend", response_model=BusinessActionResponse, status_code=status.HTTP_201_CREATED)
async def amend_action(action_id: UUID, payload: ActionAmendRequest, request: Request,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> BusinessActionResponse:
    async with session.begin():
        action = await replace_action_proposal(session, action_id=action_id, identity=identity, settings=settings_from(request),
            expected_generation=payload.expected_generation, expected_parameters_digest=payload.expected_parameters_digest,
            expected_catalog_version=payload.expected_catalog_version, context_run_id=payload.context_run_id,
            approver_id=payload.approver_id, title=payload.title, summary=payload.summary)
    return await _response(session, action, identity, payload.context_run_id)


@router.get("/{action_id}/escalations", response_model=EscalationPackageListResponse)
async def list_action_escalations(action_id: UUID, context_run_id: UUID | None = Query(default=None),
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> EscalationPackageListResponse:
    await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    filters = (TicketEscalationPackage.tenant_id == identity.principal.tenant_id,
        TicketEscalationPackage.user_id == identity.user.id, TicketEscalationPackage.action_id == action_id)
    total = int(await session.scalar(select(func.count()).select_from(TicketEscalationPackage).where(*filters)) or 0)
    items = (await session.scalars(select(TicketEscalationPackage).where(*filters).order_by(TicketEscalationPackage.created_at.desc(), TicketEscalationPackage.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return EscalationPackageListResponse(items=[EscalationPackageSummaryResponse.model_validate(item) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{action_id}/escalations/{package_id}", response_model=EscalationPackageDetailResponse)
async def read_action_escalation(action_id: UUID, package_id: UUID, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> EscalationPackageDetailResponse:
    # 摘要范围先过滤主体/租户，详细内容再检查来源 Run、工具和引用的当前权限。
    package = await session.scalar(select(TicketEscalationPackage).where(TicketEscalationPackage.id == package_id,
        TicketEscalationPackage.action_id == action_id, TicketEscalationPackage.tenant_id == identity.principal.tenant_id,
        TicketEscalationPackage.user_id == identity.user.id))
    if package is None:
        raise ResourceNotFoundError("升级包不存在或不可访问")
    payload = await project_escalation_package(session, package=package, identity=identity)
    return EscalationPackageDetailResponse(**EscalationPackageSummaryResponse.model_validate(package).model_dump(), payload=payload)


async def _response(session: AsyncSession, action: BusinessAction, identity: IdentityContext, context_run_id: UUID | None = None) -> BusinessActionResponse:
    _, _, policy, _ = await authorized_action_source(session, run_id=context_run_id or action.execution_run_id or action.run_id,
        identity=identity, lock=False)
    return BusinessActionResponse.model_validate(action).model_copy(update={"effective_catalog_version": policy.catalog_version,
        "approval_required": action.approval_required or policy.approval_required})


@router.post("/mock-ticket-proposals", response_model=BusinessActionResponse, status_code=status.HTTP_201_CREATED)
async def propose_ticket(payload: ActionProposalRequest, request: Request, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> BusinessActionResponse:
    async with session.begin():
        action = await propose_mock_ticket(session, run_id=payload.run_id, identity=identity,
            settings=settings_from(request), correlation_id=request.state.correlation_id)
    return await _response(session, action, identity, payload.run_id)


@router.get("", response_model=BusinessActionListResponse)
async def list_actions(run_id: UUID = Query(), pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> BusinessActionListResponse:
    await authorized_action_source(session, run_id=run_id, identity=identity, lock=False)
    linked = exists(select(RunEvent.id).where(RunEvent.tenant_id == BusinessAction.tenant_id,
        RunEvent.run_id == run_id, RunEvent.event_type == "action.proposed", RunEvent.data["logical_key"].as_string() == BusinessAction.logical_key))
    filters = (BusinessAction.tenant_id == identity.principal.tenant_id, BusinessAction.proposed_by == identity.user.id, linked)
    total = int(await session.scalar(select(func.count()).select_from(BusinessAction).where(*filters)) or 0)
    items = (await session.scalars(select(BusinessAction).where(*filters).order_by(BusinessAction.created_at.desc(), BusinessAction.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return BusinessActionListResponse(items=[await _response(session, action, identity, run_id) for action in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{action_id}/receipts", response_model=ActionReceiptListResponse)
async def read_receipts(action_id: UUID, context_run_id: UUID | None = Query(default=None),
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> ActionReceiptListResponse:
    await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    filters = (ActionReceipt.action_id == action_id, ActionReceipt.tenant_id == identity.principal.tenant_id)
    total = int(await session.scalar(select(func.count()).select_from(ActionReceipt).where(*filters)) or 0)
    items = (await session.scalars(select(ActionReceipt).where(*filters).order_by(ActionReceipt.observed_at.desc(), ActionReceipt.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return ActionReceiptListResponse(items=[ActionReceiptResponse.model_validate(item) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{action_id}", response_model=BusinessActionResponse)
async def read_action(action_id: UUID, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
    context_run_id: UUID | None = Query(default=None),
) -> BusinessActionResponse:
    action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    return await _response(session, action, identity, context_run_id)


@router.post("/{action_id}/execute", response_model=BusinessActionResponse, status_code=status.HTTP_202_ACCEPTED)
async def queue_action(action_id: UUID, payload: ActionExecutionRequest, request: Request,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> BusinessActionResponse:
    async with session.begin():
        action = await request_action_execution(session, action_id=action_id, identity=identity, settings=settings_from(request),
            expected_generation=payload.expected_generation, expected_parameters_digest=payload.expected_parameters_digest,
            expected_catalog_version=payload.expected_catalog_version, correlation_id=request.state.correlation_id,
            context_run_id=payload.context_run_id)
    return await _response(session, action, identity, payload.context_run_id)


@router.post("/{action_id}/reconcile", response_model=BusinessActionResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_reconciliation(action_id: UUID, payload: ActionRecoveryRequest, request: Request,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> BusinessActionResponse:
    async with session.begin():
        action = await resume_action_reconciliation(session, action_id=action_id, identity=identity, settings=settings_from(request),
            expected_generation=payload.expected_generation, expected_reconciliation_count=payload.expected_reconciliation_count,
            context_run_id=payload.context_run_id, correlation_id=request.state.correlation_id)
    return await _response(session, action, identity, payload.context_run_id)
