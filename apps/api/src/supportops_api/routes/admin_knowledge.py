"""知识来源、文档版本和发布流程管理 API。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.enums import KnowledgeDocumentStatus
from supportops_core.knowledge_services import (
    archive_knowledge_document,
    count_knowledge_audit_events,
    count_knowledge_documents,
    create_knowledge_document,
    create_knowledge_source,
    create_knowledge_version,
    get_effective_knowledge_snapshot,
    get_knowledge_document,
    import_mock_knowledge_documents,
    list_knowledge_audit_events,
    list_knowledge_documents,
    publish_knowledge_version,
    replace_knowledge_acl,
    retire_knowledge_version,
    rollback_knowledge_version,
    submit_knowledge_version_for_review,
    update_knowledge_document,
)
from supportops_core.models import KnowledgeSource, KnowledgeVersion, User

from supportops_api.dependencies import current_identity, database_session, settings_from
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    KnowledgeAclReplace,
    KnowledgeAuditEventListResponse,
    KnowledgeAuditEventResponse,
    KnowledgeDocumentCreate,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
    KnowledgeDocumentUpdate,
    KnowledgeMockImportRequest,
    KnowledgeMockImportResponse,
    KnowledgeSnapshotResponse,
    KnowledgeSourceCreate,
    KnowledgeSourceListResponse,
    KnowledgeSourceResponse,
    KnowledgeVersionCreate,
    KnowledgeVersionResponse,
)

router = APIRouter(prefix="/v1/admin/knowledge", tags=["admin-knowledge"])


async def platform_admin_identity(
    identity: IdentityContext = Depends(current_identity),
) -> IdentityContext:
    require_platform_admin(identity)
    return identity


async def _user_name_map(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_ids: set[UUID],
) -> dict[UUID, str]:
    if not user_ids:
        return {}
    rows = await session.execute(
        select(User.id, User.display_name).where(
            User.tenant_id == tenant_id,
            User.id.in_(user_ids),
        )
    )
    return {user_id: display_name for user_id, display_name in rows}


async def _document_response(
    session: AsyncSession,
    document,
    *,
    tenant_id: UUID,
) -> KnowledgeDocumentResponse:
    names = await _user_name_map(
        session,
        tenant_id=tenant_id,
        user_ids={document.owner_user_id, document.created_by},
    )
    return KnowledgeDocumentResponse.model_validate(document).model_copy(
        update={
            "owner_user_name": names.get(document.owner_user_id),
            "created_by_name": names.get(document.created_by),
        }
    )


async def _version_response(
    session: AsyncSession,
    version,
    *,
    tenant_id: UUID,
) -> KnowledgeVersionResponse:
    user_ids = {version.created_by}
    if version.reviewed_by:
        user_ids.add(version.reviewed_by)
    if version.published_by:
        user_ids.add(version.published_by)
    names = await _user_name_map(session, tenant_id=tenant_id, user_ids=user_ids)
    return KnowledgeVersionResponse.model_validate(version).model_copy(
        update={
            "created_by_name": names.get(version.created_by),
            "reviewed_by_name": names.get(version.reviewed_by) if version.reviewed_by else None,
            "published_by_name": names.get(version.published_by) if version.published_by else None,
        }
    )


@router.post("/sources", response_model=KnowledgeSourceResponse, status_code=status.HTTP_201_CREATED)
async def add_knowledge_source(
    payload: KnowledgeSourceCreate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeSourceResponse:
    async with session.begin():
        source = await create_knowledge_source(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            external_key=payload.external_key,
            name=payload.name,
            source_type=payload.source_type,
            environment=payload.environment,
            owner_user_id=payload.owner_user_id,
            correlation_id=request.state.correlation_id,
        )
    names = await _user_name_map(
        session,
        tenant_id=identity.principal.tenant_id,
        user_ids={source.owner_user_id, source.created_by},
    )
    return KnowledgeSourceResponse.model_validate(source).model_copy(
        update={
            "owner_user_name": names.get(source.owner_user_id),
            "created_by_name": names.get(source.created_by),
        }
    )


@router.get("/sources", response_model=KnowledgeSourceListResponse)
async def read_knowledge_sources(
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeSourceListResponse:
    sources = await session.scalars(
        select(KnowledgeSource)
        .where(KnowledgeSource.tenant_id == identity.principal.tenant_id)
        .order_by(KnowledgeSource.name)
    )
    source_items = list(sources)
    names = await _user_name_map(
        session,
        tenant_id=identity.principal.tenant_id,
        user_ids={
            user_id
            for source in source_items
            for user_id in (source.owner_user_id, source.created_by)
        },
    )
    return KnowledgeSourceListResponse(
        items=[
            KnowledgeSourceResponse.model_validate(source).model_copy(
                update={
                    "owner_user_name": names.get(source.owner_user_id),
                    "created_by_name": names.get(source.created_by),
                }
            )
            for source in source_items
        ]
    )


@router.post("/documents", response_model=KnowledgeDocumentResponse, status_code=status.HTTP_201_CREATED)
async def add_knowledge_document(
    payload: KnowledgeDocumentCreate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeDocumentResponse:
    async with session.begin():
        document = await create_knowledge_document(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            source_id=payload.source_id,
            external_key=payload.external_key,
            owner_user_id=payload.owner_user_id,
            title=payload.title,
            content_markdown=payload.content_markdown,
            change_summary=payload.change_summary,
            effective_from=payload.effective_from,
            effective_until=payload.effective_until,
            correlation_id=request.state.correlation_id,
        )
    return await _document_response(
        session, document, tenant_id=identity.principal.tenant_id
    )


@router.get("/documents", response_model=KnowledgeDocumentListResponse)
async def read_knowledge_documents(
    status_filter: KnowledgeDocumentStatus | None = Query(default=None, alias="status"),
    source_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    owner_name: str | None = Query(default=None, alias="owner_name", max_length=200),
    keywords: str | None = Query(default=None, max_length=200),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeDocumentListResponse:
    documents = await list_knowledge_documents(
        session,
        tenant_id=identity.principal.tenant_id,
        status=status_filter,
        source_id=source_id,
        owner_user_id=owner_user_id,
        owner_name=owner_name,
        keywords=keywords,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_knowledge_documents(
        session,
        tenant_id=identity.principal.tenant_id,
        status=status_filter,
        source_id=source_id,
        owner_user_id=owner_user_id,
        owner_name=owner_name,
        keywords=keywords,
    )
    metadata = pagination_metadata(total, pagination)
    document_items = list(documents)
    names = await _user_name_map(
        session,
        tenant_id=identity.principal.tenant_id,
        user_ids={
            user_id
            for document in document_items
            for user_id in (document.owner_user_id, document.created_by)
        },
    )
    return KnowledgeDocumentListResponse(
        items=[
            KnowledgeDocumentResponse.model_validate(item).model_copy(
                update={
                    "owner_user_name": names.get(item.owner_user_id),
                    "created_by_name": names.get(item.created_by),
                }
            )
            for item in document_items
        ],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.post("/mock-import", response_model=KnowledgeMockImportResponse)
async def import_mock_documents(
    payload: KnowledgeMockImportRequest,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeMockImportResponse:
    async with session.begin():
        created, skipped = await import_mock_knowledge_documents(
            session,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            source_external_key=payload.source_external_key,
            source_name=payload.source_name,
            items=[item.model_dump() for item in payload.items],
            correlation_id=request.state.correlation_id,
        )
    return KnowledgeMockImportResponse(created=created, skipped=skipped)


@router.get("/audit-events", response_model=KnowledgeAuditEventListResponse)
async def read_knowledge_audit_events(
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeAuditEventListResponse:
    events = await list_knowledge_audit_events(
        session,
        tenant_id=identity.principal.tenant_id,
        page_size=pagination.page_size,
        offset=pagination.offset,
    )
    total = await count_knowledge_audit_events(session, tenant_id=identity.principal.tenant_id)
    metadata = pagination_metadata(total, pagination)
    return KnowledgeAuditEventListResponse(
        items=[
            KnowledgeAuditEventResponse(
                id=event.id,
                resource_type=event.resource_type.value,
                action=event.action.value,
                actor_user_id=event.actor_user_id,
                version_id=event.version_id,
                metadata=event.metadata_payload,
                correlation_id=event.correlation_id,
                created_at=event.created_at,
            )
            for event in events
        ],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.get("/documents/{document_id}/snapshot", response_model=KnowledgeSnapshotResponse)
async def read_knowledge_snapshot(
    document_id: UUID,
    identity: IdentityContext = Depends(current_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeSnapshotResponse:
    snapshot = await get_effective_knowledge_snapshot(
        session,
        document_id=document_id,
        tenant_id=identity.principal.tenant_id,
        identity=identity,
    )
    return KnowledgeSnapshotResponse.model_validate(snapshot)


@router.get("/documents/{document_id}", response_model=KnowledgeDocumentResponse)
async def read_knowledge_document(
    document_id: UUID,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeDocumentResponse:
    document = await get_knowledge_document(
        session, document_id=document_id, tenant_id=identity.principal.tenant_id
    )
    return await _document_response(
        session, document, tenant_id=identity.principal.tenant_id
    )


@router.patch("/documents/{document_id}", response_model=KnowledgeDocumentResponse)
async def edit_knowledge_document(
    document_id: UUID,
    payload: KnowledgeDocumentUpdate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeDocumentResponse:
    async with session.begin():
        document = await update_knowledge_document(
            session,
            document_id=document_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            owner_user_id=payload.owner_user_id,
            review_due_at=payload.review_due_at,
            correlation_id=request.state.correlation_id,
        )
    return await _document_response(
        session, document, tenant_id=identity.principal.tenant_id
    )


@router.post("/documents/{document_id}/versions", response_model=KnowledgeVersionResponse, status_code=status.HTTP_201_CREATED)
async def add_knowledge_version(
    document_id: UUID,
    payload: KnowledgeVersionCreate,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeVersionResponse:
    async with session.begin():
        version = await create_knowledge_version(
            session,
            document_id=document_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            title=payload.title,
            content_markdown=payload.content_markdown,
            expected_revision=payload.expected_revision,
            change_summary=payload.change_summary,
            effective_from=payload.effective_from,
            effective_until=payload.effective_until,
            correlation_id=request.state.correlation_id,
        )
    return await _version_response(
        session, version, tenant_id=identity.principal.tenant_id
    )


@router.get("/documents/{document_id}/versions", response_model=list[KnowledgeVersionResponse])
async def read_knowledge_versions(
    document_id: UUID,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> list[KnowledgeVersionResponse]:
    await get_knowledge_document(session, document_id=document_id, tenant_id=identity.principal.tenant_id)
    versions = await session.scalars(
        select(KnowledgeVersion)
        .where(
            KnowledgeVersion.document_id == document_id,
            KnowledgeVersion.tenant_id == identity.principal.tenant_id,
        )
        .order_by(KnowledgeVersion.version_number.desc())
    )
    return [
        await _version_response(session, version, tenant_id=identity.principal.tenant_id)
        for version in versions
    ]


@router.post("/versions/{version_id}/submit-review", response_model=KnowledgeVersionResponse)
async def submit_review(
    version_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeVersionResponse:
    async with session.begin():
        version = await submit_knowledge_version_for_review(
            session,
            version_id=version_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            correlation_id=request.state.correlation_id,
        )
    return await _version_response(
        session, version, tenant_id=identity.principal.tenant_id
    )


@router.post("/versions/{version_id}/publish", response_model=KnowledgeVersionResponse)
async def publish_version(
    version_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeVersionResponse:
    async with session.begin():
        await publish_knowledge_version(
            session,
            version_id=version_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            runtime_environment=settings_from(request).environment,
            correlation_id=request.state.correlation_id,
        )
        version = await session.get(KnowledgeVersion, version_id)
    return await _version_response(
        session, version, tenant_id=identity.principal.tenant_id
    )


@router.post("/versions/{version_id}/retire", response_model=KnowledgeVersionResponse)
async def retire_version(
    version_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeVersionResponse:
    async with session.begin():
        version = await retire_knowledge_version(
            session,
            version_id=version_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            correlation_id=request.state.correlation_id,
        )
    return await _version_response(
        session, version, tenant_id=identity.principal.tenant_id
    )


@router.post("/documents/{document_id}/rollback/{source_version_id}", response_model=KnowledgeVersionResponse)
async def rollback_version(
    document_id: UUID,
    source_version_id: UUID,
    request: Request,
    expected_revision: int = Query(ge=1),
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeVersionResponse:
    async with session.begin():
        version = await rollback_knowledge_version(
            session,
            document_id=document_id,
            source_version_id=source_version_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            expected_revision=expected_revision,
            correlation_id=request.state.correlation_id,
        )
    return await _version_response(
        session, version, tenant_id=identity.principal.tenant_id
    )


@router.put("/documents/{document_id}/acl")
async def change_knowledge_acl(
    document_id: UUID,
    payload: KnowledgeAclReplace,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> list[dict[str, str]]:
    async with session.begin():
        entries = await replace_knowledge_acl(
            session,
            document_id=document_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            entries=[(entry.subject_type, entry.subject_id) for entry in payload.entries],
            correlation_id=request.state.correlation_id,
        )
    return [{"subject_type": entry.subject_type.value, "subject_id": entry.subject_id} for entry in entries]


@router.delete("/documents/{document_id}", response_model=KnowledgeDocumentResponse)
async def archive_document(
    document_id: UUID,
    request: Request,
    identity: IdentityContext = Depends(platform_admin_identity),
    session: AsyncSession = Depends(database_session),
) -> KnowledgeDocumentResponse:
    async with session.begin():
        document = await archive_knowledge_document(
            session,
            document_id=document_id,
            tenant_id=identity.principal.tenant_id,
            actor_user_id=identity.user.id,
            correlation_id=request.state.correlation_id,
        )
    return await _document_response(
        session, document, tenant_id=identity.principal.tenant_id
    )
