"""关键词索引元数据、有限重建和死信恢复，不返回知识正文。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.knowledge_services import (
    KnowledgeConflictError, append_knowledge_audit, append_knowledge_index_event, lock_knowledge_document,
)
from supportops_core.models import KnowledgeDocument, KnowledgeIndexGeneration, KnowledgeOutboxEvent
from supportops_core.services import ResourceNotFoundError

from supportops_api.dependencies import current_identity, database_session
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import PaginatedListResponse

router = APIRouter(prefix="/v1/admin/knowledge", tags=["admin-knowledge-index"])


class IndexGenerationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    snapshot_id: UUID
    generation_number: int
    algorithm_version: str
    status: str
    chunk_count: int
    error_code: str | None
    created_at: datetime
    ready_at: datetime | None


class IndexJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    version_id: UUID | None
    event_type: str
    attempts: int
    available_at: datetime
    processed_at: datetime | None
    dead_at: datetime | None
    error_code: str | None
    created_at: datetime
    retry_available: bool = False


class IndexJobsResponse(PaginatedListResponse):
    items: list[IndexJobResponse]


class KnowledgeIndexResponse(BaseModel):
    document_id: UUID
    strategy: str = "keyword_v1"
    vector_enabled: bool = False
    generation: IndexGenerationResponse | None
    pending_jobs: int
    dead_jobs: int


class IndexRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_attempts: int = Field(ge=1)


async def administrator(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@router.get("/documents/{document_id}/index", response_model=KnowledgeIndexResponse)
async def read_index(
    document_id: UUID, identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> KnowledgeIndexResponse:
    document = await session.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.id == document_id, KnowledgeDocument.tenant_id == identity.principal.tenant_id,
    ))
    if document is None:
        raise ResourceNotFoundError("知识文档不存在")
    generation = await session.scalar(select(KnowledgeIndexGeneration).where(
        KnowledgeIndexGeneration.document_id == document_id, KnowledgeIndexGeneration.tenant_id == identity.principal.tenant_id,
    ).order_by(KnowledgeIndexGeneration.generation_number.desc()).limit(1))
    filters = [KnowledgeOutboxEvent.document_id == document_id, KnowledgeOutboxEvent.tenant_id == identity.principal.tenant_id,
               KnowledgeOutboxEvent.processed_at.is_(None)]
    pending = int(await session.scalar(select(func.count()).select_from(KnowledgeOutboxEvent).where(
        *filters, KnowledgeOutboxEvent.dead_at.is_(None))) or 0)
    dead = int(await session.scalar(select(func.count()).select_from(KnowledgeOutboxEvent).where(
        *filters, KnowledgeOutboxEvent.dead_at.is_not(None))) or 0)
    return KnowledgeIndexResponse(document_id=document_id, generation=IndexGenerationResponse.model_validate(generation) if generation else None,
                                  pending_jobs=pending, dead_jobs=dead)


@router.get("/index-jobs", response_model=IndexJobsResponse)
async def read_index_jobs(
    document_id: UUID | None = None, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IndexJobsResponse:
    filters = [KnowledgeOutboxEvent.tenant_id == identity.principal.tenant_id]
    if document_id is not None:
        filters.append(KnowledgeOutboxEvent.document_id == document_id)
    total = int(await session.scalar(select(func.count()).select_from(KnowledgeOutboxEvent).where(*filters)) or 0)
    items = (await session.scalars(select(KnowledgeOutboxEvent).where(*filters)
        .order_by(KnowledgeOutboxEvent.created_at.desc(), KnowledgeOutboxEvent.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return IndexJobsResponse(items=[IndexJobResponse.model_validate(item).model_copy(update={
        "retry_available": item.dead_at is not None and not item.payload.get("replacement_event_id")
    }) for item in items],
        total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.post("/documents/{document_id}/index/rebuild", response_model=KnowledgeIndexResponse)
async def rebuild_index(
    document_id: UUID, identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> KnowledgeIndexResponse:
    async with session.begin():
        document = await lock_knowledge_document(session, document_id=document_id, tenant_id=identity.principal.tenant_id)
        if document.current_version_id is None:
            raise KnowledgeConflictError("没有当前发布版本，不能重建索引")
        pending = await session.scalar(select(KnowledgeOutboxEvent.id).where(
            KnowledgeOutboxEvent.document_id == document_id, KnowledgeOutboxEvent.tenant_id == identity.principal.tenant_id,
            KnowledgeOutboxEvent.event_type == "knowledge.rebuild", KnowledgeOutboxEvent.processed_at.is_(None),
            KnowledgeOutboxEvent.dead_at.is_(None),
        ).limit(1))
        if pending is None:
            await append_knowledge_index_event(session, document=document, event_type="knowledge.rebuild", version_id=document.current_version_id)
            await append_knowledge_audit(session, tenant_id=document.tenant_id, actor_user_id=identity.user.id,
                resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT, action=AuditAction.KNOWLEDGE_UPDATED,
                correlation_id=f"index:{uuid4()}", metadata={"document_id": str(document_id), "operation": "index.rebuild", "strategy": "keyword_v1"})
    return await read_index(document_id, identity, session)


@router.post("/index-jobs/{job_id}/retry", response_model=IndexJobResponse)
async def retry_index_job(
    job_id: UUID, payload: IndexRetryRequest,
    identity: IdentityContext = Depends(administrator), session: AsyncSession = Depends(database_session),
) -> IndexJobResponse:
    async with session.begin():
        scope = await session.scalar(select(KnowledgeOutboxEvent).where(
            KnowledgeOutboxEvent.id == job_id, KnowledgeOutboxEvent.tenant_id == identity.principal.tenant_id))
        if scope is None:
            raise ResourceNotFoundError("索引任务不存在")
        # 归档事件也可以恢复；不通过正文读取接口绕过归档状态。
        await session.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.id == scope.document_id, KnowledgeDocument.tenant_id == identity.principal.tenant_id,
        ).execution_options(include_deleted=True).with_for_update())
        event = await session.scalar(select(KnowledgeOutboxEvent).where(KnowledgeOutboxEvent.id == job_id)
                                     .execution_options(populate_existing=True).with_for_update())
        if event.dead_at is None or event.attempts != payload.expected_attempts or event.payload.get("replacement_event_id"):
            raise KnowledgeConflictError("任务状态已变化，刷新后重试")
        replacement = KnowledgeOutboxEvent(id=uuid4(), tenant_id=event.tenant_id, document_id=event.document_id,
            version_id=event.version_id, event_type=event.event_type, event_key=f"knowledge.retry:{event.id}",
            payload={"previous_event_id": str(event.id)}, attempts=0, available_at=datetime.now(UTC))
        session.add(replacement)
        event.payload = {**event.payload, "replacement_event_id": str(replacement.id)}
        await append_knowledge_audit(session, tenant_id=event.tenant_id, actor_user_id=identity.user.id,
            resource_type=AuditResourceType.KNOWLEDGE_DOCUMENT, action=AuditAction.KNOWLEDGE_UPDATED,
            correlation_id=f"index:{uuid4()}", metadata={"document_id": str(event.document_id), "operation": "index.retry",
                "previous_event_id": str(event.id), "replacement_event_id": str(replacement.id), "previous_attempts": event.attempts})
        await session.flush()
    return IndexJobResponse.model_validate(replacement)
