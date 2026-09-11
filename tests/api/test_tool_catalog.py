from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from supportops_core.config import Settings
from supportops_core.models import ToolCatalogEntry

from tests.conftest import auth_headers, provision_agent


async def test_platform_admin_can_manage_server_registered_tool_catalog(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    await provision_agent(engine, settings, tenant_id, subject="tool-admin")
    admin = await auth_headers(
        engine,
        settings,
        tenant_id,
        subject="tool-admin",
        roles=("platform_admin",),
    )

    listed = await client.get("/v1/admin/tools", headers=admin)
    assert listed.status_code == 200
    entries = listed.json()["items"]
    identity = next(item for item in entries if item["tool_id"] == "current_identity_summary")
    assert identity["implementation_key"] == "current_identity_summary"
    assert identity["is_enabled"] is True

    updated = await client.patch(
        "/v1/admin/tools/current_identity_summary",
        headers=admin,
        json={
            "name": "当前身份信息",
            "description": "读取当前请求的可信身份摘要。",
            "required_roles": ["employee"],
            "risk_level": "low",
            "is_enabled": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["is_enabled"] is False
    assert updated.json()["version"] == identity["version"] + 1

    async with async_sessionmaker(engine)() as session:
        entry = await session.scalar(
            select(ToolCatalogEntry).where(
                ToolCatalogEntry.tenant_id == tenant_id,
                ToolCatalogEntry.tool_id == "current_identity_summary",
            )
        )
    assert entry is not None
    assert entry.is_enabled is False

    employee = await auth_headers(engine, settings, tenant_id, subject="tool-employee")
    denied = await client.get("/v1/admin/tools", headers=employee)
    assert denied.status_code == 403
