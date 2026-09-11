"""受控工具目录服务；目录只管理服务端已注册的实现。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import TOOL_REGISTRY
from supportops_core.models import ToolCatalogEntry, utc_now
from supportops_core.services import ResourceNotFoundError


class ToolCatalogConflictError(ValueError):
    pass


async def ensure_builtin_tools(
    session: AsyncSession, *, tenant_id: UUID, actor_user_id: UUID
) -> list[ToolCatalogEntry]:
    """为开发库或旧租户补齐服务端已注册的内置工具元数据。"""
    current = {
        entry.tool_id: entry
        for entry in (
            await session.scalars(
                select(ToolCatalogEntry).where(ToolCatalogEntry.tenant_id == tenant_id)
            )
        ).all()
    }
    for tool_id, definition in TOOL_REGISTRY.items():
        if tool_id in current:
            continue
        entry = ToolCatalogEntry(
            tenant_id=tenant_id,
            tool_id=definition.id,
            name=definition.name,
            description=definition.description,
            implementation_key=definition.id,
            required_roles=sorted(definition.required_roles),
            risk_level=definition.risk_level,
            version=definition.version,
            is_enabled=True,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )
        session.add(entry)
        current[tool_id] = entry
    await session.flush()
    return list(current.values())


async def list_tool_catalog(
    session: AsyncSession, *, tenant_id: UUID, actor_user_id: UUID
) -> list[ToolCatalogEntry]:
    entries = await ensure_builtin_tools(
        session, tenant_id=tenant_id, actor_user_id=actor_user_id
    )
    return sorted(entries, key=lambda entry: entry.tool_id)


async def get_tool_catalog_entry(
    session: AsyncSession, *, tenant_id: UUID, tool_id: str
) -> ToolCatalogEntry:
    entry = await session.scalar(
        select(ToolCatalogEntry).where(
            ToolCatalogEntry.tenant_id == tenant_id,
            ToolCatalogEntry.tool_id == tool_id,
        )
    )
    if entry is None:
        raise ResourceNotFoundError("工具不存在")
    return entry


async def update_tool_catalog_entry(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    tool_id: str,
    name: str,
    description: str,
    required_roles: list[str],
    risk_level: str,
    is_enabled: bool,
) -> ToolCatalogEntry:
    if tool_id not in TOOL_REGISTRY:
        raise ToolCatalogConflictError("工具没有服务端注册实现，不能进入运行目录")
    entry = await get_tool_catalog_entry(session, tenant_id=tenant_id, tool_id=tool_id)
    entry.name = name
    entry.description = description
    entry.required_roles = sorted(set(required_roles))
    entry.risk_level = risk_level
    entry.is_enabled = is_enabled
    entry.version += 1
    entry.updated_by = actor_user_id
    entry.updated_at = utc_now()
    await session.flush()
    return entry


def catalog_entry_metadata(entry: ToolCatalogEntry) -> dict[str, Any]:
    return {
        "tool_id": entry.tool_id,
        "name": entry.name,
        "description": entry.description,
        "implementation_key": entry.implementation_key,
        "required_roles": list(entry.required_roles),
        "risk_level": entry.risk_level,
        "version": entry.version,
        "is_enabled": entry.is_enabled,
    }
