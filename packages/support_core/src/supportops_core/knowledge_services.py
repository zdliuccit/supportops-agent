"""知识来源、文档版本和发布快照的确定性领域服务。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.auth import IdentityContext
from supportops_core.enums import (
    AuditAction,
    AuditResourceType,
    KnowledgeAclSubjectType,
    KnowledgeDocumentStatus,
    KnowledgeSourceEnvironment,
    KnowledgeVersionStatus,
)
from supportops_core.knowledge_content import (
    knowledge_content_digest,
    normalize_knowledge_markdown,
    parse_section_anchors,
)
from supportops_core.models import (
    AgentAuditEvent,
    KnowledgeAclEntry,
    KnowledgeDocument,
    KnowledgeOutboxEvent,
    KnowledgeSnapshot,
    KnowledgeSource,
    KnowledgeVersion,
    utc_now,
)
from supportops_core.services import ResourceNotFoundError


class KnowledgeConflictError(ValueError):
    """知识版本或发布操作与当前状态冲突。"""


class KnowledgeStateError(KnowledgeConflictError):
    """不允许的知识生命周期状态转换。"""


class KnowledgeAccessDeniedError(PermissionError):
    """知识正文或治理操作无权访问。"""


class KnowledgeRevisionConflictError(KnowledgeConflictError):
    """文档在客户端读取后已被其他操作修改。"""


def _transition(version: KnowledgeVersion, target: KnowledgeVersionStatus) -> None:
    allowed: dict[KnowledgeVersionStatus, set[KnowledgeVersionStatus]] = {
        KnowledgeVersionStatus.DRAFT: {KnowledgeVersionStatus.IN_REVIEW},
        KnowledgeVersionStatus.IN_REVIEW: {
            KnowledgeVersionStatus.DRAFT,
            KnowledgeVersionStatus.PUBLISHED,
        },
        KnowledgeVersionStatus.PUBLISHED: {
            KnowledgeVersionStatus.SUPERSEDED,
            KnowledgeVersionStatus.RETIRED,
        },
        KnowledgeVersionStatus.SUPERSEDED: set(),
        KnowledgeVersionStatus.RETIRED: set(),
    }
    if target not in allowed[version.status]:
        raise KnowledgeStateError(f"知识版本不能从 {version.status} 转换为 {target}")


async def append_knowledge_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    resource_type: AuditResourceType,
    action: AuditAction,
    correlation_id: str,
    version_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AgentAuditEvent(
            tenant_id=tenant_id,
            resource_type=resource_type,
            agent_id=None,
            model_endpoint_id=None,
            action=action,
            actor_user_id=actor_user_id,
            version_id=version_id,
            metadata_payload=metadata or {},
            correlation_id=correlation_id,
        )
    )
    await session.flush()


async def create_knowledge_source(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    external_key: str,
    name: str,
    source_type: str,
    environment: KnowledgeSourceEnvironment,
    owner_user_id: UUID,
    correlation_id: str,
) -> KnowledgeSource:
    source = KnowledgeSource(
        tenant_id=tenant_id,
        external_key=external_key.strip(),
        name=name.strip(),
        source_type=source_type.strip(),
        environment=environment,
        owner_user_id=owner_user_id,
        created_by=actor_user_id,
        metadata_payload={},
    )
    session.add(source)
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_SOURCE,
        action=AuditAction.KNOWLEDGE_CREATED,
        correlation_id=correlation_id,
        metadata={"source_id": str(source.id), "external_key": source.external_key},
    )
    return source


async def get_knowledge_document(
    session: AsyncSession, *, document_id: UUID, tenant_id: UUID
) -> KnowledgeDocument:
    document = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
    )
    if document is None:
        raise ResourceNotFoundError("知识文档不存在")
    return document


async def list_knowledge_documents(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    status: KnowledgeDocumentStatus | None = None,
    source_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    keywords: str | None = None,
    page_size: int = 20,
    offset: int = 0,
) -> list[KnowledgeDocument]:
    query = select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)
    if status is not None:
        query = query.where(KnowledgeDocument.status == status)
    if source_id is not None:
        query = query.where(KnowledgeDocument.source_id == source_id)
    if owner_user_id is not None:
        query = query.where(KnowledgeDocument.owner_user_id == owner_user_id)
    if keywords:
        pattern = f"%{keywords.strip()}%"
        query = query.where(KnowledgeDocument.external_key.ilike(pattern))
    return list(
        (
            await session.scalars(
                query.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id)
                .limit(page_size)
                .offset(offset)
            )
        ).all()
    )


async def count_knowledge_documents(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    status: KnowledgeDocumentStatus | None = None,
    source_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    keywords: str | None = None,
) -> int:
    query = select(func.count()).select_from(KnowledgeDocument).where(
        KnowledgeDocument.tenant_id == tenant_id
    )
    if status is not None:
        query = query.where(KnowledgeDocument.status == status)
    if source_id is not None:
        query = query.where(KnowledgeDocument.source_id == source_id)
    if owner_user_id is not None:
        query = query.where(KnowledgeDocument.owner_user_id == owner_user_id)
    if keywords:
        query = query.where(KnowledgeDocument.external_key.ilike(f"%{keywords.strip()}%"))
    return int(await session.scalar(query) or 0)


async def archive_knowledge_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    correlation_id: str,
) -> KnowledgeDocument:
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    document.status = KnowledgeDocumentStatus.ARCHIVED
    document.current_version_id = None
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
        action=AuditAction.KNOWLEDGE_RETIRED,
        correlation_id=correlation_id,
        metadata={"document_id": str(document.id), "status": document.status.value},
    )
    return document


async def update_knowledge_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    owner_user_id: UUID | None = None,
    review_due_at: datetime | None = None,
    correlation_id: str,
) -> KnowledgeDocument:
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    if document.status == KnowledgeDocumentStatus.ARCHIVED:
        raise KnowledgeConflictError("已归档知识文档不可编辑")
    if owner_user_id is not None:
        document.owner_user_id = owner_user_id
    document.review_due_at = review_due_at
    document.updated_at = utc_now()
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
        action=AuditAction.KNOWLEDGE_UPDATED,
        correlation_id=correlation_id,
        metadata={"document_id": str(document.id), "owner_user_id": str(document.owner_user_id)},
    )
    return document


async def replace_knowledge_acl(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    entries: list[tuple[KnowledgeAclSubjectType, str]],
    correlation_id: str,
) -> list[KnowledgeAclEntry]:
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    normalized = {(subject_type, subject_id.strip()) for subject_type, subject_id in entries}
    if any(not subject_id for _subject_type, subject_id in normalized):
        raise KnowledgeConflictError("知识 ACL 主体标识不能为空")
    await session.execute(
        delete(KnowledgeAclEntry).where(
            KnowledgeAclEntry.document_id == document.id,
            KnowledgeAclEntry.tenant_id == tenant_id,
        )
    )
    records = [
        KnowledgeAclEntry(
            tenant_id=tenant_id,
            document_id=document.id,
            subject_type=subject_type,
            subject_id=subject_id,
            created_by=actor_user_id,
        )
        for subject_type, subject_id in sorted(normalized, key=lambda value: (value[0].value, value[1]))
    ]
    session.add_all(records)
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
        action=AuditAction.KNOWLEDGE_ACL_UPDATED,
        correlation_id=correlation_id,
        metadata={"document_id": str(document.id), "entry_count": len(records)},
    )
    return records


async def refresh_knowledge_document_status(
    session: AsyncSession, *, document_id: UUID, tenant_id: UUID
) -> KnowledgeDocument:
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    if document.status == KnowledgeDocumentStatus.ARCHIVED:
        return document
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == document.current_version_id,
            KnowledgeVersion.tenant_id == tenant_id,
            KnowledgeVersion.status == KnowledgeVersionStatus.PUBLISHED,
        )
    )
    now = datetime.now(UTC)
    effective_from = version.effective_from if version is not None else None
    effective_until = version.effective_until if version is not None else None
    if effective_from is not None and effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=UTC)
    if effective_until is not None and effective_until.tzinfo is None:
        effective_until = effective_until.replace(tzinfo=UTC)
    if version is not None and effective_from <= now and (
        effective_until is None or effective_until > now
    ):
        document.status = KnowledgeDocumentStatus.PUBLISHED
    elif version is not None and effective_until is not None and effective_until <= now:
        document.status = KnowledgeDocumentStatus.EXPIRED
    elif document.status == KnowledgeDocumentStatus.PUBLISHED:
        document.status = KnowledgeDocumentStatus.EXPIRED
    await session.flush()
    return document


async def create_knowledge_document(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    source_id: UUID,
    external_key: str,
    owner_user_id: UUID,
    title: str,
    content_markdown: str,
    change_summary: str = "",
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
    correlation_id: str,
) -> KnowledgeDocument:
    source = await session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.id == source_id, KnowledgeSource.tenant_id == tenant_id
        )
    )
    if source is None:
        raise ResourceNotFoundError("知识来源不存在")
    content = normalize_knowledge_markdown(content_markdown)
    digest = knowledge_content_digest(content)
    existing = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.source_id == source_id,
            KnowledgeDocument.external_key == external_key.strip(),
        )
    )
    if existing is not None:
        raise KnowledgeConflictError("知识文档外部标识已存在")
    document = KnowledgeDocument(
        tenant_id=tenant_id,
        source_id=source_id,
        external_key=external_key.strip(),
        owner_user_id=owner_user_id,
        status=KnowledgeDocumentStatus.DRAFT,
        created_by=actor_user_id,
    )
    session.add(document)
    await session.flush()
    version = KnowledgeVersion(
        tenant_id=tenant_id,
        document_id=document.id,
        version_number=1,
        status=KnowledgeVersionStatus.DRAFT,
        title=title.strip(),
        content_markdown=content,
        content_digest=digest,
        section_anchors=parse_section_anchors(content),
        change_summary=change_summary.strip(),
        effective_from=effective_from or utc_now(),
        effective_until=effective_until,
        created_by=actor_user_id,
    )
    session.add(version)
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT,
        action=AuditAction.KNOWLEDGE_CREATED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"document_id": str(document.id), "version_number": 1, "content_digest": digest},
    )
    return document


async def create_knowledge_version(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    title: str,
    content_markdown: str,
    expected_revision: int,
    change_summary: str = "",
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
    allow_duplicate: bool = False,
    correlation_id: str,
) -> KnowledgeVersion:
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    latest = await session.scalar(
        select(KnowledgeVersion)
        .where(
            KnowledgeVersion.document_id == document.id,
            KnowledgeVersion.tenant_id == tenant_id,
        )
        .order_by(KnowledgeVersion.version_number.desc())
        .limit(1)
    )
    current_revision = latest.version_number if latest is not None else 0
    if current_revision != expected_revision:
        raise KnowledgeRevisionConflictError("知识文档版本已被其他管理员修改")
    content = normalize_knowledge_markdown(content_markdown)
    digest = knowledge_content_digest(content)
    if not allow_duplicate:
        duplicate = await session.scalar(
            select(KnowledgeVersion).where(
                KnowledgeVersion.document_id == document.id,
                KnowledgeVersion.content_digest == digest,
            )
        )
        if duplicate is not None:
            raise KnowledgeConflictError("知识内容未发生变化")
    version = KnowledgeVersion(
        tenant_id=tenant_id,
        document_id=document.id,
        version_number=current_revision + 1,
        status=KnowledgeVersionStatus.DRAFT,
        title=title.strip(),
        content_markdown=content,
        content_digest=digest,
        section_anchors=parse_section_anchors(content),
        change_summary=change_summary.strip(),
        effective_from=effective_from or utc_now(),
        effective_until=effective_until,
        created_by=actor_user_id,
    )
    session.add(version)
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION,
        action=AuditAction.KNOWLEDGE_UPDATED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"document_id": str(document.id), "version_number": version.version_number},
    )
    return version


async def submit_knowledge_version_for_review(
    session: AsyncSession,
    *,
    version_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    correlation_id: str,
) -> KnowledgeVersion:
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == version_id, KnowledgeVersion.tenant_id == tenant_id
        )
    )
    if version is None:
        raise ResourceNotFoundError("知识版本不存在")
    _transition(version, KnowledgeVersionStatus.IN_REVIEW)
    version.status = KnowledgeVersionStatus.IN_REVIEW
    version.reviewed_by = actor_user_id
    version.reviewed_at = utc_now()
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION,
        action=AuditAction.KNOWLEDGE_REVIEW_SUBMITTED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"version_number": version.version_number},
    )
    return version


async def _acl_digest(session: AsyncSession, *, document_id: UUID, tenant_id: UUID) -> str:
    entries = list(
        (
            await session.scalars(
                select(KnowledgeAclEntry)
                .where(
                    KnowledgeAclEntry.document_id == document_id,
                    KnowledgeAclEntry.tenant_id == tenant_id,
                )
                .order_by(
                    KnowledgeAclEntry.subject_type,
                    KnowledgeAclEntry.subject_id,
                    KnowledgeAclEntry.id,
                )
            )
        ).all()
    )
    payload = "|".join(f"{entry.subject_type}:{entry.subject_id}" for entry in entries)
    return hashlib.sha256(payload.encode()).hexdigest()


async def publish_knowledge_version(
    session: AsyncSession,
    *,
    version_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    runtime_environment: str,
    correlation_id: str,
) -> KnowledgeSnapshot:
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == version_id, KnowledgeVersion.tenant_id == tenant_id
        )
    )
    if version is None:
        raise ResourceNotFoundError("知识版本不存在")
    document = await get_knowledge_document(session, document_id=version.document_id, tenant_id=tenant_id)
    source = await session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.id == document.source_id, KnowledgeSource.tenant_id == tenant_id
        )
    )
    if source is None:
        raise ResourceNotFoundError("知识来源不存在")
    if runtime_environment == "production" and source.environment == KnowledgeSourceEnvironment.MOCK:
        raise KnowledgeConflictError("Mock 知识来源不得发布到 production")
    _transition(version, KnowledgeVersionStatus.PUBLISHED)
    previous = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == document.current_version_id,
            KnowledgeVersion.tenant_id == tenant_id,
        )
    )
    if previous is not None:
        _transition(previous, KnowledgeVersionStatus.SUPERSEDED)
        previous.status = KnowledgeVersionStatus.SUPERSEDED
    now = utc_now()
    version.status = KnowledgeVersionStatus.PUBLISHED
    version.published_by = actor_user_id
    version.published_at = now
    document.current_version_id = version.id
    document.status = KnowledgeDocumentStatus.PUBLISHED
    acl_digest = await _acl_digest(session, document_id=document.id, tenant_id=tenant_id)
    snapshot = KnowledgeSnapshot(
        tenant_id=tenant_id,
        document_id=document.id,
        version_id=version.id,
        content_digest=version.content_digest,
        title=version.title,
        section_anchors=version.section_anchors,
        acl_digest=acl_digest,
        effective_from=version.effective_from,
        effective_until=version.effective_until,
        published_at=now,
    )
    session.add(snapshot)
    event = KnowledgeOutboxEvent(
        tenant_id=tenant_id,
        event_key=f"knowledge.publish:{version.id}",
        event_type="knowledge.published",
        document_id=document.id,
        version_id=version.id,
        payload={"snapshot_id": str(snapshot.id), "content_digest": version.content_digest},
        attempts=0,
        available_at=now,
    )
    session.add(event)
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION,
        action=AuditAction.KNOWLEDGE_PUBLISHED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"document_id": str(document.id), "snapshot_id": str(snapshot.id)},
    )
    return snapshot


async def retire_knowledge_version(
    session: AsyncSession,
    *,
    version_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    correlation_id: str,
) -> KnowledgeVersion:
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == version_id, KnowledgeVersion.tenant_id == tenant_id
        )
    )
    if version is None:
        raise ResourceNotFoundError("知识版本不存在")
    _transition(version, KnowledgeVersionStatus.RETIRED)
    version.status = KnowledgeVersionStatus.RETIRED
    if version.published_at is not None:
        version.effective_until = min(version.effective_until or utc_now(), utc_now())
    document = await get_knowledge_document(session, document_id=version.document_id, tenant_id=tenant_id)
    if document.current_version_id == version.id:
        document.current_version_id = None
        document.status = KnowledgeDocumentStatus.EXPIRED
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION,
        action=AuditAction.KNOWLEDGE_RETIRED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"document_id": str(document.id)},
    )
    return version


async def rollback_knowledge_version(
    session: AsyncSession,
    *,
    document_id: UUID,
    source_version_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    expected_revision: int,
    correlation_id: str,
) -> KnowledgeVersion:
    source = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == source_version_id,
            KnowledgeVersion.document_id == document_id,
            KnowledgeVersion.tenant_id == tenant_id,
        )
    )
    if source is None:
        raise ResourceNotFoundError("要回滚的知识版本不存在")
    version = await create_knowledge_version(
        session,
        document_id=document_id,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        title=source.title,
        content_markdown=source.content_markdown,
        expected_revision=expected_revision,
        change_summary=f"从版本 {source.version_number} 回滚",
        effective_from=source.effective_from,
        effective_until=source.effective_until,
        allow_duplicate=True,
        correlation_id=correlation_id,
    )
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION,
        action=AuditAction.KNOWLEDGE_ROLLED_BACK,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={"source_version_id": str(source.id)},
    )
    return version


async def knowledge_access_allowed(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    identity: IdentityContext,
) -> bool:
    if identity.principal.tenant_id != tenant_id:
        return False
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == document.current_version_id,
            KnowledgeVersion.tenant_id == tenant_id,
            KnowledgeVersion.status == KnowledgeVersionStatus.PUBLISHED,
        )
    )
    if version is None:
        return False
    now = datetime.now(UTC)
    effective_from = version.effective_from
    effective_until = version.effective_until
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=UTC)
    if effective_until is not None and effective_until.tzinfo is None:
        effective_until = effective_until.replace(tzinfo=UTC)
    if effective_from > now or (
        effective_until is not None and effective_until <= now
    ):
        return False
    entries = list(
        (
            await session.scalars(
                select(KnowledgeAclEntry).where(
                    KnowledgeAclEntry.document_id == document.id,
                    KnowledgeAclEntry.tenant_id == tenant_id,
                )
            )
        ).all()
    )
    if not entries:
        return False
    role_ids = {entry.subject_id for entry in entries if entry.subject_type == KnowledgeAclSubjectType.ROLE}
    if role_ids.intersection(identity.principal.roles):
        return True
    user_id = str(identity.user.id)
    if any(entry.subject_type == KnowledgeAclSubjectType.USER and entry.subject_id == user_id for entry in entries):
        return True
    org_id = str(identity.user.organization_unit_id) if identity.user.organization_unit_id else None
    return org_id is not None and any(
        entry.subject_type == KnowledgeAclSubjectType.ORGANIZATION_UNIT and entry.subject_id == org_id
        for entry in entries
    )


async def get_published_knowledge_content(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    identity: IdentityContext,
) -> KnowledgeVersion:
    if not await knowledge_access_allowed(
        session, document_id=document_id, tenant_id=tenant_id, identity=identity
    ):
        raise KnowledgeAccessDeniedError("无权读取该知识正文")
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == document.current_version_id,
            KnowledgeVersion.tenant_id == tenant_id,
            KnowledgeVersion.status == KnowledgeVersionStatus.PUBLISHED,
        )
    )
    if version is None:
        raise ResourceNotFoundError("当前没有有效的知识版本")
    return version


async def get_effective_knowledge_snapshot(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    identity: IdentityContext,
) -> KnowledgeSnapshot:
    if not await knowledge_access_allowed(
        session, document_id=document_id, tenant_id=tenant_id, identity=identity
    ):
        raise KnowledgeAccessDeniedError("无权读取该知识快照")
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    snapshot = await session.scalar(
        select(KnowledgeSnapshot).where(
            KnowledgeSnapshot.document_id == document.id,
            KnowledgeSnapshot.version_id == document.current_version_id,
            KnowledgeSnapshot.tenant_id == tenant_id,
        )
    )
    if snapshot is None:
        raise ResourceNotFoundError("当前没有有效的知识快照")
    return snapshot


async def list_knowledge_audit_events(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    page_size: int = 20,
    offset: int = 0,
) -> list[AgentAuditEvent]:
    return list(
        (
            await session.scalars(
                select(AgentAuditEvent)
                .where(
                    AgentAuditEvent.tenant_id == tenant_id,
                    AgentAuditEvent.resource_type.in_(
                        {
                            AuditResourceType.KNOWLEDGE_SOURCE,
                            AuditResourceType.KNOWLEDGE_DOCUMENT,
                            AuditResourceType.KNOWLEDGE_VERSION,
                        }
                    ),
                )
                .order_by(AgentAuditEvent.created_at.desc(), AgentAuditEvent.id.desc())
                .limit(page_size)
                .offset(offset)
            )
        ).all()
    )


async def count_knowledge_audit_events(session: AsyncSession, *, tenant_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(AgentAuditEvent)
            .where(
                AgentAuditEvent.tenant_id == tenant_id,
                AgentAuditEvent.resource_type.in_(
                    {
                        AuditResourceType.KNOWLEDGE_SOURCE,
                        AuditResourceType.KNOWLEDGE_DOCUMENT,
                        AuditResourceType.KNOWLEDGE_VERSION,
                    }
                ),
            )
        )
        or 0
    )


async def import_mock_knowledge_documents(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_user_id: UUID,
    source_external_key: str,
    source_name: str,
    items: list[dict[str, str]],
    correlation_id: str,
) -> tuple[int, int]:
    """显式、租户限定地导入 P0 Mock 文档；返回 (created, skipped)。"""
    source = await session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.tenant_id == tenant_id,
            KnowledgeSource.external_key == source_external_key,
        )
    )
    if source is None:
        source = await create_knowledge_source(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            external_key=source_external_key,
            name=source_name,
            source_type="mock",
            environment=KnowledgeSourceEnvironment.MOCK,
            owner_user_id=actor_user_id,
            correlation_id=correlation_id,
        )
    created = 0
    skipped = 0
    for item in items:
        existing = await session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.source_id == source.id,
                KnowledgeDocument.external_key == item["external_key"].strip(),
            )
        )
        if existing is not None:
            skipped += 1
            continue
        await create_knowledge_document(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            source_id=source.id,
            external_key=item["external_key"],
            owner_user_id=actor_user_id,
            title=item["title"],
            content_markdown=item["content_markdown"],
            change_summary="P0 Mock 知识清单导入",
            correlation_id=correlation_id,
        )
        created += 1
    return created, skipped
