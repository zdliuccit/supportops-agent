from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from supportops_api.main import create_app
from supportops_core.agent_config import (
    agent_config_digest,
    default_agent_config,
    normalize_agent_config,
)
from supportops_core.auth import create_access_token
from supportops_core.config import Settings
from supportops_core.enums import (
    AgentStatus,
    GrantSubjectType,
    ModelApiProtocol,
    ModelProviderKind,
    ModelVerificationStatus,
)
from supportops_core.models import (
    Agent,
    AgentAccessGrant,
    AgentDraft,
    AgentVersion,
    Base,
    ModelCredential,
    ModelCredentialRevision,
    ModelEndpoint,
    ModelEndpointVersion,
    Tenant,
    User,
)
from supportops_core.passwords import hash_password
from supportops_core.secrets import LocalEnvelopeSecretProvider


class FakeRedis:
    def __init__(self) -> None:
        self.queues: dict[str, list[bytes]] = defaultdict(list)

    async def ping(self) -> bool:
        return True

    async def rpush(self, name: str, value: str) -> int:
        self.queues[name].append(value.encode())
        return len(self.queues[name])

    async def blpop(
        self,
        name: str,
        timeout: int = 0,  # noqa: ASYNC109 - 模拟 redis-py 的公开签名
    ) -> tuple[bytes, bytes] | None:
        del timeout
        if not self.queues[name]:
            await asyncio.sleep(0)
            return None
        return name.encode(), self.queues[name].pop(0)


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        redis_url="redis://unused/0",
        jwt_secret="test-secret-with-at-least-thirty-two-characters",
        bootstrap_admin_enabled=False,
        sse_poll_interval_seconds=0.01,
        sse_heartbeat_seconds=0.05,
    )


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    test_engine = create_async_engine(settings.database_url)
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield test_engine
    await test_engine.dispose()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
async def client(
    settings: Settings, engine: AsyncEngine, fake_redis: FakeRedis
) -> AsyncIterator[AsyncClient]:
    app = create_app(settings, engine=engine, redis=cast(Redis, fake_redis))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
            yield value


async def auth_headers(
    engine: AsyncEngine,
    settings: Settings,
    tenant_id: UUID,
    subject: str = "user-1",
    *,
    roles: tuple[str, ...] = ("customer",),
) -> dict[str, str]:
    user_id = uuid5(NAMESPACE_URL, f"{tenant_id}:{subject}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session, session.begin():
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            session.add(Tenant(id=tenant_id, name="测试租户"))
        user = await session.get(User, user_id)
        if user is None:
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=f"{subject}-{tenant_id}@example.test",
                    password_hash=hash_password("Test-password-123"),
                    display_name=subject,
                    roles=list(roles),
                )
            )
        else:
            user.roles = list(roles)
    token = create_access_token(
        settings,
        user_id=user_id,
        tenant_id=tenant_id,
        roles=roles,
        display_name=subject,
    )
    return {"Authorization": f"Bearer {token}"}


async def provision_agent(
    engine: AsyncEngine,
    settings: Settings,
    tenant_id: UUID,
    *,
    subject: str = "user-1",
) -> UUID:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session, session.begin():
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            tenant = Tenant(id=tenant_id, name="测试租户")
            session.add(tenant)
            await session.flush()
        user_id = uuid5(NAMESPACE_URL, f"{tenant_id}:{subject}")
        user = await session.get(User, user_id)
        if user is None:
            user = User(
                id=user_id,
                tenant_id=tenant_id,
                email=f"{subject}-{tenant_id}@example.test",
                password_hash=hash_password("Test-password-123"),
                display_name=subject,
                roles=["customer"],
            )
            session.add(user)
            await session.flush()
        existing = await session.scalar(select(Agent).where(Agent.tenant_id == tenant_id))
        if existing is not None:
            return existing.id
        endpoint = ModelEndpoint(
            tenant_id=tenant_id,
            name="测试模型",
            is_enabled=True,
            created_by=user.id,
            updated_by=user.id,
        )
        session.add(endpoint)
        await session.flush()
        secret_provider = LocalEnvelopeSecretProvider(settings.secret_encryption_key)
        credential = ModelCredential(
            tenant_id=tenant_id,
            endpoint_id=endpoint.id,
            provider=secret_provider.provider_name,
            encrypted_secret=secret_provider.seal("test-api-key"),
            masked_hint="****-key",
            revision=1,
        )
        session.add(credential)
        await session.flush()
        session.add(
            ModelCredentialRevision(
                tenant_id=tenant_id,
                credential_id=credential.id,
                revision=1,
                encrypted_secret=credential.encrypted_secret,
            )
        )
        model_version = ModelEndpointVersion(
            tenant_id=tenant_id,
            endpoint_id=endpoint.id,
            version_number=1,
            provider_kind=ModelProviderKind.OPENAI_OFFICIAL,
            api_protocol=ModelApiProtocol.RESPONSES,
            base_url="https://api.openai.com/v1",
            remote_model_name="test-model",
            capabilities={"streaming": True, "tool_calling": True, "structured_output": True},
            defaults={"max_output_tokens": 4096},
            request_metadata={},
            pricing={},
            credential_id=credential.id,
            verification_status=ModelVerificationStatus.VERIFIED,
            verification_result={"model": "passed"},
            created_by=user.id,
        )
        session.add(model_version)
        await session.flush()
        endpoint.active_version_id = model_version.id
        agent = Agent(
            tenant_id=tenant_id,
            slug="test-agent",
            name="测试支持 Agent",
            description="测试 Agent",
            welcome_message="你好",
            suggested_prompts=["测试问题"],
            status=AgentStatus.ACTIVE,
            created_by=user.id,
            updated_by=user.id,
        )
        session.add(agent)
        await session.flush()
        config = default_agent_config(endpoint.id)
        version = AgentVersion(
            tenant_id=tenant_id,
            agent_id=agent.id,
            version_number=1,
            schema_version=config.schema_version,
            config=normalize_agent_config(config),
            config_digest=agent_config_digest(config),
            model_endpoint_version_id=model_version.id,
            resolved_tool_ids=[],
            published_by=user.id,
        )
        session.add(version)
        await session.flush()
        agent.active_version_id = version.id
        session.add(
            AgentDraft(
                agent_id=agent.id,
                tenant_id=tenant_id,
                revision=1,
                schema_version=config.schema_version,
                config=normalize_agent_config(config),
                updated_by=user.id,
            )
        )
        session.add(
            AgentAccessGrant(
                tenant_id=tenant_id,
                agent_id=agent.id,
                subject_type=GrantSubjectType.ROLE,
                subject_id="customer",
                created_by=user.id,
            )
        )
        return agent.id
