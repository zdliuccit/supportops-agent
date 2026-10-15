"""登录会话生命周期；后台业务继续使用当前用户/grant，不依赖浏览器会话。"""
from datetime import UTC, datetime
from uuid import UUID
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext
from supportops_core.models import AgentAuditEvent, AuthSession, User
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.services import ResourceNotFoundError


async def invalidate_user_sessions(session: AsyncSession, *, user: User, actor_user_id: UUID,
    code: str, correlation_id: str,
) -> None:
    """SQL 原子递增许可代次，避免并发安全修改丢失增量，使旧会话再次有效。"""
    now = datetime.now(UTC)
    await session.execute(update(User).where(User.id == user.id, User.tenant_id == user.tenant_id)
        .values(auth_generation=User.auth_generation+1).execution_options(synchronize_session="fetch"))
    await session.execute(update(AuthSession).where(AuthSession.user_id == user.id, AuthSession.tenant_id == user.tenant_id,
        AuthSession.revoked_at.is_(None)).values(revoked_at=now, revoked_by=actor_user_id, revocation_code=code))
    session.add(AgentAuditEvent(tenant_id=user.tenant_id, actor_user_id=actor_user_id,
        resource_type=AuditResourceType.AUTH_SESSION, action=AuditAction.AUTH_SESSIONS_REVOKED,
        correlation_id=correlation_id, metadata_payload={"user_id": str(user.id), "code": code}))


async def revoke_owned_session(session: AsyncSession, *, identity: IdentityContext, session_id: UUID,
    correlation_id: str,
) -> AuthSession:
    row = await session.scalar(select(AuthSession).where(AuthSession.id == session_id,
        AuthSession.tenant_id == identity.principal.tenant_id, AuthSession.user_id == identity.user.id)
        .execution_options(populate_existing=True).with_for_update(key_share=True))
    if row is None:
        raise ResourceNotFoundError("登录会话不存在")
    if row.revoked_at is None:
        row.revoked_at, row.revoked_by, row.revocation_code = datetime.now(UTC), identity.user.id, "OWNER_LOGOUT"
        session.add(AgentAuditEvent(tenant_id=row.tenant_id, actor_user_id=identity.user.id,
            resource_type=AuditResourceType.AUTH_SESSION, action=AuditAction.AUTH_SESSION_REVOKED,
            correlation_id=correlation_id, metadata_payload={"session_id": str(row.id), "user_id": str(row.user_id), "code": row.revocation_code}))
    return row
