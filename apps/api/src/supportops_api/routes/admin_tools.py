from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.tool_services import list_tool_catalog, update_tool_catalog_entry

from supportops_api.dependencies import current_identity, database_session
from supportops_api.schemas import ToolCatalogListResponse, ToolCatalogResponse, ToolCatalogUpdate

router = APIRouter(prefix="/v1/admin/tools", tags=["admin-tools"])


async def platform_admin_identity(
    identity: IdentityContext = Depends(current_identity),
) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@router.get("", response_model=ToolCatalogListResponse)
async def read_tools(
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ToolCatalogListResponse:
    async with session.begin():
        entries = await list_tool_catalog(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
        )
    return ToolCatalogListResponse(
        items=[ToolCatalogResponse.model_validate(entry) for entry in entries]
    )


@router.patch("/{tool_id}", response_model=ToolCatalogResponse)
async def change_tool(
    tool_id: str,
    payload: ToolCatalogUpdate,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> ToolCatalogResponse:
    async with session.begin():
        entries = await list_tool_catalog(
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
        )
        del entries
    return ToolCatalogResponse.model_validate(entry)
