"""按租户诊断执行尝试；人工恢复不能扩权、复活终态或重放未知动作。"""

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.models import AgentRun, ModelCallLedger, RunEvent
from supportops_core.model_routing_reservation_contracts import RoutingReservationRecord
from supportops_core.model_routing_reservations import read_routing_reservation
from supportops_core.model_routing_accounting_contracts import RoutingAccountingView
from supportops_core.model_routing_settlements import RoutingAccountingUnconfirmed, read_routing_accounting
from supportops_core.model_routing_policy_services import require_routing_administrator
from supportops_core.model_routing_request_contracts import RoutingExecutionOutcomeView
from supportops_core.model_routing_run_lifecycle import RoutingOutcomeUnavailable, read_routing_execution_outcome
from supportops_core.redaction import redact_sensitive_text
from supportops_core.run_attempt_services import list_run_attempts, recover_run_manually
from supportops_core.services import ResourceNotFoundError

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import ModelCallListResponse, ModelCallResponse, RunAttemptListResponse, RunAttemptResponse, RunRecoveryRequest, RunResponse
from supportops_api.schemas import RunSecurityEventListResponse, RunSecurityEventResponse

router = APIRouter(prefix="/v1/admin/runs", tags=["admin-run-attempts"])
SECURITY_EVENT_TYPES = frozenset({"tool.denied", "approval.requested", "approval.decided", "approval.consumed",
    "approval.cancelled", "approval.expired", "approval.invalidated", "action.queued", "action.started",
    "action.updated", "action.blocked", "action.cancelled", "action.recovering", "action.parameters_replaced"})
SECURITY_METADATA_KEYS = frozenset({"code", "status", "risk_level", "revision", "generation", "tool_id", "actor_id",
    "invocation_id", "action_id", "action_attempt_id", "approval_id", "receipt_id", "receipt_status",
    "parameters_digest", "implementation_version", "catalog_version", "data_environment"})


async def platform_admin_identity(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@router.get("/{run_id}/model-calls/{call_id}/routing", response_model=RoutingReservationRecord)
async def read_model_call_routing(
    run_id: UUID, call_id: UUID, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> RoutingReservationRecord:
    """单次预留的完整有界记录；查证原调用不预留预算、不派发或产生执行许可。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        return await read_routing_reservation(session, tenant_id=identity.principal.tenant_id,
            run_id=run_id, call_id=call_id)
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在或当前不可访问",
            headers={"Cache-Control": "no-store"}) from None


@router.get("/{run_id}/model-calls/{call_id}/routing/accounting", response_model=RoutingAccountingView)
async def read_model_call_routing_accounting(run_id: UUID, call_id: UUID, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> RoutingAccountingView:
    """一份完整有界记账详情；最多两次观察，不是普通列表，不分页，不取执行回执。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        async with asyncio.timeout(5), session.begin():
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await require_routing_administrator(session, identity)
            result = await read_routing_accounting(session, tenant_id=identity.principal.tenant_id,
                run_id=run_id, call_id=call_id)
        return result
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在或当前不可访问",
            headers={"Cache-Control": "no-store"}) from None
    except (TimeoutError, SQLAlchemyError):
        raise RoutingAccountingUnconfirmed() from None


@router.get("/{run_id}/attempts/{attempt_id}/routing-outcome", response_model=RoutingExecutionOutcomeView)
async def read_routing_outcome(run_id: UUID, attempt_id: UUID, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> RoutingExecutionOutcomeView:
    """单个原attempt的一份有界结果查证，非列表不分页；不停止、恢复或返回正文。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        async with asyncio.timeout(5), session.begin():
            if session.get_bind().dialect.name != "postgresql":
                raise RoutingOutcomeUnavailable()
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await require_routing_administrator(session, identity)
            result = await read_routing_execution_outcome(session, tenant_id=identity.principal.tenant_id,
                run_id=run_id, attempt_id=attempt_id)
        return result
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="资源不存在或当前不可访问",
            headers={"Cache-Control": "no-store"}) from None
    except (TimeoutError, SQLAlchemyError):
        raise RoutingOutcomeUnavailable() from None


@router.get("/{run_id}/security-events", response_model=RunSecurityEventListResponse)
async def read_security_events(run_id: UUID, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> RunSecurityEventListResponse:
    run = await session.scalar(select(AgentRun.id).where(AgentRun.id == run_id, AgentRun.tenant_id == identity.principal.tenant_id))
    if run is None:
        raise ResourceNotFoundError("Run 不存在")
    filters = (RunEvent.tenant_id == identity.principal.tenant_id, RunEvent.run_id == run_id,
        RunEvent.event_type.in_(SECURITY_EVENT_TYPES))
    total = int(await session.scalar(select(func.count()).select_from(RunEvent).where(*filters)) or 0)
    events = (await session.scalars(select(RunEvent).where(*filters).order_by(RunEvent.sequence.desc(), RunEvent.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    items = []
    for event in events:
        metadata = {key: redact_sensitive_text(value)[:200] if isinstance(value, str) else value
            for key, value in event.data.items() if key in SECURITY_METADATA_KEYS
            and (value is None or isinstance(value, (str, int, bool)))}
        items.append(RunSecurityEventResponse(sequence=event.sequence, event_type=event.event_type,
            occurred_at=event.created_at, attempt_id=event.attempt_id, correlation_id=event.correlation_id, metadata=metadata))
    page = pagination_metadata(total, pagination)
    return RunSecurityEventListResponse(items=items, total=page.total, page=page.page, page_size=page.page_size, pages=page.pages)


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
