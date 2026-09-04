from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from supportops_core.enums import ROLE_EMPLOYEE
from supportops_core.models import Tenant, User
from supportops_core.passwords import hash_password


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


async def test_email_password_login_and_identity(
    client: AsyncClient, engine: AsyncEngine
) -> None:
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
