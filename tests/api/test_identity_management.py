from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from supportops_core.bootstrap import bootstrap_enterprise_identity
from supportops_core.config import Settings
from supportops_core.models import OrganizationUnit, Tenant, User
from supportops_core.passwords import verify_password

from tests.conftest import auth_headers


async def test_bootstrap_creates_exactly_one_company_and_admin(
    engine: AsyncEngine, settings: Settings
) -> None:
    configured = settings.model_copy(
        update={
            "bootstrap_admin_enabled": True,
            "bootstrap_company_slug": f"bootstrap-{uuid4()}",
            "bootstrap_admin_email": f"admin-{uuid4()}@example.test",
            "bootstrap_admin_password": "Bootstrap-password-123",
        }
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await bootstrap_enterprise_identity(factory, configured)
    await bootstrap_enterprise_identity(factory, configured)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Tenant)) == 1
        assert await session.scalar(select(func.count()).select_from(OrganizationUnit)) == 0
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        admin = await session.scalar(select(User))
        assert admin is not None
        assert verify_password("Bootstrap-password-123", admin.password_hash)
        assert "platform_admin" in admin.roles
        assert admin.organization_unit_id is None


async def test_admin_manages_organization_user_and_login(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    admin = await auth_headers(
        engine, settings, tenant_id, "admin", roles=("platform_admin", "employee")
    )
    root = await client.post(
        "/v1/admin/organization-units",
        headers=admin,
        json={"name": "研发中心"},
    )
    assert root.status_code == 201
    duplicate_root = await client.post(
        "/v1/admin/organization-units",
        headers=admin,
        json={"name": "研发中心"},
    )
    assert duplicate_root.status_code == 409
    legacy_payload = await client.post(
        "/v1/admin/organization-units",
        headers=admin,
        json={"name": "旧字段部门", "code": "LEGACY"},
    )
    assert legacy_payload.status_code == 422
    child = await client.post(
        "/v1/admin/organization-units",
        headers=admin,
        json={"name": "平台支持", "parent_id": root.json()["id"]},
    )
    assert child.status_code == 201
    user_email = f"employee-{uuid4()}@example.test"
    created = await client.post(
        "/v1/admin/users",
        headers=admin,
        json={
            "email": user_email,
            "password": "Employee-password-123",
            "display_name": "研发员工",
            "organization_unit_id": root.json()["id"],
            "job_title": "支持工程师",
            "roles": ["employee"],
        },
    )
    assert created.status_code == 201
    assert "password" not in created.text
    child_user = await client.post(
        "/v1/admin/users",
        headers=admin,
        json={
            "email": f"child-{uuid4()}@example.test",
            "password": "Child-password-123",
            "display_name": "平台支持员工",
            "organization_unit_id": child.json()["id"],
            "job_title": "支持工程师",
            "roles": ["employee"],
        },
    )
    assert child_user.status_code == 201
    login = await client.post(
        "/v1/auth/login",
        json={"email": user_email, "password": "Employee-password-123"},
    )
    assert login.status_code == 200
    tree = await client.get("/v1/admin/organization-units", headers=admin)
    assert tree.json()["items"][0]["direct_user_count"] == 1
    assert tree.json()["items"][0]["user_count"] == 2
    assert tree.json()["items"][0]["children"][0]["direct_user_count"] == 1
    assert tree.json()["items"][0]["children"][0]["user_count"] == 1
    assert set(tree.json()["items"][0]) == {
        "id",
        "parent_id",
        "name",
        "direct_user_count",
        "user_count",
        "children",
    }

    payload = {
        "display_name": "研发员工",
        "organization_unit_id": root.json()["id"],
        "job_title": "支持工程师",
        "phone": "",
        "roles": ["employee"],
        "status": "disabled",
    }
    disabled = await client.patch(
        f"/v1/admin/users/{created.json()['id']}", headers=admin, json=payload
    )
    assert disabled.status_code == 200
    rejected = await client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert rejected.status_code == 403

    reset = await client.put(
        f"/v1/admin/users/{created.json()['id']}/password",
        headers=admin,
        json={"password": "Employee-new-password-456"},
    )
    assert reset.status_code == 204
    enabled = await client.patch(
        f"/v1/admin/users/{created.json()['id']}",
        headers=admin,
        json={**payload, "status": "active"},
    )
    assert enabled.status_code == 200
    old_password = await client.post(
        "/v1/auth/login",
        json={"email": user_email, "password": "Employee-password-123"},
    )
    assert old_password.status_code == 401
    new_password = await client.post(
        "/v1/auth/login",
        json={"email": user_email, "password": "Employee-new-password-456"},
    )
    assert new_password.status_code == 200

    first_users_page = await client.get(
        "/v1/admin/users", params={"page": 1, "page_size": 1}, headers=admin
    )
    second_users_page = await client.get(
        "/v1/admin/users", params={"page": 2, "page_size": 1}, headers=admin
    )
    assert first_users_page.json()["total"] == 3
    assert first_users_page.json()["page"] == 1
    assert first_users_page.json()["page_size"] == 1
    assert first_users_page.json()["pages"] == 3
    assert len(first_users_page.json()["items"]) == 1
    assert len(second_users_page.json()["items"]) == 1
    assert first_users_page.json()["items"][0]["id"] != second_users_page.json()["items"][0]["id"]

    keyword_users = await client.get(
        "/v1/admin/users",
        params={"keywords": "研发员工", "page": 1, "page_size": 20},
        headers=admin,
    )
    assert keyword_users.status_code == 200
    assert keyword_users.json()["total"] == 1
    assert keyword_users.json()["items"][0]["display_name"] == "研发员工"

    department_keyword_users = await client.get(
        "/v1/admin/users",
        params={"keywords": "研发中心", "page": 1, "page_size": 20},
        headers=admin,
    )
    assert department_keyword_users.status_code == 200
    assert department_keyword_users.json()["total"] == 1

    department_id_keyword_users = await client.get(
        "/v1/admin/users",
        params={"keywords": root.json()["id"], "page": 1, "page_size": 20},
        headers=admin,
    )
    assert department_id_keyword_users.status_code == 200
    assert department_id_keyword_users.json()["total"] == 1


async def test_organization_cycle_and_self_disable_are_rejected(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    tenant_id = uuid4()
    admin_headers = await auth_headers(
        engine, settings, tenant_id, "admin", roles=("platform_admin", "employee")
    )
    parent = await client.post(
        "/v1/admin/organization-units",
        headers=admin_headers,
        json={"name": "父部门"},
    )
    child = await client.post(
        "/v1/admin/organization-units",
        headers=admin_headers,
        json={
            "name": "子部门",
            "parent_id": parent.json()["id"],
        },
    )
    cycle = await client.patch(
        f"/v1/admin/organization-units/{parent.json()['id']}",
        headers=admin_headers,
        json={
            "name": "父部门",
            "parent_id": child.json()["id"],
        },
    )
    assert cycle.status_code == 422

    me = await client.get("/v1/auth/me", headers=admin_headers)
    self_disable = await client.patch(
        f"/v1/admin/users/{me.json()['user_id']}",
        headers=admin_headers,
        json={
            "display_name": "admin",
            "organization_unit_id": None,
            "job_title": "",
            "phone": "",
            "roles": ["platform_admin", "employee"],
            "status": "disabled",
        },
    )
    assert self_disable.status_code == 409

    non_empty_delete = await client.delete(
        f"/v1/admin/organization-units/{parent.json()['id']}",
        headers=admin_headers,
    )
    assert non_empty_delete.status_code == 409
    empty_delete = await client.delete(
        f"/v1/admin/organization-units/{child.json()['id']}",
        headers=admin_headers,
    )
    assert empty_delete.status_code == 204


async def test_identity_admin_apis_enforce_role_and_tenant_boundaries(
    client: AsyncClient, engine: AsyncEngine, settings: Settings
) -> None:
    first_tenant_id = uuid4()
    second_tenant_id = uuid4()
    first_admin = await auth_headers(
        engine,
        settings,
        first_tenant_id,
        "first-admin",
        roles=("platform_admin", "employee"),
    )
    second_admin = await auth_headers(
        engine,
        settings,
        second_tenant_id,
        "second-admin",
        roles=("platform_admin", "employee"),
    )
    employee = await auth_headers(
        engine, settings, first_tenant_id, "employee", roles=("employee",)
    )

    forbidden = await client.get("/v1/admin/users", headers=employee)
    assert forbidden.status_code == 403

    foreign_unit = await client.post(
        "/v1/admin/organization-units",
        headers=first_admin,
        json={"name": "一租户部门"},
    )
    assert foreign_unit.status_code == 201
    cross_tenant_user = await client.post(
        "/v1/admin/users",
        headers=second_admin,
        json={
            "email": f"cross-{uuid4()}@example.test",
            "password": "Cross-tenant-password-123",
            "display_name": "跨租户用户",
            "organization_unit_id": foreign_unit.json()["id"],
            "roles": ["employee"],
        },
    )
    assert cross_tenant_user.status_code == 404

    second_users = await client.get("/v1/admin/users", headers=second_admin)
    assert second_users.status_code == 200
    assert len(second_users.json()["items"]) == 1
