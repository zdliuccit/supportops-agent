"""受控工具目录服务；目录只管理服务端已注册的实现。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import TOOL_REGISTRY
from supportops_core.models import Tenant, ToolCatalogEntry, utc_now
from supportops_core.models import AgentAuditEvent
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.services import ResourceNotFoundError


class ToolCatalogConflictError(ValueError):
    pass


async def ensure_builtin_tools(
    session: AsyncSession, *, tenant_id: UUID, actor_user_id: UUID
) -> None:
    """为开发库或旧租户补齐服务端已注册的内置工具元数据。"""
    # 元数据 GET 与 Worker 领取都可能补齐；兼容 FK 的锁保证并发不重复插入。
    await session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update(key_share=True))
    current = set(
        (
            await session.scalars(
                select(ToolCatalogEntry.tool_id).where(
                    ToolCatalogEntry.tenant_id == tenant_id,
                    ToolCatalogEntry.tool_id.in_(TOOL_REGISTRY),
                )
            )
        ).all()
    )
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
    await session.flush()


async def list_tool_catalog(
    session: AsyncSession, *, tenant_id: UUID, page_size: int, offset: int
) -> list[ToolCatalogEntry]:
    """按租户与稳定唯一工具标识在数据库分页。"""
    return list(
        (await session.scalars(
            select(ToolCatalogEntry)
            .where(ToolCatalogEntry.tenant_id == tenant_id)
            .order_by(ToolCatalogEntry.tool_id, ToolCatalogEntry.id)
            .offset(offset)
            .limit(page_size)
        )).all()
    )


async def count_tool_catalog(session: AsyncSession, *, tenant_id: UUID) -> int:
    """计数与列表使用相同租户和默认软删除范围。"""
    return int(await session.scalar(
        select(func.count()).select_from(ToolCatalogEntry)
        .where(ToolCatalogEntry.tenant_id == tenant_id)
    ) or 0)


async def get_tool_catalog_entry(
    session: AsyncSession, *, tenant_id: UUID, tool_id: str, lock: bool = False,
) -> ToolCatalogEntry:
    query = select(ToolCatalogEntry).where(
            ToolCatalogEntry.tenant_id == tenant_id,
            ToolCatalogEntry.tool_id == tool_id,
        )
    if lock:
        query = query.execution_options(populate_existing=True).with_for_update()
    entry = await session.scalar(query)
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
    correlation_id: str,
) -> ToolCatalogEntry:
    if tool_id not in TOOL_REGISTRY:
        raise ToolCatalogConflictError("工具没有服务端注册实现，不能进入运行目录")
    entry = await get_tool_catalog_entry(session, tenant_id=tenant_id, tool_id=tool_id, lock=True)
    entry.name = name
    entry.description = description
    entry.required_roles = sorted(set(required_roles))
    entry.risk_level = risk_level
    entry.is_enabled = is_enabled
    entry.version += 1
    entry.updated_by = actor_user_id
    entry.updated_at = utc_now()
    session.add(AgentAuditEvent(tenant_id=tenant_id, resource_type=AuditResourceType.TOOL,
        action=AuditAction.TOOL_UPDATED, actor_user_id=actor_user_id, correlation_id=correlation_id,
        metadata_payload={"tool_id": tool_id, "catalog_version": entry.version, "risk_level": risk_level,
                          "is_enabled": is_enabled, "is_revoked": entry.is_revoked}))
    await session.flush()
    return entry


async def set_tool_revocation(
    session: AsyncSession, *, tenant_id: UUID, tool_id: str, actor_user_id: UUID,
    revoked: bool, correlation_id: str,
    expected_version: int,
) -> ToolCatalogEntry:
    entry = await get_tool_catalog_entry(session, tenant_id=tenant_id, tool_id=tool_id, lock=True)
    if entry.version != expected_version:
        raise ToolCatalogConflictError("工具策略版本已变化，请刷新后重新确认")
    if entry.is_revoked == revoked:
        return entry
    entry.is_revoked = revoked
    entry.version += 1
    entry.updated_by = actor_user_id
    entry.updated_at = utc_now()
    session.add(AgentAuditEvent(tenant_id=tenant_id, resource_type=AuditResourceType.TOOL,
        action=AuditAction.TOOL_REVOKED if revoked else AuditAction.TOOL_REVOCATION_CLEARED,
        actor_user_id=actor_user_id, correlation_id=correlation_id,
        metadata_payload={"tool_id": tool_id, "catalog_version": entry.version, "is_revoked": revoked}))
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
        "is_revoked": entry.is_revoked,
    }
