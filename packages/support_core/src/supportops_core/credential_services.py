"""固定凭据 revision 的实时撤销和受限解封，不返回给模型或前端。"""

from typing import Literal
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.models import AgentAuditEvent, ModelCredential, ModelCredentialRevision
from supportops_core.secrets import SecretProvider, SecretProviderError


class CredentialAccessDenied(SecretProviderError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def record_credential_denial(session: AsyncSession, *, tenant_id: UUID, actor_user_id: UUID,
    endpoint_id: UUID, revision: int, purpose: str, correlation_id: str, code: str,
    run_id: UUID | None = None, test_run_id: UUID | None = None,
) -> None:
    """终止事务追加拒绝事实，不记录密文或解封异常正文。"""
    session.add(AgentAuditEvent(tenant_id=tenant_id, model_endpoint_id=endpoint_id,
        resource_type=AuditResourceType.MODEL_ENDPOINT, action=AuditAction.CREDENTIAL_ACCESS_DENIED,
        actor_user_id=actor_user_id, correlation_id=correlation_id,
        metadata_payload={"credential_revision": revision, "purpose": purpose, "code": code,
            "run_id": str(run_id) if run_id else None, "test_run_id": str(test_run_id) if test_run_id else None}))


async def assert_credential_revision_active(session: AsyncSession, *, tenant_id: UUID,
    credential_id: UUID, revision: int,
) -> ModelCredentialRevision:
    row = await session.scalar(select(ModelCredentialRevision).join(ModelCredential,
        ModelCredential.id == ModelCredentialRevision.credential_id).where(
        ModelCredentialRevision.tenant_id == tenant_id, ModelCredential.tenant_id == tenant_id,
        ModelCredentialRevision.credential_id == credential_id, ModelCredentialRevision.revision == revision)
        .execution_options(populate_existing=True))
    if row is None:
        raise CredentialAccessDenied("MODEL_CREDENTIAL_UNAVAILABLE", "固定凭据 revision 不存在或已删除")
    if row.revoked_at is not None:
        raise CredentialAccessDenied("MODEL_CREDENTIAL_REVOKED", "固定凭据 revision 已撤销，不能改用新密钥继续执行")
    if row.provider != "local_envelope_v1":
        raise CredentialAccessDenied("MODEL_CREDENTIAL_PROVIDER_UNAVAILABLE", "固定凭据 provider 未获支持，不能自动降级")
    return row


async def resolve_credential_secret(session: AsyncSession, *, tenant_id: UUID, credential_id: UUID,
    revision: int, provider: SecretProvider, actor_user_id: UUID, endpoint_id: UUID,
    purpose: Literal["model_run", "model_probe", "connection_probe"], correlation_id: str,
    run_id: UUID | None = None, test_run_id: UUID | None = None,
    audit_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> str:
    row = await assert_credential_revision_active(session, tenant_id=tenant_id, credential_id=credential_id, revision=revision)
    if row.provider != provider.provider_name:
        raise CredentialAccessDenied("MODEL_CREDENTIAL_PROVIDER_UNAVAILABLE", "固定凭据 provider 与执行策略不一致")
    try:
        value = provider.open(row.encrypted_secret)
    except SecretProviderError as exc:
        raise CredentialAccessDenied("MODEL_CREDENTIAL_UNSEAL_FAILED", "固定凭据无法解封") from exc
    audit = AgentAuditEvent(tenant_id=tenant_id, model_endpoint_id=endpoint_id,
        resource_type=AuditResourceType.MODEL_ENDPOINT, action=AuditAction.CREDENTIAL_RESOLVED,
        actor_user_id=actor_user_id, correlation_id=correlation_id,
        metadata_payload={"credential_id": str(credential_id), "credential_revision": revision,
            "provider": row.provider, "purpose": purpose, "run_id": str(run_id) if run_id else None,
            "test_run_id": str(test_run_id) if test_run_id else None})
    if audit_session_factory is None:
        # 兼容显式事务内的核心调用；API/Worker 实际执行入口均传入独立审计工厂。
        session.add(audit)
        await session.flush()
    else:
        async with audit_session_factory() as audit_session, audit_session.begin():
            audit_session.add(audit)
            await audit_session.flush()
    await assert_credential_revision_active(session, tenant_id=tenant_id, credential_id=credential_id, revision=revision)
    return value
