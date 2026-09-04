from collections.abc import AsyncIterator
from typing import cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from supportops_core.auth import (
    AuthenticationError,
    IdentityContext,
    IdentityNotProvisionedError,
    decode_access_token,
    ensure_identity,
)
from supportops_core.config import Settings
from supportops_core.queue import RunQueue
from supportops_core.secrets import LocalEnvelopeSecretProvider, SecretProvider

bearer = HTTPBearer(auto_error=False)


def settings_from(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def session_factory_from(request: Request) -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)


def redis_from(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def queue_from(request: Request) -> RunQueue:
    return cast(RunQueue, request.app.state.run_queue)


def secret_provider_from(request: Request) -> SecretProvider:
    settings = settings_from(request)
    return LocalEnvelopeSecretProvider(settings.secret_encryption_key)


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = session_factory_from(request)
    async with session_factory() as session:
        yield session


async def current_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(database_session),
) -> IdentityContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌")
    settings = settings_from(request)
    try:
        principal = decode_access_token(credentials.credentials, settings)
        identity = await ensure_identity(session, principal)
        if session.in_transaction():
            await session.commit()
        return identity
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except IdentityNotProvisionedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
