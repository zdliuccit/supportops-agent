"""策略草稿由管理员管理；擦除意向与元数据预览始终只属于本人。"""
import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.models import ErasureRequest, RetentionPolicy
from supportops_core.retention_contracts import (
    ErasurePreviewCommand, ErasureRequestCreate, ErasureRequestDetail,
    RetentionPolicyCreate, RetentionPolicyDetail,
)
from supportops_core.retention_services import (
    DataGovernanceUnavailable, create_policy, create_request, get_request,
    policy_detail, preview_request, request_detail, request_summary, withdraw_request,
)
from supportops_core.services import ResourceNotFoundError

from supportops_api.dependencies import current_identity, database_session
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import ErasureRequestListResponse, RetentionPolicyListResponse

admin_router = APIRouter(prefix="/v1/admin/data-governance/policies", tags=["data-governance"])
account_router = APIRouter(prefix="/v1/account/erasure-requests", tags=["data-governance"])


async def admin_identity(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@admin_router.get("", response_model=RetentionPolicyListResponse)
async def list_policies(response: Response, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity), session: AsyncSession = Depends(database_session),
) -> RetentionPolicyListResponse:
    response.headers["Cache-Control"] = "no-store"
    filters = [RetentionPolicy.tenant_id == identity.principal.tenant_id]
    total = int(await session.scalar(select(func.count()).select_from(RetentionPolicy).where(*filters)) or 0)
    rows = (await session.scalars(select(RetentionPolicy).where(*filters).order_by(RetentionPolicy.created_at.desc(), RetentionPolicy.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    page = pagination_metadata(total, pagination)
    return RetentionPolicyListResponse(items=[policy_detail(row).policy for row in rows],
        total=page.total, page=page.page, page_size=page.page_size, pages=page.pages)


@admin_router.post("", response_model=RetentionPolicyDetail, status_code=201)
async def add_policy(payload: RetentionPolicyCreate, response: Response,
    identity: IdentityContext = Depends(admin_identity), session: AsyncSession = Depends(database_session),
) -> RetentionPolicyDetail:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        row = await create_policy(session, identity=identity, payload=payload)
    return policy_detail(row)


@admin_router.get("/{policy_id}", response_model=RetentionPolicyDetail)
async def read_policy(policy_id: UUID, response: Response, identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> RetentionPolicyDetail:
    response.headers["Cache-Control"] = "no-store"
    row = await session.scalar(select(RetentionPolicy).where(RetentionPolicy.id == policy_id, RetentionPolicy.tenant_id == identity.principal.tenant_id))
    if row is None:
        raise ResourceNotFoundError("策略不存在")
    return policy_detail(row)


@account_router.get("", response_model=ErasureRequestListResponse)
async def list_requests(response: Response, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ErasureRequestListResponse:
    response.headers["Cache-Control"] = "no-store"
    filters = [ErasureRequest.tenant_id == identity.principal.tenant_id, ErasureRequest.subject_user_id == identity.principal.user_id]
    total = int(await session.scalar(select(func.count()).select_from(ErasureRequest).where(*filters)) or 0)
    rows = (await session.scalars(select(ErasureRequest).where(*filters).order_by(ErasureRequest.created_at.desc(), ErasureRequest.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    page = pagination_metadata(total, pagination)
    return ErasureRequestListResponse(items=[request_summary(row) for row in rows], total=page.total,
        page=page.page, page_size=page.page_size, pages=page.pages)


@account_router.post("", response_model=ErasureRequestDetail, status_code=201)
async def add_request(payload: ErasureRequestCreate, response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")],
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ErasureRequestDetail:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        row = await create_request(session, identity=identity, payload=payload, idempotency_key=idempotency_key)
    return request_detail(row)


@account_router.get("/{request_id}", response_model=ErasureRequestDetail)
async def read_request(request_id: UUID, response: Response, identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> ErasureRequestDetail:
    response.headers["Cache-Control"] = "no-store"
    return request_detail(await get_request(session, identity=identity, request_id=request_id))


@account_router.post("/{request_id}/preview", response_model=ErasureRequestDetail)
async def preview(request_id: UUID, payload: ErasurePreviewCommand, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ErasureRequestDetail:
    response.headers["Cache-Control"] = "no-store"
    try:
        # 总时限覆盖锁等待、全部计数和提交；任何失败回滚，不保存半份预览或将未知填零。
        async with asyncio.timeout(10):
            async with session.begin():
                if session.get_bind().dialect.name == "postgresql":
                    await session.execute(text("SET LOCAL statement_timeout = '2500ms'"))
                    await session.execute(text("SET LOCAL lock_timeout = '1000ms'"))
                row = await preview_request(session, identity=identity, request_id=request_id, expected_revision=payload.expected_revision)
    except (TimeoutError, SQLAlchemyError) as exc:
        raise DataGovernanceUnavailable("影响预览未完成") from exc
    return request_detail(row)


@account_router.post("/{request_id}/withdraw", response_model=ErasureRequestDetail)
async def withdraw(request_id: UUID, payload: ErasurePreviewCommand, response: Response,
    identity: IdentityContext = Depends(current_identity), session: AsyncSession = Depends(database_session),
) -> ErasureRequestDetail:
    response.headers["Cache-Control"] = "no-store"
    async with session.begin():
        row = await withdraw_request(session, identity=identity, request_id=request_id, expected_revision=payload.expected_revision)
    return request_detail(row)
