"""按租户诊断执行尝试；人工恢复不能扩权、复活终态或重放未知动作。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.models import AgentRun, ModelCallLedger
from supportops_core.run_attempt_services import list_run_attempts, recover_run_manually
from supportops_core.services import ResourceNotFoundError

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import ModelCallListResponse, ModelCallResponse, RunAttemptListResponse, RunAttemptResponse, RunRecoveryRequest, RunResponse

router = APIRouter(prefix="/v1/admin/runs", tags=["admin-run-attempts"])


async def platform_admin_identity(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@router.get("/{run_id}/attempts", response_model=RunAttemptListResponse)
async def read_attempts(
    run_id: UUID, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> RunAttemptListResponse:
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == identity.principal.tenant_id))
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    attempts, total = await list_run_attempts(session, tenant_id=identity.principal.tenant_id,
                                             run_id=run_id, page_size=pagination.page_size, offset=pagination.offset)
    metadata = pagination_metadata(total, pagination)
    return RunAttemptListResponse(items=[RunAttemptResponse.model_validate(item) for item in attempts],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.post("/{run_id}/recover", response_model=RunResponse)
async def recover_run(
    run_id: UUID, payload: RunRecoveryRequest, request: Request,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> RunResponse:
    async with session.begin():
        run = await recover_run_manually(session, tenant_id=identity.principal.tenant_id, run_id=run_id,
            actor_user_id=identity.user.id, expected_generation=payload.expected_generation,
            confirm_legacy_executor_stopped=payload.confirm_legacy_executor_stopped, cancel=payload.cancel,
            settings=settings_from(request))
    return RunResponse.model_validate(run)


@router.get("/{run_id}/model-calls", response_model=ModelCallListResponse)
async def read_model_calls(
    run_id: UUID, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ModelCallListResponse:
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == identity.principal.tenant_id))
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    filters = [ModelCallLedger.tenant_id == identity.principal.tenant_id, ModelCallLedger.run_id == run_id]
    total = int(await session.scalar(select(func.count()).select_from(ModelCallLedger).where(*filters)) or 0)
    items = (await session.scalars(select(ModelCallLedger).where(*filters)
        .order_by(ModelCallLedger.call_number, ModelCallLedger.id).offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return ModelCallListResponse(items=[ModelCallResponse.model_validate(item) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)
