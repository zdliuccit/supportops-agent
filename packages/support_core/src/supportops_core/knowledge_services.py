"""知识来源、文档版本和发布快照的确定性领域服务。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, load_only
from sqlalchemy.sql.elements import ColumnElement

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
    User,
    utc_now,
)
from supportops_core.services import ResourceNotFoundError


class KnowledgeConflictError(ValueError):
    """知识版本或发布操作与当前状态冲突。"""


class KnowledgeStateError(KnowledgeConflictError):
    """不允许的知识生命周期状态转换。"""


class KnowledgeAccessDeniedError(PermissionError):
    """知识正文或治理操作无权访问。"""


async def append_knowledge_index_event(
    session: AsyncSession,
    *,
    document: KnowledgeDocument,
    event_type: str,
    version_id: UUID | None = None,
    event_key: str | None = None,
) -> None:
    """与产品变更同事务保存索引事实，不包含正文或 ACL 主体。"""
    session.add(
        KnowledgeOutboxEvent(
            tenant_id=document.tenant_id,
            document_id=document.id,
            version_id=version_id,
            event_type=event_type,
            event_key=event_key or f"{event_type}:{document.id}:{uuid4()}",
            payload={},
            attempts=0,
            available_at=utc_now(),
        )
    )


async def lock_knowledge_document(
    session: AsyncSession, *, document_id: UUID, tenant_id: UUID
) -> KnowledgeDocument:
    """生命周期、ACL 与索引切换使用相同文档锁顺序。"""
    document = await session.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if document is None:
        raise ResourceNotFoundError("知识文档不存在")
    return document


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


def _source_filters(tenant_id: UUID, keywords: str | None) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [KnowledgeSource.tenant_id == tenant_id]
    if keywords and keywords.strip():
        filters.append(KnowledgeSource.name.icontains(keywords.strip(), autoescape=True))
    return filters


async def list_knowledge_sources(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    page_size: int,
    offset: int,
    keywords: str | None = None,
) -> list[KnowledgeSource]:
    """先限定租户与查询条件，再按来源名称和唯一键分页。"""
    return list(
        (
            await session.scalars(
                select(KnowledgeSource)
                .where(*_source_filters(tenant_id, keywords))
                .order_by(KnowledgeSource.name, KnowledgeSource.id)
                .offset(offset)
                .limit(page_size)
            )
        ).all()
    )


async def count_knowledge_sources(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    keywords: str | None = None,
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeSource)
            .where(*_source_filters(tenant_id, keywords))
        )
        or 0
    )


async def list_knowledge_versions(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    page_size: int,
    offset: int,
) -> list[KnowledgeVersion]:
    """按版本号和唯一键倒序分页，不将其他租户版本计入列表。"""
    return list(
        (
            await session.scalars(
                select(KnowledgeVersion)
                .options(
                    defer(KnowledgeVersion.content_markdown, raiseload=True),
                    defer(KnowledgeVersion.section_anchors, raiseload=True),
                    defer(KnowledgeVersion.review_comment, raiseload=True),
                )
                .where(
                    KnowledgeVersion.tenant_id == tenant_id,
                    KnowledgeVersion.document_id == document_id,
                )
                .order_by(KnowledgeVersion.version_number.desc(), KnowledgeVersion.id.desc())
                .offset(offset)
                .limit(page_size)
            )
        ).all()
    )


async def count_knowledge_versions(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeVersion)
            .where(
                KnowledgeVersion.tenant_id == tenant_id, KnowledgeVersion.document_id == document_id
            )
        )
        or 0
    )


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
    owner_name: str | None = None,
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
    if owner_name:
        query = query.join(User, User.id == KnowledgeDocument.owner_user_id).where(
            User.tenant_id == tenant_id,
            User.display_name.ilike(f"%{owner_name.strip()}%"),
        )
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
    owner_name: str | None = None,
    keywords: str | None = None,
) -> int:
    query = (
        select(func.count())
        .select_from(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == tenant_id)
    )
    if status is not None:
        query = query.where(KnowledgeDocument.status == status)
    if source_id is not None:
        query = query.where(KnowledgeDocument.source_id == source_id)
    if owner_user_id is not None:
        query = query.where(KnowledgeDocument.owner_user_id == owner_user_id)
    if owner_name:
        query = query.join(User, User.id == KnowledgeDocument.owner_user_id).where(
            User.tenant_id == tenant_id,
            User.display_name.ilike(f"%{owner_name.strip()}%"),
        )
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
    document = await lock_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    document.status = KnowledgeDocumentStatus.ARCHIVED
    document.deleted_at = utc_now()
    document.updated_at = document.deleted_at
    await append_knowledge_index_event(session, document=document, event_type="knowledge.archived")
    for resource in (KnowledgeVersion, KnowledgeSnapshot, KnowledgeAclEntry):
        await session.execute(
            update(resource)
            .where(
                resource.document_id == document.id,
                resource.tenant_id == tenant_id,
                resource.deleted_at.is_(None),
            )
            .values(deleted_at=document.deleted_at)
        )
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
    document = await lock_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
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
    document = await lock_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    normalized = {(subject_type, subject_id.strip()) for subject_type, subject_id in entries}
    if any(not subject_id for _subject_type, subject_id in normalized):
        raise KnowledgeConflictError("知识 ACL 主体标识不能为空")
    await session.execute(
        update(KnowledgeAclEntry)
        .where(
            KnowledgeAclEntry.document_id == document.id,
            KnowledgeAclEntry.tenant_id == tenant_id,
            KnowledgeAclEntry.deleted_at.is_(None),
        )
        .values(deleted_at=utc_now())
    )
    records = [
        KnowledgeAclEntry(
            tenant_id=tenant_id,
            document_id=document.id,
            subject_type=subject_type,
            subject_id=subject_id,
            created_by=actor_user_id,
        )
        for subject_type, subject_id in sorted(
            normalized, key=lambda value: (value[0].value, value[1])
        )
    ]
    session.add_all(records)
    await append_knowledge_index_event(
        session, document=document, event_type="knowledge.acl_changed"
    )
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
    document = await lock_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    previous_status = document.status
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
    if (
        version is not None
        and effective_from is not None
        and effective_from <= now
        and (effective_until is None or effective_until > now)
    ):
        document.status = KnowledgeDocumentStatus.PUBLISHED
    elif version is not None and effective_until is not None and effective_until <= now:
        document.status = KnowledgeDocumentStatus.EXPIRED
    elif document.status == KnowledgeDocumentStatus.PUBLISHED:
        document.status = KnowledgeDocumentStatus.EXPIRED
    if document.status == KnowledgeDocumentStatus.EXPIRED and previous_status != document.status:
        await append_knowledge_index_event(
            session,
            document=document,
            event_type="knowledge.expired",
            version_id=document.current_version_id,
        )
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
    document = await lock_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
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
    expected_review_revision: int | None = None,
) -> KnowledgeVersion:
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == version_id, KnowledgeVersion.tenant_id == tenant_id
        )
    )
    if version is None:
        raise ResourceNotFoundError("知识版本不存在")
    await lock_knowledge_document(session, document_id=version.document_id, tenant_id=tenant_id)
    await session.refresh(version)
    await _knowledge_governance_actor(session, tenant_id=tenant_id, user_id=actor_user_id)
    if expected_review_revision is not None and version.review_revision != expected_review_revision:
        raise KnowledgeRevisionConflictError("审核状态已变化，请刷新后重新提交")
    if version.status == KnowledgeVersionStatus.IN_REVIEW:
        if version.review_decision != "approved" or expected_review_revision is None:
            raise KnowledgeStateError("版本已在审核中；已有批准重新送审必须提供当前 revision")
    else:
        _transition(version, KnowledgeVersionStatus.IN_REVIEW)
    version.status = KnowledgeVersionStatus.IN_REVIEW
    version.submitted_by, version.submitted_at = actor_user_id, utc_now()
    version.reviewed_by, version.reviewed_at = None, None
    version.review_decision, version.review_scope_digest, version.review_comment = (
        "pending",
        None,
        None,
    )
    version.review_revision += 1
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION,
        action=AuditAction.KNOWLEDGE_REVIEW_SUBMITTED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={
            "version_number": version.version_number,
            "review_revision": version.review_revision,
            "content_digest": version.content_digest,
            "policy_version": "separate_reviewer_v1",
        },
    )
    return version


async def _knowledge_governance_actor(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID
) -> User:
    """领域层也复检审核/发布权限，避免后台调用绕过 API 依赖。"""
    user = await session.scalar(
        select(User)
        .where(User.id == user_id, User.tenant_id == tenant_id, User.status == "active")
        .execution_options(populate_existing=True)
    )
    if user is None or "platform_admin" not in user.roles:
        raise KnowledgeAccessDeniedError("知识审核与发布需要当前有效的平台管理员权限")
    return user


async def _review_scope_digest(session: AsyncSession, version: KnowledgeVersion) -> str:
    actual_digest = knowledge_content_digest(normalize_knowledge_markdown(version.content_markdown))
    if actual_digest != version.content_digest:
        raise KnowledgeConflictError("知识正文与不可变摘要不一致")

    def timestamp(value: datetime | None) -> str | None:
        return (
            (
                value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            ).isoformat()
            if value
            else None
        )

    # 审核/发布调用方均持文档锁；固定审核时的发布基线，竞争者不能覆盖新发布。
    current_version_id = await session.scalar(
        select(KnowledgeDocument.current_version_id).where(
            KnowledgeDocument.id == version.document_id,
            KnowledgeDocument.tenant_id == version.tenant_id,
        )
    )
    # 发布后再失效可能让指针回到空值；包含保留历史的计数防止旧批准复活。
    publication_count = await session.scalar(
        select(func.count())
        .select_from(KnowledgeSnapshot)
        .where(
            KnowledgeSnapshot.document_id == version.document_id,
            KnowledgeSnapshot.tenant_id == version.tenant_id,
        )
        .execution_options(include_deleted=True)
    )
    payload = {
        "schema_version": "knowledge_review_scope_v2",
        "current_version_id": str(current_version_id) if current_version_id is not None else None,
        "publication_count": publication_count,
        "content_digest": actual_digest,
        "title": version.title,
        "document_id": str(version.document_id),
        "effective_from": timestamp(version.effective_from),
        "effective_until": timestamp(version.effective_until),
        "acl_digest": await _acl_digest(
            session, document_id=version.document_id, tenant_id=version.tenant_id
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def decide_knowledge_review(
    session: AsyncSession,
    *,
    version_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    decision: str,
    expected_review_revision: int,
    expected_content_digest: str,
    comment: str,
    correlation_id: str,
) -> KnowledgeVersion:
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == version_id, KnowledgeVersion.tenant_id == tenant_id
        )
    )
    if version is None:
        raise ResourceNotFoundError("知识版本不存在")
    await lock_knowledge_document(session, document_id=version.document_id, tenant_id=tenant_id)
    await session.refresh(version)
    await _knowledge_governance_actor(session, tenant_id=tenant_id, user_id=actor_user_id)
    if actor_user_id in {version.created_by, version.submitted_by}:
        raise KnowledgeAccessDeniedError(
            "创建人和提交人不能审核自己的知识版本，请由另一位管理员审核"
        )
    if (
        version.review_revision != expected_review_revision
        or version.content_digest != expected_content_digest
    ):
        raise KnowledgeRevisionConflictError("审核范围已变化，请刷新版本后重新审核")
    if version.status != KnowledgeVersionStatus.IN_REVIEW or version.review_decision != "pending":
        raise KnowledgeStateError("只有待审核版本可以作出审核决策")
    reason = comment.strip()
    if (
        decision not in {"approved", "rejected"}
        or len(reason) > 1000
        or (decision == "rejected" and not reason)
    ):
        raise KnowledgeConflictError("审核决策无效；拒绝需要说明且不超过 1000 字符")
    version.review_decision, version.review_comment = decision, reason or None
    version.reviewed_by, version.reviewed_at = actor_user_id, utc_now()
    version.review_scope_digest = (
        await _review_scope_digest(session, version) if decision == "approved" else None
    )
    version.review_revision += 1
    if decision == "rejected":
        _transition(version, KnowledgeVersionStatus.DRAFT)
        version.status = KnowledgeVersionStatus.DRAFT
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION,
        action=AuditAction.KNOWLEDGE_REVIEW_APPROVED
        if decision == "approved"
        else AuditAction.KNOWLEDGE_REVIEW_REJECTED,
        correlation_id=correlation_id,
        version_id=version.id,
        metadata={
            "decision": decision,
            "review_revision": version.review_revision,
            "content_digest": version.content_digest,
            "scope_digest": version.review_scope_digest,
            "comment_digest": hashlib.sha256(reason.encode()).hexdigest(),
            "policy_version": "separate_reviewer_v1",
        },
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
    expected_review_revision: int | None = None,
) -> KnowledgeSnapshot:
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == version_id, KnowledgeVersion.tenant_id == tenant_id
        )
    )
    if version is None:
        raise ResourceNotFoundError("知识版本不存在")
    document = await lock_knowledge_document(
        session, document_id=version.document_id, tenant_id=tenant_id
    )
    await session.refresh(version)
    await _knowledge_governance_actor(session, tenant_id=tenant_id, user_id=actor_user_id)
    if expected_review_revision is not None and version.review_revision != expected_review_revision:
        raise KnowledgeRevisionConflictError("审核决策已变化，请刷新后发布")
    if (
        version.review_decision != "approved"
        or version.reviewed_by is None
        or version.reviewed_at is None
        or version.reviewed_by in {version.created_by, version.submitted_by}
    ):
        raise KnowledgeStateError("发布需要独立审核批准")
    if version.review_scope_digest != await _review_scope_digest(session, version):
        raise KnowledgeRevisionConflictError(
            "审核范围或当前发布版本已变化，请重新送审并取得独立批准"
        )
    await _knowledge_governance_actor(session, tenant_id=tenant_id, user_id=version.reviewed_by)
    source = await session.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.id == document.source_id, KnowledgeSource.tenant_id == tenant_id
        )
    )
    if source is None:
        raise ResourceNotFoundError("知识来源不存在")
    if (
        runtime_environment == "production"
        and source.environment == KnowledgeSourceEnvironment.MOCK
    ):
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
        await append_knowledge_index_event(
            session, document=document, event_type="knowledge.superseded", version_id=previous.id
        )
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
    await session.flush()
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
        metadata={
            "document_id": str(document.id),
            "snapshot_id": str(snapshot.id),
            "review_revision": version.review_revision,
            "reviewed_by": str(version.reviewed_by),
            "review_scope_digest": version.review_scope_digest,
        },
    )
    return snapshot


async def withdraw_knowledge_candidate(
    session: AsyncSession,
    *,
    version_id: UUID,
    tenant_id: UUID,
    actor_user_id: UUID,
    expected_review_revision: int,
    expected_content_digest: str,
    reason_code: str,
    correlation_id: str,
) -> KnowledgeVersion:
    """本人显式撤回未发布的最新候选；保留正文/审核历史和当前发布，不产生索引事件。"""
    if reason_code not in {
        "source_scope_changed",
        "manual_resolution_required",
        "no_longer_needed",
    }:
        raise KnowledgeConflictError("候选撤回原因无效")
    document_id = await session.scalar(
        select(KnowledgeVersion.document_id).where(
            KnowledgeVersion.id == version_id, KnowledgeVersion.tenant_id == tenant_id
        )
    )
    if document_id is None:
        raise ResourceNotFoundError("知识候选不存在")
    document = await lock_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    await _knowledge_governance_actor(session, tenant_id=tenant_id, user_id=actor_user_id)
    candidate = await session.scalar(
        select(KnowledgeVersion)
        .where(
            KnowledgeVersion.id == version_id,
            KnowledgeVersion.document_id == document.id,
            KnowledgeVersion.tenant_id == tenant_id,
        )
        .options(
            load_only(
                KnowledgeVersion.id,
                KnowledgeVersion.status,
                KnowledgeVersion.created_by,
                KnowledgeVersion.published_at,
                KnowledgeVersion.review_revision,
                KnowledgeVersion.content_digest,
            )
        )
        .execution_options(populate_existing=True)
    )
    latest = await session.scalar(
        select(KnowledgeVersion.id)
        .where(KnowledgeVersion.document_id == document.id, KnowledgeVersion.tenant_id == tenant_id)
        .order_by(KnowledgeVersion.version_number.desc())
        .limit(1)
        .execution_options(include_deleted=True)
    )
    if (
        candidate is None
        or candidate.created_by != actor_user_id
        or candidate.status not in {KnowledgeVersionStatus.DRAFT, KnowledgeVersionStatus.IN_REVIEW}
        or candidate.published_at is not None
        or document.current_version_id == candidate.id
        or document.status == KnowledgeDocumentStatus.ARCHIVED
        or latest != candidate.id
    ):
        raise KnowledgeStateError("只能撤回本人未发布且仍为最新的候选")
    if (
        candidate.review_revision != expected_review_revision
        or candidate.content_digest != expected_content_digest
    ):
        raise KnowledgeRevisionConflictError("候选审核范围已变化，请刷新后重新确认")
    # 专用撤回边界不扩展通用发布/失效状态转换；退役版本不能再次送审或发布。
    candidate.status = KnowledgeVersionStatus.RETIRED
    candidate.review_revision += 1
    document.updated_at = utc_now()
    await session.flush()
    await append_knowledge_audit(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        resource_type=AuditResourceType.KNOWLEDGE_VERSION,
        action=AuditAction.KNOWLEDGE_RETIRED,
        correlation_id=correlation_id,
        version_id=candidate.id,
        metadata={
            "operation": "candidate_withdrawn",
            "reason_code": reason_code,
            "document_id": str(document.id),
            "review_revision": candidate.review_revision,
            "content_digest": candidate.content_digest,
        },
    )
    return candidate


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
    document = await lock_knowledge_document(
        session, document_id=version.document_id, tenant_id=tenant_id
    )
    await session.refresh(version)
    _transition(version, KnowledgeVersionStatus.RETIRED)
    version.status = KnowledgeVersionStatus.RETIRED
    if version.published_at is not None:
        version.effective_until = min(version.effective_until or utc_now(), utc_now())
    if document.current_version_id == version.id:
        document.current_version_id = None
        document.status = KnowledgeDocumentStatus.EXPIRED
    await append_knowledge_index_event(
        session, document=document, event_type="knowledge.retired", version_id=version.id
    )
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
    # 授权判定只需有效期，不在ACL检查之前加载正文。
    version = (
        await session.execute(
            select(KnowledgeVersion.effective_from, KnowledgeVersion.effective_until).where(
                KnowledgeVersion.id == document.current_version_id,
                KnowledgeVersion.tenant_id == tenant_id,
                KnowledgeVersion.document_id == document.id,
                KnowledgeVersion.status == KnowledgeVersionStatus.PUBLISHED,
            )
        )
    ).one_or_none()
    if version is None:
        return False
    now = datetime.now(UTC)
    effective_from = version.effective_from
    effective_until = version.effective_until
    if effective_from.tzinfo is None:
        effective_from = effective_from.replace(tzinfo=UTC)
    if effective_until is not None and effective_until.tzinfo is None:
        effective_until = effective_until.replace(tzinfo=UTC)
    if effective_from > now or (effective_until is not None and effective_until <= now):
        return False
    return await knowledge_acl_allows_identity(
        session, document_id=document.id, tenant_id=tenant_id, identity=identity
    )


async def knowledge_acl_allows_identity(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    identity: IdentityContext,
) -> bool:
    """仅判定文档受众；治理草稿可用，不能替代业务读取的发布/有效期检查。"""
    if identity.principal.tenant_id != tenant_id or identity.user.tenant_id != tenant_id:
        return False
    document = await get_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
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
    role_ids = {
        entry.subject_id for entry in entries if entry.subject_type == KnowledgeAclSubjectType.ROLE
    }
    if role_ids.intersection(identity.principal.roles):
        return True
    user_id = str(identity.user.id)
    if any(
        entry.subject_type == KnowledgeAclSubjectType.USER and entry.subject_id == user_id
        for entry in entries
    ):
        return True
    org_id = str(identity.user.organization_unit_id) if identity.user.organization_unit_id else None
    return org_id is not None and any(
        entry.subject_type == KnowledgeAclSubjectType.ORGANIZATION_UNIT
        and entry.subject_id == org_id
        for entry in entries
    )


async def get_published_knowledge_content(
    session: AsyncSession,
    *,
    document_id: UUID,
    tenant_id: UUID,
    identity: IdentityContext,
) -> KnowledgeVersion:
    if identity.principal.tenant_id != tenant_id:
        raise KnowledgeAccessDeniedError("无权读取该知识正文")
    document = await lock_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    if not await knowledge_access_allowed(
        session, document_id=document_id, tenant_id=tenant_id, identity=identity
    ):
        raise KnowledgeAccessDeniedError("无权读取该知识正文")
    version = await session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.id == document.current_version_id,
            KnowledgeVersion.document_id == document.id,
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
    if identity.principal.tenant_id != tenant_id:
        raise KnowledgeAccessDeniedError("无权读取该知识快照")
    document = await lock_knowledge_document(session, document_id=document_id, tenant_id=tenant_id)
    if not await knowledge_access_allowed(
        session, document_id=document_id, tenant_id=tenant_id, identity=identity
    ):
        raise KnowledgeAccessDeniedError("无权读取该知识快照")
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
