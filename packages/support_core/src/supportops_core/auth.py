"""系统用户 JWT 的签发、解析与数据库身份解析。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from jwt import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.config import Settings
from supportops_core.models import AuthSession, Tenant, User


class AuthenticationError(ValueError):
    """访问令牌缺失、格式错误或声明不符合约束。"""

    pass


class IdentityNotProvisionedError(ValueError):
    """令牌有效，但对应租户或用户尚未在平台中预配。"""

    pass


@dataclass(frozen=True, slots=True)
class Principal:
    """由可信 JWT 声明解析出的调用者身份。"""

    user_id: UUID
    tenant_id: UUID
    roles: tuple[str, ...]
    display_name: str
    session_id: UUID | None = None
    auth_generation: int | None = None
    token_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IdentityContext:
    """将令牌主体与已持久化用户关联后的请求身份上下文。"""

    principal: Principal
    user: User


def decode_access_token(token: str, settings: Settings) -> Principal:
    """校验 JWT 的签名及标准声明，并返回不可变调用者身份。"""

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "tenant_id", "roles", "jti", "auth_generation"]},
        )
        roles = claims["roles"]
        generation, expires = claims["auth_generation"], claims["exp"]
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0 or isinstance(expires, bool) or not isinstance(expires, int):
            raise AuthenticationError("登录会话声明无效")
        if (
            not isinstance(roles, list)
            or not roles
            or not all(isinstance(role, str) for role in roles)
        ):
            raise AuthenticationError("roles 声明无效")
        return Principal(
            user_id=UUID(str(claims["sub"])),
            tenant_id=UUID(str(claims["tenant_id"])),
            roles=tuple(roles),
            display_name=str(claims.get("name") or claims["sub"]),
            session_id=UUID(str(claims["jti"])),
            auth_generation=generation,
            token_expires_at=datetime.fromtimestamp(expires, tz=UTC),
        )
    except (InvalidTokenError, KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise AuthenticationError("访问令牌无效") from exc


def create_access_token(
    settings: Settings,
    *,
    user_id: UUID,
    tenant_id: UUID,
    roles: tuple[str, ...],
    display_name: str,
    session_id: UUID,
    auth_generation: int,
    expires_in: timedelta = timedelta(minutes=60),
) -> str:
    """为已通过认证的系统用户签发带租户和角色声明的短期 JWT。"""
    now = datetime.now(UTC)
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "roles": list(roles),
        "name": display_name,
        "jti": str(session_id),
        "auth_generation": auth_generation,
        "iat": now,
        "exp": now + expires_in,
    }
    return str(jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


async def ensure_identity(session: AsyncSession, principal: Principal, *, require_session: bool = False) -> IdentityContext:
    """按系统 User UUID 加载 active 用户，不从 JWT 自动创建任何身份。"""

    user = await session.get(User, principal.user_id)
    if user is None or user.tenant_id != principal.tenant_id:
        if require_session:
            raise AuthenticationError("登录会话已失效，请重新登录")
        raise IdentityNotProvisionedError("系统用户不存在")
    if user.status != "active":
        if require_session:
            raise AuthenticationError("登录会话已失效，请重新登录")
        raise IdentityNotProvisionedError("用户已被停用")
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or tenant.status != "active":
        if require_session:
            raise AuthenticationError("登录会话已失效，请重新登录")
        raise IdentityNotProvisionedError("租户不可用")
    if require_session:
        if principal.session_id is None or principal.auth_generation is None or principal.token_expires_at is None:
            raise AuthenticationError("需要重新登录")
        record = await session.get(AuthSession, principal.session_id)
        now = datetime.now(UTC)
        expiry = record.expires_at if record is not None else None
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (record is None or record.tenant_id != user.tenant_id or record.user_id != user.id
            or record.revoked_at is not None or record.auth_generation != user.auth_generation
            or principal.auth_generation != user.auth_generation or expiry is None or expiry <= now
            or principal.token_expires_at <= now):
            raise AuthenticationError("登录会话已失效，请重新登录")
    # 权限和显示名以数据库实时状态为准；HTTP 会话不成为后台业务授权令牌。
    current_principal = Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        roles=tuple(user.roles),
        display_name=user.display_name,
        session_id=principal.session_id,
        auth_generation=principal.auth_generation,
        token_expires_at=principal.token_expires_at,
    )
    return IdentityContext(principal=current_principal, user=user)
