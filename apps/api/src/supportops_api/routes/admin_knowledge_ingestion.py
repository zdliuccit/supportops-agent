"""本人导入任务及默认关闭的获准本地来源人工入口。"""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import PermissionDeniedError, require_platform_admin
from supportops_core.auth import AuthenticationError, IdentityContext
from supportops_core.knowledge_ingestion_contracts import (
    IngestionCreateCommand, IngestionJobView, IngestionRevisionCommand, IngestionSourceReadiness, PersistedIngestionStatus,
    IngestionCreationStatus, IngestionCreationStopCommand,
)
from supportops_core.knowledge_ingestion_approval import (
    FileLocalMarkdownApprovalProvider, create_configured_ingestion_job, read_configured_ingestion_source,
)
from supportops_core.knowledge_ingestion_runtime import execute_local_markdown_ingestion
from supportops_core.knowledge_ingestion_services import (
    cancel_ingestion_job, expire_ingestion_job, list_ingestion_job_views, read_ingestion_job_view,
    read_ingestion_job_by_request,
    IngestionConflict, IngestionCreationStopUncertain, read_ingestion_creation_status, stop_ingestion_creation_request,
)
from supportops_core.services import ResourceNotFoundError
from supportops_core.knowledge_ingestion_review import (
    IngestionComparisonView, IngestionDraftView, list_ingestion_drafts, read_ingestion_comparison,
)
from supportops_core.knowledge_ingestion_governance import (
    IngestionGovernanceCommand, IngestionGovernanceView, IngestionOwnerOption, list_ingestion_owners,
    read_ingestion_governance, update_ingestion_governance,
    IngestionWithdrawalCommand, withdraw_ingestion_candidate,
)

from supportops_api.dependencies import current_identity, database_session, session_factory_from, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import PaginatedListResponse

router = APIRouter(prefix="/v1/admin/knowledge/ingestion-jobs", tags=["admin-knowledge-ingestion"])


class IngestionJobListResponse(PaginatedListResponse):
    items: list[IngestionJobView]


class IngestionDraftListResponse(PaginatedListResponse):
    items: list[IngestionDraftView]


class IngestionOwnerListResponse(PaginatedListResponse):
    items: list[IngestionOwnerOption]


async def administrator(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@router.get("", response_model=IngestionJobListResponse)
async def list_jobs(response: Response, status: PersistedIngestionStatus | None = None,
    source_id: UUID | None = None, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionJobListResponse:
    response.headers["Cache-Control"] = "no-store"
    items, total = await list_ingestion_job_views(session, identity=identity, page_size=pagination.page_size,
        offset=pagination.offset, status=status, source_id=source_id)
    metadata = pagination_metadata(total, pagination)
    return IngestionJobListResponse(items=items, total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages)


@router.get("/source-readiness", response_model=IngestionSourceReadiness)
async def source_readiness(request: Request, response: Response,
    identity: IdentityContext = Depends(administrator),
) -> IngestionSourceReadiness:
    response.headers["Cache-Control"] = "no-store"
    return await read_configured_ingestion_source(session_factory_from(request), identity=identity,
        provider=FileLocalMarkdownApprovalProvider(settings_from(request)))


@router.get("/by-request", response_model=IngestionJobView)
async def read_by_request(response: Response, request_key: UUID,
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionJobView:
    response.headers["Cache-Control"] = "no-store"
    return await read_ingestion_job_by_request(session, identity=identity, request_key=str(request_key))


@router.post("", response_model=IngestionJobView)
async def create_job(payload: IngestionCreateCommand, request: Request, response: Response,
    idempotency_key: UUID = Header(alias="Idempotency-Key"), identity: IdentityContext = Depends(administrator),
) -> IngestionJobView:
    response.headers["Cache-Control"] = "no-store"
    return await create_configured_ingestion_job(session_factory_from(request), identity=identity,
        provider=FileLocalMarkdownApprovalProvider(settings_from(request)),
        expected_scope_digest=payload.expected_scope_digest, request_key=idempotency_key)


@router.get("/creation-requests/{request_key}", response_model=IngestionCreationStatus)
async def read_creation_status(request_key: UUID, response: Response,
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionCreationStatus:
    response.headers["Cache-Control"] = "no-store"
    return await read_ingestion_creation_status(session, identity=identity, request_key=request_key)


@router.post("/creation-requests/{request_key}/stop", response_model=IngestionCreationStatus)
async def stop_creation(request_key: UUID, payload: IngestionCreationStopCommand, response: Response,
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionCreationStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        async with session.begin():
            result = await stop_ingestion_creation_request(session, identity=identity,
                request_key=request_key, correlation_id=f"ingestion-stop:{uuid4()}")
    except (IngestionConflict, PermissionDeniedError, AuthenticationError, ResourceNotFoundError):
        raise
    except Exception:
        raise IngestionCreationStopUncertain("创建停止结果无法确认，请查询原请求状态；勿自动重发") from None
    return result


@router.get("/{job_id}", response_model=IngestionJobView)
async def read_job(job_id: UUID, response: Response, identity: IdentityContext = Depends(administrator),
    session: AsyncSession = Depends(database_session),
) -> IngestionJobView:
    response.headers["Cache-Control"] = "no-store"
    return await read_ingestion_job_view(session, identity=identity, job_id=job_id)


@router.post("/{job_id}/execute", response_model=IngestionJobView)
async def execute_job(job_id: UUID, payload: IngestionRevisionCommand, request: Request, response: Response,
    identity: IdentityContext = Depends(administrator),
) -> IngestionJobView:
    response.headers["Cache-Control"] = "no-store"
    return await execute_local_markdown_ingestion(session_factory_from(request), identity=identity,
        job_id=job_id, expected_revision=payload.expected_revision,
        approval_provider=FileLocalMarkdownApprovalProvider(settings_from(request)))


@router.get("/{job_id}/drafts", response_model=IngestionDraftListResponse)
async def list_drafts(job_id: UUID, response: Response,
    pagination: PaginationParams = Depends(pagination_params), identity: IdentityContext = Depends(administrator),
    session: AsyncSession = Depends(database_session),
) -> IngestionDraftListResponse:
    response.headers["Cache-Control"] = "no-store"
    items, total = await list_ingestion_drafts(session, identity=identity, job_id=job_id,
        page_size=pagination.page_size, offset=pagination.offset)
    metadata = pagination_metadata(total, pagination)
    return IngestionDraftListResponse(items=items, total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{job_id}/drafts/{materialization_id}/comparison", response_model=IngestionComparisonView)
async def read_comparison(job_id: UUID, materialization_id: UUID, response: Response,
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionComparisonView:
    response.headers["Cache-Control"] = "no-store"
    return await read_ingestion_comparison(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id)


@router.get("/{job_id}/drafts/{materialization_id}/governance", response_model=IngestionGovernanceView)
async def read_governance(job_id: UUID, materialization_id: UUID, response: Response,
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionGovernanceView:
    response.headers["Cache-Control"] = "no-store"
    return await read_ingestion_governance(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id)


@router.patch("/{job_id}/drafts/{materialization_id}/governance", response_model=IngestionGovernanceView)
async def edit_governance(job_id: UUID, materialization_id: UUID, payload: IngestionGovernanceCommand,
    response: Response, identity: IdentityContext = Depends(administrator),
    session: AsyncSession = Depends(database_session),
) -> IngestionGovernanceView:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        return await update_ingestion_governance(session, identity=identity, job_id=job_id,
            materialization_id=materialization_id, command=payload, correlation_id=f"ingestion-review:{uuid4()}")


@router.get("/{job_id}/drafts/{materialization_id}/governance/owners", response_model=IngestionOwnerListResponse)
async def list_owners(job_id: UUID, materialization_id: UUID, response: Response,
    keywords: str = Query(default="", max_length=200), pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionOwnerListResponse:
    response.headers["Cache-Control"] = "no-store"
    items, total = await list_ingestion_owners(session, identity=identity, job_id=job_id,
        materialization_id=materialization_id, keywords=keywords, page_size=pagination.page_size, offset=pagination.offset)
    metadata = pagination_metadata(total, pagination)
    return IngestionOwnerListResponse(items=items, total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages)


@router.post("/{job_id}/drafts/{materialization_id}/withdraw", response_model=IngestionGovernanceView)
async def withdraw_candidate(job_id: UUID, materialization_id: UUID, payload: IngestionWithdrawalCommand,
    response: Response, identity: IdentityContext = Depends(administrator),
    session: AsyncSession = Depends(database_session),
) -> IngestionGovernanceView:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        return await withdraw_ingestion_candidate(session, identity=identity, job_id=job_id,
            materialization_id=materialization_id, command=payload, correlation_id=f"ingestion-withdraw:{uuid4()}")


@router.post("/{job_id}/cancel", response_model=IngestionJobView)
async def cancel_job(job_id: UUID, payload: IngestionRevisionCommand, response: Response,
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionJobView:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        await cancel_ingestion_job(session, identity=identity, job_id=job_id,
            expected_revision=payload.expected_revision, correlation_id=f"ingestion:{uuid4()}")
    return await read_ingestion_job_view(session, identity=identity, job_id=job_id)


@router.post("/{job_id}/expire", response_model=IngestionJobView)
async def expire_job(job_id: UUID, payload: IngestionRevisionCommand, response: Response,
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IngestionJobView:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        await expire_ingestion_job(session, identity=identity, job_id=job_id,
            expected_revision=payload.expected_revision, correlation_id=f"ingestion:{uuid4()}")
    return await read_ingestion_job_view(session, identity=identity, job_id=job_id)
