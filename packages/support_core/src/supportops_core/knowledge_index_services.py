"""确定性关键词分块和知识 Outbox 消费；没有 embedding/向量调用。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.enums import KnowledgeDocumentStatus, KnowledgeVersionStatus
from supportops_core.knowledge_content import knowledge_content_digest, normalize_knowledge_markdown
from supportops_core.knowledge_services import refresh_knowledge_document_status
from supportops_core.models import (
    KnowledgeChunk, KnowledgeDocument, KnowledgeIndexGeneration, KnowledgeOutboxEvent,
    KnowledgeSnapshot, KnowledgeVersion,
)

KEYWORD_ALGORITHM = "keyword_v1"
_HEADING = re.compile(r"^#{1,6}\s+.+?\s*$")
_EVENT_TYPES = frozenset({"knowledge.published", "knowledge.superseded", "knowledge.retired",
    "knowledge.archived", "knowledge.acl_changed", "knowledge.expired", "knowledge.rebuild"})


class KnowledgeIndexError(ValueError):
    """索引错误只持久化稳定代码，不泄露正文。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    section_id: str
    section_title: str
    start_offset: int
    end_offset: int
    content: str
    content_digest: str
    chunk_key: str


def chunk_knowledge(version: KnowledgeVersion, snapshot: KnowledgeSnapshot) -> list[ChunkDraft]:
    """按章节和 1600 字符上限切块，偏移可定位原文，重复构建得到同一键。"""
    content = normalize_knowledge_markdown(version.content_markdown)
    if knowledge_content_digest(content) != snapshot.content_digest or version.content_digest != snapshot.content_digest:
        raise KnowledgeIndexError("KNOWLEDGE_CONTENT_DIGEST_MISMATCH")
    sections: list[tuple[int, str, str]] = [(0, "document", version.title)]
    anchor_index, offset = 0, 0
    for line in content.splitlines(keepends=True):
        if _HEADING.match(line.rstrip("\n")):
            if anchor_index >= len(snapshot.section_anchors):
                raise KnowledgeIndexError("KNOWLEDGE_SECTION_ANCHOR_MISMATCH")
            anchor = snapshot.section_anchors[anchor_index]
            if len(anchor["id"]) > 500:
                raise KnowledgeIndexError("KNOWLEDGE_SECTION_ANCHOR_TOO_LONG")
            sections.append((offset, anchor["id"], anchor["title"]))
            anchor_index += 1
        offset += len(line)
    if anchor_index != len(snapshot.section_anchors):
        raise KnowledgeIndexError("KNOWLEDGE_SECTION_ANCHOR_MISMATCH")
    chunks: list[ChunkDraft] = []
    for index, (start, section_id, title) in enumerate(sections):
        stop = sections[index + 1][0] if index + 1 < len(sections) else len(content)
        while start < stop:
            end = min(start + 1600, stop)
            if end < stop:
                newline = content.rfind("\n", start + 800, end)
                if newline >= 0:
                    end = newline + 1
            body = content[start:end]
            if body.strip():
                digest = hashlib.sha256(body.encode()).hexdigest()
                key = hashlib.sha256(f"{snapshot.id}:{KEYWORD_ALGORITHM}:{start}:{end}:{digest}".encode()).hexdigest()
                chunks.append(ChunkDraft(len(chunks), section_id[:500], title[:500], start, end, body, digest, key))
            start = end
    if not chunks:
        raise KnowledgeIndexError("KNOWLEDGE_INDEX_EMPTY")
    return chunks


async def _reconcile_document(session: AsyncSession, document: KnowledgeDocument, *, force: bool) -> None:
    """调用方持文档锁；失效先处理，新索引在同事务构建成功后切换。"""
    now = datetime.now(UTC)
    version = await session.scalar(select(KnowledgeVersion).where(
        KnowledgeVersion.id == document.current_version_id, KnowledgeVersion.tenant_id == document.tenant_id,
        KnowledgeVersion.deleted_at.is_(None), KnowledgeVersion.status == KnowledgeVersionStatus.PUBLISHED,
    ))
    until = version.effective_until if version is not None else None
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    valid = (document.deleted_at is None and document.status != KnowledgeDocumentStatus.ARCHIVED
             and version is not None and (until is None or until > now))
    generations = list((await session.scalars(select(KnowledgeIndexGeneration).where(
        KnowledgeIndexGeneration.tenant_id == document.tenant_id,
        KnowledgeIndexGeneration.document_id == document.id,
        KnowledgeIndexGeneration.status == "ready",
    ))).all())
    snapshot = None
    if valid:
        snapshot = await session.scalar(select(KnowledgeSnapshot).where(
            KnowledgeSnapshot.document_id == document.id, KnowledgeSnapshot.tenant_id == document.tenant_id,
            KnowledgeSnapshot.version_id == version.id,
        ))
        if snapshot is None:
            raise KnowledgeIndexError("KNOWLEDGE_SNAPSHOT_MISSING")
    for generation in generations:
        if snapshot is None or generation.snapshot_id != snapshot.id:
            generation.status = "invalidated"
    if snapshot is None or (not force and any(g.snapshot_id == snapshot.id for g in generations)):
        return
    drafts = chunk_knowledge(version, snapshot)
    next_number = int(await session.scalar(select(func.max(KnowledgeIndexGeneration.generation_number)).where(
        KnowledgeIndexGeneration.document_id == document.id, KnowledgeIndexGeneration.tenant_id == document.tenant_id,
    )) or 0) + 1
    generation = KnowledgeIndexGeneration(tenant_id=document.tenant_id, document_id=document.id,
        snapshot_id=snapshot.id, generation_number=next_number, algorithm_version=KEYWORD_ALGORITHM,
        content_digest=snapshot.content_digest, status="pending", chunk_count=0)
    session.add(generation)
    await session.flush()
    session.add_all([KnowledgeChunk(tenant_id=document.tenant_id, generation_id=generation.id,
        ordinal=d.ordinal, section_id=d.section_id, section_title=d.section_title, start_offset=d.start_offset,
        end_offset=d.end_offset, content=d.content, content_digest=d.content_digest, chunk_key=d.chunk_key)
        for d in drafts])
    await session.flush()
    # 不修改旧分块，已保存的 evidence 可以继续定位原始资料并复查当前授权。
    for old in generations:
        old.status = "invalidated"
    generation.status, generation.chunk_count, generation.ready_at = "ready", len(drafts), now


async def consume_knowledge_events(session_factory: async_sessionmaker[AsyncSession], *, settings: Settings) -> int:
    """短事务消费，文档锁先于事件锁；重复/乱序事件按当前事实收敛。"""
    async with session_factory() as session:
        candidates = list((await session.execute(select(KnowledgeOutboxEvent.id,
            KnowledgeOutboxEvent.document_id, KnowledgeOutboxEvent.tenant_id).where(
            KnowledgeOutboxEvent.processed_at.is_(None), KnowledgeOutboxEvent.dead_at.is_(None),
            KnowledgeOutboxEvent.available_at <= datetime.now(UTC),
        ).order_by(KnowledgeOutboxEvent.available_at, KnowledgeOutboxEvent.created_at, KnowledgeOutboxEvent.id)
            .limit(settings.knowledge_index_batch_size))).all())
    processed = 0
    for event_id, document_id, tenant_id in candidates:
        async with session_factory() as session, session.begin():
            document = await session.scalar(select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id, KnowledgeDocument.tenant_id == tenant_id,
            ).execution_options(include_deleted=True, populate_existing=True).with_for_update(skip_locked=True))
            if document is None:
                continue
            event = await session.scalar(select(KnowledgeOutboxEvent).where(
                KnowledgeOutboxEvent.id == event_id, KnowledgeOutboxEvent.tenant_id == tenant_id,
                KnowledgeOutboxEvent.processed_at.is_(None), KnowledgeOutboxEvent.dead_at.is_(None),
                KnowledgeOutboxEvent.available_at <= datetime.now(UTC),
            ).with_for_update(skip_locked=True))
            if event is None:
                continue
            event.attempts += 1
            try:
                async with session.begin_nested():
                    if event.event_type not in _EVENT_TYPES:
                        raise KnowledgeIndexError("KNOWLEDGE_EVENT_UNSUPPORTED")
                    await _reconcile_document(session, document, force=event.event_type == "knowledge.rebuild")
            except Exception as exc:
                event.error_code = exc.code if isinstance(exc, KnowledgeIndexError) else "KNOWLEDGE_INDEX_BUILD_FAILED"
                if event.attempts >= settings.knowledge_index_max_attempts:
                    event.dead_at = datetime.now(UTC)
                else:
                    event.available_at = datetime.now(UTC) + timedelta(seconds=min(300, 2 ** event.attempts))
            else:
                event.processed_at, event.error_code = datetime.now(UTC), None
                processed += 1
    return processed


async def invalidate_expired_knowledge(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """没有用户访问也能失效索引；检索自身仍实时查有效期，维护延迟不能越权。"""
    now = datetime.now(UTC)
    async with session_factory() as session:
        expired = (await session.execute(select(KnowledgeDocument.id, KnowledgeDocument.tenant_id)
            .join(KnowledgeVersion, KnowledgeVersion.id == KnowledgeDocument.current_version_id).where(
                KnowledgeDocument.status != KnowledgeDocumentStatus.EXPIRED,
                KnowledgeVersion.status == KnowledgeVersionStatus.PUBLISHED, KnowledgeVersion.effective_until <= now,
            ).order_by(KnowledgeVersion.effective_until, KnowledgeDocument.id).limit(100))).all()
    for document_id, tenant_id in expired:
        async with session_factory() as session, session.begin():
            await refresh_knowledge_document_status(session, document_id=document_id, tenant_id=tenant_id)
    valid_snapshots = select(KnowledgeSnapshot.id).join(KnowledgeDocument,
        KnowledgeDocument.id == KnowledgeSnapshot.document_id).join(KnowledgeVersion,
        KnowledgeVersion.id == KnowledgeSnapshot.version_id).where(
        KnowledgeDocument.deleted_at.is_(None), KnowledgeDocument.status != KnowledgeDocumentStatus.ARCHIVED,
        KnowledgeDocument.current_version_id == KnowledgeVersion.id, KnowledgeVersion.status == KnowledgeVersionStatus.PUBLISHED,
        KnowledgeVersion.deleted_at.is_(None), KnowledgeSnapshot.deleted_at.is_(None),
        or_(KnowledgeVersion.effective_until.is_(None), KnowledgeVersion.effective_until > now),
    )
    async with session_factory() as session, session.begin():
        result = await session.execute(update(KnowledgeIndexGeneration).where(
            KnowledgeIndexGeneration.status == "ready", ~KnowledgeIndexGeneration.snapshot_id.in_(valid_snapshots),
        ).values(status="invalidated"))
        return result.rowcount
