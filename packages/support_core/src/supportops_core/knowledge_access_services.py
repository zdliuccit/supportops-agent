"""显式知识读取的事务边界与独立最小拒绝审计。"""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.auth import IdentityContext
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.knowledge_services import KnowledgeConflictError, append_knowledge_audit

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def knowledge_read_transaction(session: AsyncSession) -> AsyncIterator[None]:
    """知识及升级上下文API在文档锁下完成投影；不把DB失败伪装成ACL拒绝。"""
    try:
        async with asyncio.timeout(5), session.begin():
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(text("SET LOCAL statement_timeout = '4s'"))
                await session.execute(text("SET LOCAL lock_timeout = '1s'"))
            yield
    except (TimeoutError, SQLAlchemyError):
        raise KnowledgeConflictError("知识读取暂时不可确认，请稍后重试") from None


async def record_knowledge_access_denial(
    factory: async_sessionmaker[AsyncSession], *, identity: IdentityContext,
    operation: Literal["snapshot_read", "evidence_read"], requested_resource_id: UUID,
    correlation_id: str, requested_run_id: UUID | None = None,
) -> bool:
    """调用者先结束拒绝请求事务；审计失败不恢复读取，也不提交请求原事务。"""
    tenant_id = identity.principal.tenant_id
    # 回滚可能过期同会话User实例；只使用已经认证的不可变principal定位。
    actor_user_id = identity.principal.user_id
    correlation = correlation_id if 0 < len(correlation_id) <= 64 else str(uuid4())
    correlation_digest = hashlib.sha256(
        b"knowledge-denial-correlation-v1\0" + tenant_id.bytes + correlation.encode("utf-8")
    ).hexdigest()
    metadata = {
        "schema_version": "knowledge_access_denial_v1", "operation": operation,
        "outcome": "not_accessible", "target_observation": "requested_only",
        "requested_resource_digest": hashlib.sha256(
            b"knowledge-denial-target-v1\0" + tenant_id.bytes + operation.encode("ascii")
            + b"\0" + requested_resource_id.bytes
        ).hexdigest(),
    }
    if requested_run_id is not None:
        metadata["requested_run_digest"] = hashlib.sha256(
            b"knowledge-denial-run-v1\0" + tenant_id.bytes + requested_run_id.bytes
        ).hexdigest()
    try:
        async with asyncio.timeout(2), factory() as session, session.begin():
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(text("SET LOCAL statement_timeout = '1s'"))
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
            await append_knowledge_audit(
                session, tenant_id=tenant_id, actor_user_id=actor_user_id,
                resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
                action=AuditAction.KNOWLEDGE_ACCESS_DENIED,
                correlation_id=correlation_digest,
                metadata=metadata,
            )
        return True
    except Exception:
        # 不打印SQL、原异常或请求关联值；不声称每次拒绝都有持久化审计。
        try:
            logger.warning(
                "knowledge_access_denial_audit_unavailable",
                code="KNOWLEDGE_DENIAL_AUDIT_UNAVAILABLE", correlation_id=correlation_digest,
            )
        except Exception:
            # 日志设施故障同样不能覆盖原读取拒绝；返回值仍明确表示未确认审计。
            return False
        return False
