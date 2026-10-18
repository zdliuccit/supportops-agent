"""固定凭据 revision 的实时撤销和受限解封，不返回给模型或前端。"""

import asyncio
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.models import AgentAuditEvent, ModelCredential, ModelCredentialRevision
from supportops_core.secrets import SecretProvider, SecretProviderError


class CredentialAccessDenied(SecretProviderError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CredentialServiceUnavailable(CredentialAccessDenied):
    """凭据服务不可确认的失败域，HTTP不与当前权限拒绝混淆。"""


class CredentialAuditUnavailable(CredentialServiceUnavailable):
    """审计结果未确认时停止返回秘密，不携带数据库原始诊断。"""

    def __init__(self) -> None:
        super().__init__("MODEL_CREDENTIAL_AUDIT_UNAVAILABLE", "凭据访问审计暂不可确认，已停止返回密钥")


class CredentialSealUnavailable(CredentialServiceUnavailable):
    """密封异常仅使用固定提示，不保留provider诊断或输入材料。"""

    def __init__(self) -> None:
        super().__init__("MODEL_CREDENTIAL_SEAL_FAILED", "模型凭据密封暂不可用")


@dataclass(frozen=True, slots=True)
class CredentialRevisionMetadata:
    """状态校验结果不携带密文，也不持有可延迟加载秘密的ORM实体。"""

    revision_id: UUID
    endpoint_id: UUID
    provider: str


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
) -> CredentialRevisionMetadata:
    """只查询当前元数据；不返回可加载密文的历史修订实体。"""
    row = (await session.execute(select(
        ModelCredentialRevision.id, ModelCredential.endpoint_id,
        ModelCredentialRevision.provider, ModelCredentialRevision.revoked_at,
    ).select_from(ModelCredentialRevision).join(ModelCredential,
        ModelCredential.id == ModelCredentialRevision.credential_id).where(
        ModelCredentialRevision.tenant_id == tenant_id, ModelCredential.tenant_id == tenant_id,
        ModelCredentialRevision.credential_id == credential_id, ModelCredentialRevision.revision == revision,
        ModelCredentialRevision.deleted_at.is_(None), ModelCredential.deleted_at.is_(None),
    ))).one_or_none()
    if row is None:
        raise CredentialAccessDenied("MODEL_CREDENTIAL_UNAVAILABLE", "固定凭据 revision 不存在或已删除")
    if row.revoked_at is not None:
        raise CredentialAccessDenied("MODEL_CREDENTIAL_REVOKED", "固定凭据 revision 已撤销，不能改用新密钥继续执行")
    if row.provider != "local_envelope_v1":
        raise CredentialAccessDenied("MODEL_CREDENTIAL_PROVIDER_UNAVAILABLE", "固定凭据 provider 未获支持，不能自动降级")
    return CredentialRevisionMetadata(revision_id=row.id, endpoint_id=row.endpoint_id, provider=row.provider)


def _assert_resolution_scope(metadata: CredentialRevisionMetadata, *, endpoint_id: UUID,
    provider_name: str, revision_id: UUID,
) -> None:
    if metadata.endpoint_id != endpoint_id or metadata.revision_id != revision_id:
        raise CredentialAccessDenied("MODEL_CREDENTIAL_UNAVAILABLE", "固定凭据 revision 与执行范围不一致")
    if metadata.provider != provider_name:
        raise CredentialAccessDenied("MODEL_CREDENTIAL_PROVIDER_UNAVAILABLE", "固定凭据 provider 与执行策略不一致")


async def _persist_resolution_audit(session: AsyncSession, *, audit: AgentAuditEvent,
    factory: async_sessionmaker[AsyncSession] | None,
) -> None:
    """实际执行入口独立提交；超时/失败不重试，也不返回已解封的秘密。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2
    try:
        async with asyncio.timeout_at(deadline):
            if factory is None:
                # 保留核心事务内兼容调用；不提交、回滚或修改调用者事务配置。
                session.add(audit)
                await session.flush()
            else:
                async with factory() as audit_session, audit_session.begin():
                    if audit_session.get_bind().dialect.name == "postgresql":
                        await audit_session.execute(text("SET LOCAL statement_timeout = '1s'"))
                        await audit_session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                    audit_session.add(audit)
                    await audit_session.flush()
        # 驱动即使吞掉取消后迟到完成，也不能把超时审计解释为本次返回许可。
        if loop.time() >= deadline:
            raise CredentialAuditUnavailable()
    except Exception:
        # 提交响应未知不代表未记录；保留事实，不重放或暴露SQL/参数/原异常链。
        raise CredentialAuditUnavailable() from None


async def resolve_credential_secret(session: AsyncSession, *, tenant_id: UUID, credential_id: UUID,
    revision: int, provider: SecretProvider, actor_user_id: UUID, endpoint_id: UUID,
    purpose: Literal["model_run", "model_probe", "connection_probe"], correlation_id: str,
    run_id: UUID | None = None, test_run_id: UUID | None = None,
    audit_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> str:
    metadata = await assert_credential_revision_active(session, tenant_id=tenant_id,
        credential_id=credential_id, revision=revision)
    provider_name = provider.provider_name
    _assert_resolution_scope(metadata, endpoint_id=endpoint_id, provider_name=provider_name,
        revision_id=metadata.revision_id)
    # 密文只由显式解封入口读取；同一SQL再次限制固定定位及当前可用状态。
    sealed_secret = await session.scalar(select(ModelCredentialRevision.encrypted_secret)
        .select_from(ModelCredentialRevision)
        .join(ModelCredential, ModelCredential.id == ModelCredentialRevision.credential_id).where(
            ModelCredentialRevision.id == metadata.revision_id,
            ModelCredentialRevision.tenant_id == tenant_id, ModelCredential.tenant_id == tenant_id,
            ModelCredentialRevision.credential_id == credential_id, ModelCredentialRevision.revision == revision,
            ModelCredential.endpoint_id == endpoint_id, ModelCredentialRevision.provider == provider_name,
            ModelCredentialRevision.revoked_at.is_(None),
            ModelCredentialRevision.deleted_at.is_(None), ModelCredential.deleted_at.is_(None),
        ))
    if sealed_secret is None:
        # 仅重查元数据以保留撤销错误码；不换修订、不重读密文或重新解封。
        await assert_credential_revision_active(session, tenant_id=tenant_id,
            credential_id=credential_id, revision=revision)
        raise CredentialAccessDenied("MODEL_CREDENTIAL_UNAVAILABLE", "固定凭据 revision 当前不可解封")
    try:
        value = provider.open(sealed_secret)
        if not isinstance(value, str) or not value.strip():
            raise SecretProviderError("固定凭据解封结果不可用")
    except Exception:
        # provider异常可能含秘密输入；仅保留固定失败，不传播原异常链或重试。
        raise CredentialAccessDenied("MODEL_CREDENTIAL_UNSEAL_FAILED", "固定凭据无法解封") from None
    audit = AgentAuditEvent(tenant_id=tenant_id, model_endpoint_id=endpoint_id,
        resource_type=AuditResourceType.MODEL_ENDPOINT, action=AuditAction.CREDENTIAL_RESOLVED,
        actor_user_id=actor_user_id, correlation_id=correlation_id,
        metadata_payload={"credential_id": str(credential_id), "credential_revision": revision,
            "provider": metadata.provider, "purpose": purpose, "run_id": str(run_id) if run_id else None,
            "test_run_id": str(test_run_id) if test_run_id else None})
    await _persist_resolution_audit(session, audit=audit, factory=audit_session_factory)
    current = await assert_credential_revision_active(session, tenant_id=tenant_id,
        credential_id=credential_id, revision=revision)
    _assert_resolution_scope(current, endpoint_id=endpoint_id, provider_name=provider_name,
        revision_id=metadata.revision_id)
    return value
