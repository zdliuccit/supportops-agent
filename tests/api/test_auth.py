from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from supportops_core.auth import create_access_token, decode_access_token
from supportops_core.config import Settings
from supportops_core.enums import ROLE_EMPLOYEE
from supportops_core.models import AuthSession, Tenant, User, utc_now
from supportops_core.passwords import hash_password

from tests.conftest import auth_headers


async def seed_login_user(engine: AsyncEngine, *, status: str = "active") -> User:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session, session.begin():
        tenant = Tenant(id=uuid4(), name="登录测试公司", slug=f"login-{uuid4()}")
        session.add(tenant)
        await session.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"login-{uuid4()}@example.test",
            password_hash=hash_password("Correct-password-123"),
            display_name="登录用户",
            roles=[ROLE_EMPLOYEE],
            status=status,
        )
        session.add(user)
        await session.flush()
        return user


async def test_email_password_login_and_identity(client: AsyncClient, engine: AsyncEngine) -> None:
    user = await seed_login_user(engine)
    response = await client.post(
        "/v1/auth/login",
        json={"email": user.email.upper(), "password": "Correct-password-123"},
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    token = response.json()["access_token"]
    identity = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert identity.status_code == 200
    assert identity.json()["user_id"] == str(user.id)
    assert identity.json()["email"] == user.email


async def test_login_hides_unknown_email_and_wrong_password(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    user = await seed_login_user(engine)
    unknown = await client.post(
        "/v1/auth/login",
        json={"email": "unknown@example.test", "password": "Wrong-password-123"},
    )
    wrong = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "Wrong-password-123"},
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


async def test_disabled_user_cannot_login(client: AsyncClient, engine: AsyncEngine) -> None:
    user = await seed_login_user(engine, status="disabled")
    response = await client.post(
        "/v1/auth/login",
        json={"email": user.email, "password": "Correct-password-123"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("operation", ["/logout", "/sessions/revoke-all"])
async def test_revoked_token_cannot_be_replayed(
    client: AsyncClient,
    engine: AsyncEngine,
    operation: str,
) -> None:
    user = await seed_login_user(engine)
    tokens = []
    for _ in range(2):
        login = await client.post(
            "/v1/auth/login",
            json={
                "email": user.email,
                "password": "Correct-password-123",
            },
        )
        assert login.status_code == 200
        tokens.append({"Authorization": f"Bearer {login.json()['access_token']}"})
    assert (await client.post("/v1/auth" + operation, headers=tokens[0])).status_code == 204
    for _ in range(2):
        replay = await client.get("/v1/auth/me", headers=tokens[0])
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "AUTH_SESSION_INVALID"
        assert replay.headers["Cache-Control"] == "no-store"
        assert replay.headers["WWW-Authenticate"] == "Bearer"
    other = await client.get("/v1/auth/me", headers=tokens[1])
    assert other.status_code == (401 if operation.endswith("revoke-all") else 200)


async def test_expired_jwt_and_persisted_session_are_rejected(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    user = await seed_login_user(engine)
    login = await client.post(
        "/v1/auth/login",
        json={
            "email": user.email,
            "password": "Correct-password-123",
        },
    )
    token = login.json()["access_token"]
    principal = decode_access_token(token, settings)
    expired = create_access_token(
        settings,
        user_id=user.id,
        tenant_id=user.tenant_id,
        roles=tuple(user.roles),
        display_name=user.display_name,
        session_id=principal.session_id,
        auth_generation=principal.auth_generation,
        expires_in=timedelta(seconds=-1),
    )
    response = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    async with async_sessionmaker(engine)() as session, session.begin():
        record = await session.scalar(
            select(AuthSession).where(AuthSession.id == principal.session_id)
        )
        assert record is not None
        record.expires_at = utc_now() - timedelta(seconds=1)
    response = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_session_revoke_hides_other_users_session(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    users = [await seed_login_user(engine), await seed_login_user(engine)]
    headers = []
    session_ids = []
    for user in users:
        login = await client.post(
            "/v1/auth/login",
            json={
                "email": user.email,
                "password": "Correct-password-123",
            },
        )
        token = login.json()["access_token"]
        headers.append({"Authorization": f"Bearer {token}"})
        session_ids.append(decode_access_token(token, settings).session_id)
    denied = await client.post(f"/v1/auth/sessions/{session_ids[0]}/revoke", headers=headers[1])
    assert denied.status_code == 404
    assert (await client.get("/v1/auth/me", headers=headers[0])).status_code == 200
    listed = await client.get("/v1/auth/sessions", headers=headers[1])
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == str(session_ids[1])


@pytest.mark.parametrize("change", ["roles", "status", "password"])
async def test_security_changes_permanently_invalidate_old_sessions(
    client: AsyncClient,
    engine: AsyncEngine,
    settings: Settings,
    change: str,
) -> None:
    user = await seed_login_user(engine)
    admin = await auth_headers(engine, settings, user.tenant_id, "admin", roles=("platform_admin",))
    login = await client.post(
        "/v1/auth/login",
        json={
            "email": user.email,
            "password": "Correct-password-123",
        },
    )
    old_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    base = f"/v1/admin/users/{user.id}"
    payload = {"display_name": user.display_name, "roles": ["employee"], "status": "active"}
    password = "Correct-password-123"
    if change == "password":
        password = "Changed-password-456"
        response = await client.put(base + "/password", headers=admin, json={"password": password})
        assert response.status_code == 204
    else:
        update = {
            **payload,
            change: ["platform_admin"] if change == "roles" else "disabled",
        }
        assert (await client.patch(base, headers=admin, json=update)).status_code == 200
        assert (await client.get("/v1/auth/me", headers=old_headers)).status_code == 401
        assert (await client.patch(base, headers=admin, json=payload)).status_code == 200
    assert (await client.get("/v1/auth/me", headers=old_headers)).status_code == 401
    fresh = await client.post("/v1/auth/login", json={"email": user.email, "password": password})
    assert fresh.status_code == 200
    assert (
        await client.get(
            "/v1/auth/me",
            headers={
                "Authorization": f"Bearer {fresh.json()['access_token']}",
            },
        )
    ).status_code == 200
