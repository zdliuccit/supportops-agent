"""管理员工具发现治理；目录提交是元数据操作，不执行网络发现或业务工具。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.tool_connection_contracts import ConnectionCommand, ConnectionView, ProbeCancelCommand, ProbeJobView
from supportops_core.tool_connection_services import cancel_probe, job_view, open_circuit, read_connection, request_probe, scoped_probe
from supportops_core.tool_discovery_contracts import (
    DiscoveredTool, DiscoveryCatalogCommand, DiscoveryInvalidationCommand, DiscoveryPolicyCommand, DiscoveryView,
)
from supportops_core.tool_discovery_services import (
    ToolDiscoveryConflict, configure_tool_source, invalidate_tool_source, read_tool_source, record_tool_catalog,
    resolve_discovered_tool, list_tool_sources,
)

from supportops_api.dependencies import database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.routes.admin_tools import platform_admin_identity
from supportops_api.schemas import PaginatedListResponse

router = APIRouter(prefix="/v1/admin/tool-discovery/sources", tags=["admin-tool-discovery"])
SourcePath = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class ToolSourceListResponse(PaginatedListResponse):
    items: list[DiscoveryView]


@asynccontextmanager
async def _transaction(session: AsyncSession) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(10), session.begin():
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(text("SET LOCAL statement_timeout = '4s'"))
                await session.execute(text("SET LOCAL lock_timeout = '1s'"))
            yield
    except (TimeoutError, SQLAlchemyError):
        # 提交响应未知时只允许GET核对，不输出SQL参数或自动重放。
        raise ToolDiscoveryConflict("工具发现治理结果暂不可确认，请查询当前来源修订") from None


@router.get("", response_model=ToolSourceListResponse)
async def read_sources(response: Response,
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ToolSourceListResponse:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        items, total = await list_tool_sources(session, identity, page_size=pagination.page_size, offset=pagination.offset)
    metadata = pagination_metadata(total, pagination)
    return ToolSourceListResponse(items=items, total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages)


@router.get("/{source_key}", response_model=DiscoveryView)
async def read_source(source_key: SourcePath, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> DiscoveryView:
    """单来源有界聚合，工具集合最多50项，不属于普通列表分页。"""
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await read_tool_source(session, identity, source_key=source_key)
    return result


@router.put("/{source_key}", response_model=DiscoveryView)
async def configure_source(source_key: SourcePath, command: DiscoveryPolicyCommand, request: Request, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> DiscoveryView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await configure_tool_source(session, identity, source_key=source_key, command=command,
            correlation_id=request.state.correlation_id)
    return result


@router.post("/{source_key}/invalidate", response_model=DiscoveryView)
async def invalidate_source(source_key: SourcePath, command: DiscoveryInvalidationCommand, request: Request, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> DiscoveryView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await invalidate_tool_source(session, identity, source_key=source_key, command=command,
            correlation_id=request.state.correlation_id)
    return result


@router.post("/{source_key}/catalogs", response_model=DiscoveryView)
async def record_catalog(source_key: SourcePath, command: DiscoveryCatalogCommand, request: Request, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> DiscoveryView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await record_tool_catalog(session, identity, source_key=source_key, command=command,
            correlation_id=request.state.correlation_id)
    return result


@router.get("/{source_key}/tools/{tool_id}", response_model=DiscoveredTool)
async def read_fixed_tool(source_key: SourcePath, tool_id: Annotated[str, Path(pattern=r"^mcp_[0-9a-f]{64}$")],
    record_id: UUID, catalog_digest: Annotated[str, Query(pattern=r"^[0-9a-f]{64}$")], response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> DiscoveredTool:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await resolve_discovered_tool(session, identity, source_key=source_key,
            record_id=record_id, catalog_digest=catalog_digest, tool_id=tool_id)
    return result


@router.get("/{source_key}/connection", response_model=ConnectionView)
async def connection_status(source_key: SourcePath, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
    settings: Settings = Depends(settings_from),
) -> ConnectionView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await read_connection(session, identity, source_key=source_key, settings=settings)
    return result


@router.post("/{source_key}/connection/open", response_model=ConnectionView)
async def suspend_connection(source_key: SourcePath, command: ConnectionCommand, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
    settings: Settings = Depends(settings_from),
) -> ConnectionView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await open_circuit(session, identity, source_key=source_key, command=command, settings=settings)
    return result


@router.post("/{source_key}/connection/probes", response_model=ProbeJobView, status_code=202)
async def submit_connection_probe(source_key: SourcePath, command: ConnectionCommand, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
    settings: Settings = Depends(settings_from),
) -> ProbeJobView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await request_probe(session, identity, source_key=source_key, command=command, settings=settings)
    return result


@router.get("/{source_key}/connection/probes/{job_id}", response_model=ProbeJobView)
async def connection_probe_detail(source_key: SourcePath, job_id: UUID, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ProbeJobView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        _, job = await scoped_probe(session, identity, source_key=source_key, job_id=job_id)
        result = job_view(job)
    return result


@router.post("/{source_key}/connection/probes/{job_id}/cancel", response_model=ProbeJobView)
async def cancel_connection_probe(source_key: SourcePath, job_id: UUID, command: ProbeCancelCommand, response: Response,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ProbeJobView:
    response.headers["Cache-Control"] = "no-store"
    async with _transaction(session):
        result = await cancel_probe(session, identity, source_key=source_key, job_id=job_id, revision=command.expected_revision)
    return result
