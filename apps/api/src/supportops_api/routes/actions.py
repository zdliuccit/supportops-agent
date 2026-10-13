"""本人资源的冻结动作提案与独立执行意图；不接受任意工具/主体/参数。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import exists, func, select
from supportops_core.action_proposal_services import (
    authorized_action_source, get_action_for_user, propose_mock_ticket, request_action_execution,
    resume_action_reconciliation,
)
from supportops_core.auth import IdentityContext
from supportops_core.models import ActionReceipt, BusinessAction, RunEvent

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.schemas import ActionExecutionRequest, ActionProposalRequest, BusinessActionResponse, BusinessActionListResponse, ActionReceiptListResponse, ActionReceiptResponse, ActionRecoveryRequest
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params

router = APIRouter(prefix="/v1/actions", tags=["actions"])


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
