from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.tool_services import (
    count_tool_catalog,
    ensure_builtin_tools,
    list_tool_catalog,
    update_tool_catalog_entry,
    set_tool_revocation,
)

from supportops_api.dependencies import current_identity, database_session
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import ToolCatalogListResponse, ToolCatalogResponse, ToolCatalogUpdate, ToolRevocationRequest

router = APIRouter(prefix="/v1/admin/tools", tags=["admin-tools"])


async def platform_admin_identity(
    identity: IdentityContext = Depends(current_identity),
) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@router.get("", response_model=ToolCatalogListResponse)
async def read_tools(
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ToolCatalogListResponse:
    async with session.begin():
        await ensure_builtin_tools(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
        )
        entries = await list_tool_catalog(
            session, tenant_id=identity.principal.tenant_id,
            page_size=pagination.page_size, offset=pagination.offset,
        )
        total = await count_tool_catalog(session, tenant_id=identity.principal.tenant_id)
    metadata = pagination_metadata(total, pagination)
    return ToolCatalogListResponse(
        items=[ToolCatalogResponse.model_validate(entry) for entry in entries],
        total=metadata.total, page=metadata.page,
        page_size=metadata.page_size, pages=metadata.pages,
    )


@router.patch("/{tool_id}", response_model=ToolCatalogResponse)
async def change_tool(
    tool_id: str,
    payload: ToolCatalogUpdate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ToolCatalogResponse:
    async with session.begin():
        await ensure_builtin_tools(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
        )
        entry = await update_tool_catalog_entry(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            tool_id=tool_id,
            name=payload.name,
            description=payload.description,
            required_roles=payload.required_roles,
            risk_level=payload.risk_level,
            is_enabled=payload.is_enabled,
            correlation_id=request.state.correlation_id,
        )
    return ToolCatalogResponse.model_validate(entry)


@router.post("/{tool_id}/revocation", response_model=ToolCatalogResponse)
async def change_revocation(
    tool_id: str, payload: ToolRevocationRequest, request: Request,
    identity: IdentityContext = Depends(platform_admin_identity), session: AsyncSession = Depends(database_session),
) -> ToolCatalogResponse:
    async with session.begin():
        entry = await set_tool_revocation(session, tenant_id=identity.principal.tenant_id, tool_id=tool_id,
            actor_user_id=identity.user.id, revoked=payload.revoked, correlation_id=request.state.correlation_id,
            expected_version=payload.expected_version)
    return ToolCatalogResponse.model_validate(entry)
