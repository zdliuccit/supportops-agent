"""真实 PostgreSQL 下知识发布竞争、原子回滚及重复通知收敛。"""

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from sqlalchemy import event, select, text
from supportops_core import knowledge_services
from supportops_core.db import create_session_factory
from supportops_core.enums import AuditAction, KnowledgeVersionStatus
from supportops_core.knowledge_index_services import consume_knowledge_events
from supportops_core.knowledge_services import (
    KnowledgeConflictError,
    create_knowledge_version,
    decide_knowledge_review,
    lock_knowledge_document,
    publish_knowledge_version,
    retire_knowledge_version,
    submit_knowledge_version_for_review,
)
from supportops_core.models import (
    AgentAuditEvent,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndexGeneration,
    KnowledgeOutboxEvent,
    KnowledgeSnapshot,
    KnowledgeVersion,
)

from tests.api.test_knowledge_access import KnowledgeCase
from tests.api.test_knowledge_access import knowledge_case as knowledge_case
from tests.postgres.test_knowledge_access import engine as engine
from tests.postgres.test_knowledge_access import settings as settings

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)


@dataclass(frozen=True)
class Candidate:
    id: UUID
    revision: int


async def approve(case: KnowledgeCase, version_id: UUID) -> Candidate:
    reviewer_id = uuid5(NAMESPACE_URL, f"{case.tenant_id}:knowledge-reviewer")
    async with create_session_factory(case.engine)() as session, session.begin():
        version = await session.get(KnowledgeVersion, version_id)
        await submit_knowledge_version_for_review(
            session,
            version_id=version_id,
            tenant_id=case.tenant_id,
            actor_user_id=case.owner_id,
            expected_review_revision=version.review_revision,
            correlation_id="lifecycle-submit",
        )
        await decide_knowledge_review(
            session,
            version_id=version_id,
            tenant_id=case.tenant_id,
            actor_user_id=reviewer_id,
            decision="approved",
            expected_review_revision=version.review_revision,
            expected_content_digest=version.content_digest,
            comment="",
            correlation_id="lifecycle-review",
        )
        return Candidate(version.id, version.review_revision)


async def candidate(case: KnowledgeCase, number: int = 2) -> Candidate:
    async with create_session_factory(case.engine)() as session, session.begin():
        version = await create_knowledge_version(
            session,
            document_id=case.document_id,
            tenant_id=case.tenant_id,
            actor_user_id=case.owner_id,
            title=f"候选{number}",
            content_markdown=f"# 候选{number}\n429 release {number}",
            expected_revision=number - 1,
            correlation_id="lifecycle-draft",
        )
    return await approve(case, version.id)


async def publish(session, case: KnowledgeCase, target: Candidate) -> UUID:
    snapshot = await publish_knowledge_version(
        session,
        version_id=target.id,
        tenant_id=case.tenant_id,
        actor_user_id=case.owner_id,
        expected_review_revision=target.revision,
        runtime_environment="test",
        correlation_id="lifecycle-publish",
    )
    return snapshot.id


async def facts(case: KnowledgeCase):
    async with create_session_factory(case.engine)() as session:
        document = await session.get(KnowledgeDocument, case.document_id)
        versions = (
            await session.execute(
                select(
                    KnowledgeVersion.id,
                    KnowledgeVersion.status,
                    KnowledgeVersion.published_at,
                    KnowledgeVersion.published_by,
                    KnowledgeVersion.review_revision,
                )
                .where(KnowledgeVersion.document_id == case.document_id)
                .order_by(KnowledgeVersion.id)
            )
        ).all()
        snapshots = (
            await session.execute(
                select(KnowledgeSnapshot.id, KnowledgeSnapshot.version_id)
                .where(KnowledgeSnapshot.document_id == case.document_id)
                .order_by(KnowledgeSnapshot.id)
            )
        ).all()
        outbox = (
            await session.execute(
                select(
                    KnowledgeOutboxEvent.id,
                    KnowledgeOutboxEvent.event_type,
                    KnowledgeOutboxEvent.version_id,
                    KnowledgeOutboxEvent.event_key,
                    KnowledgeOutboxEvent.payload,
                )
                .where(KnowledgeOutboxEvent.document_id == case.document_id)
                .order_by(KnowledgeOutboxEvent.id)
            )
        ).all()
        audits = (
            await session.execute(
                select(
                    AgentAuditEvent.id, AgentAuditEvent.version_id, AgentAuditEvent.metadata_payload
                )
                .where(
                    AgentAuditEvent.tenant_id == case.tenant_id,
                    AgentAuditEvent.action == AuditAction.KNOWLEDGE_PUBLISHED,
                )
                .order_by(AgentAuditEvent.id)
            )
        ).all()
        return (document.current_version_id, document.status, versions, snapshots, outbox, audits)


async def race_publications(case: KnowledgeCase, targets: list[Candidate]):
    factory = create_session_factory(case.engine)
    pids: asyncio.Queue[int] = asyncio.Queue()

    async def contender(target):
        try:
            async with factory() as session, session.begin():
                # 在锁竞争前缓存旧文档，验证生命周期锁会刷新当前发布指针。
                await session.get(KnowledgeDocument, case.document_id)
                pid = await session.scalar(text("SELECT pg_backend_pid()"))
                await pids.put(pid)
                return await publish(session, case, target)
        except KnowledgeConflictError as exc:
            return exc

    async with factory() as guard:
        await guard.begin()
        await lock_knowledge_document(guard, document_id=case.document_id, tenant_id=case.tenant_id)
        tasks = [asyncio.create_task(contender(target)) for target in targets]
        try:
            async with asyncio.timeout(5):
                backend_pids = [await pids.get() for _ in targets]
                assert len(set(backend_pids)) == len(targets)
                async with factory() as observer:
                    while True:
                        blocked = [
                            await observer.scalar(
                                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
                            )
                            for pid in backend_pids
                        ]
                        if all(blocked):
                            break
                        await asyncio.sleep(0.01)
            await guard.commit()
            async with asyncio.timeout(10):
                return await asyncio.gather(*tasks)
        finally:
            await guard.rollback()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("same_version", [False, True])
async def test_concurrent_publication_has_one_winner_and_one_current_version(
    knowledge_case: KnowledgeCase, same_version: bool
):
    case = knowledge_case
    first = await candidate(case)
    second = first if same_version else await candidate(case, 3)
    results = await race_publications(case, [first, second])
    assert sum(isinstance(result, UUID) for result in results) == 1
    assert sum(isinstance(result, KnowledgeConflictError) for result in results) == 1
    current, _, versions, snapshots, outbox, audits = await facts(case)
    assert current in {first.id, second.id}
    assert [row.id for row in versions if row.status == KnowledgeVersionStatus.PUBLISHED] == [
        current
    ]
    assert len(snapshots) == len(audits) == 2
    assert sum(row.event_type == "knowledge.published" for row in outbox) == 2
    assert sum(row.event_type == "knowledge.superseded" for row in outbox) == 1


async def test_old_base_approval_requires_explicit_review_again(knowledge_case: KnowledgeCase):
    case = knowledge_case
    first, second = await candidate(case), await candidate(case, 3)
    factory = create_session_factory(case.engine)
    async with factory() as session, session.begin():
        await publish(session, case, first)
    baseline = await facts(case)
    with pytest.raises(KnowledgeConflictError):
        async with factory() as session, session.begin():
            await publish(session, case, second)
    assert await facts(case) == baseline
    second = await approve(case, second.id)
    async with factory() as session, session.begin():
        await publish(session, case, second)
    assert (await facts(case))[0] == second.id


async def test_retire_to_empty_pointer_does_not_revive_old_approval(knowledge_case: KnowledgeCase):
    case = knowledge_case
    factory = create_session_factory(case.engine)
    async with factory() as session, session.begin():
        await retire_knowledge_version(
            session,
            version_id=case.version_id,
            tenant_id=case.tenant_id,
            actor_user_id=case.owner_id,
            correlation_id="retire-initial",
        )
    first, second = await candidate(case), await candidate(case, 3)
    async with factory() as session, session.begin():
        await publish(session, case, first)
    async with factory() as session, session.begin():
        await retire_knowledge_version(
            session,
            version_id=first.id,
            tenant_id=case.tenant_id,
            actor_user_id=case.owner_id,
            correlation_id="retire-first",
        )
    baseline = await facts(case)
    assert baseline[0] is None
    with pytest.raises(KnowledgeConflictError):
        async with factory() as session, session.begin():
            await publish(session, case, second)
    assert await facts(case) == baseline


async def test_legacy_approval_is_not_silently_upgraded(
    knowledge_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch
):
    case = knowledge_case

    async def legacy_scope(session, version):
        def timestamp(value):
            return (
                (
                    value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
                ).isoformat()
                if value
                else None
            )

        payload = {
            "content_digest": version.content_digest,
            "title": version.title,
            "document_id": str(version.document_id),
            "effective_from": timestamp(version.effective_from),
            "effective_until": timestamp(version.effective_until),
            "acl_digest": await knowledge_services._acl_digest(
                session, document_id=version.document_id, tenant_id=version.tenant_id
            ),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    with monkeypatch.context() as legacy:
        legacy.setattr(knowledge_services, "_review_scope_digest", legacy_scope)
        target = await candidate(case)
    baseline = await facts(case)
    with pytest.raises(KnowledgeConflictError):
        async with create_session_factory(case.engine)() as session, session.begin():
            await publish(session, case, target)
    assert await facts(case) == baseline
    target = await approve(case, target.id)
    async with create_session_factory(case.engine)() as session, session.begin():
        await publish(session, case, target)
    assert (await facts(case))[0] == target.id


class InjectedPublicationFailure(RuntimeError):
    pass


@pytest.mark.parametrize("boundary", ["snapshot", "outbox", "audit", "commit"])
async def test_publication_rollback_preserves_original_and_can_retry(
    knowledge_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch, boundary: str
):
    case = knowledge_case
    target = await candidate(case)
    baseline = await facts(case)
    async with create_session_factory(case.engine)() as session:
        original_flush = session.flush

        async def flush(*args, **kwargs):
            hit = any(
                (boundary == "snapshot" and isinstance(row, KnowledgeSnapshot))
                or (
                    boundary == "outbox"
                    and isinstance(row, KnowledgeOutboxEvent)
                    and row.event_type == "knowledge.published"
                )
                or (
                    boundary == "audit"
                    and isinstance(row, AgentAuditEvent)
                    and row.action == AuditAction.KNOWLEDGE_PUBLISHED
                )
                for row in session.new
            )
            await original_flush(*args, **kwargs)
            if hit:
                raise InjectedPublicationFailure(boundary)

        def fail_commit(sync_session):
            raise InjectedPublicationFailure("commit")

        if boundary == "commit":
            event.listen(session.sync_session, "before_commit", fail_commit)
        else:
            monkeypatch.setattr(session, "flush", flush)
        with pytest.raises(InjectedPublicationFailure):
            async with session.begin():
                await publish(session, case, target)
    assert await facts(case) == baseline
    async with create_session_factory(case.engine)() as session, session.begin():
        await publish(session, case, target)
    current, _, _, snapshots, _, audits = await facts(case)
    assert current == target.id and len(snapshots) == len(audits) == 2


async def test_cancel_after_snapshot_flush_rolls_back_entire_publication(
    knowledge_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch
):
    case = knowledge_case
    target = await candidate(case)
    baseline = await facts(case)
    flushed = asyncio.Event()

    async def work():
        async with create_session_factory(case.engine)() as session, session.begin():
            original_flush = session.flush

            async def flush(*args, **kwargs):
                snapshot_pending = any(isinstance(row, KnowledgeSnapshot) for row in session.new)
                await original_flush(*args, **kwargs)
                if snapshot_pending:
                    flushed.set()
                    await asyncio.Event().wait()

            monkeypatch.setattr(session, "flush", flush)
            await publish(session, case, target)

    task = asyncio.create_task(work())
    try:
        async with asyncio.timeout(5):
            await flushed.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await facts(case) == baseline
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_lost_post_commit_response_does_not_duplicate_product_facts(
    knowledge_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch
):
    from supportops_api.routes import admin_knowledge

    case = knowledge_case
    target = await candidate(case)

    async def lose_response(*args, **kwargs):
        raise ConnectionError("isolated response loss after commit")

    with monkeypatch.context() as scoped:
        scoped.setattr(admin_knowledge, "_version_response", lose_response)
        with pytest.raises(ConnectionError):
            await case.client.post(
                f"/v1/admin/knowledge/versions/{target.id}/publish",
                headers=case.owner_headers,
                json={"expected_review_revision": target.revision},
            )
    committed = await facts(case)
    assert committed[0] == target.id
    repeated = await case.client.post(
        f"/v1/admin/knowledge/versions/{target.id}/publish",
        headers=case.owner_headers,
        json={"expected_review_revision": target.revision},
    )
    assert repeated.status_code == 409
    assert await facts(case) == committed


async def index_facts(case: KnowledgeCase):
    async with create_session_factory(case.engine)() as session:
        generations = (
            await session.execute(
                select(
                    KnowledgeIndexGeneration.id,
                    KnowledgeIndexGeneration.snapshot_id,
                    KnowledgeIndexGeneration.status,
                )
                .where(KnowledgeIndexGeneration.document_id == case.document_id)
                .order_by(KnowledgeIndexGeneration.id)
            )
        ).all()
        chunks = (
            await session.execute(
                select(KnowledgeChunk.id, KnowledgeChunk.content_digest)
                .join(
                    KnowledgeIndexGeneration,
                    KnowledgeIndexGeneration.id == KnowledgeChunk.generation_id,
                )
                .where(KnowledgeIndexGeneration.document_id == case.document_id)
                .order_by(KnowledgeChunk.id)
            )
        ).all()
        return generations, chunks


async def drain(case: KnowledgeCase):
    factory = create_session_factory(case.engine)
    for _ in range(30):
        await consume_knowledge_events(factory, settings=case.settings)
        async with factory() as session:
            pending = await session.scalar(
                select(KnowledgeOutboxEvent.id)
                .where(
                    KnowledgeOutboxEvent.document_id == case.document_id,
                    KnowledgeOutboxEvent.processed_at.is_(None),
                    KnowledgeOutboxEvent.dead_at.is_(None),
                )
                .limit(1)
            )
        if pending is None:
            return
    raise AssertionError("本例Outbox未在有界轮次内处理完毕")


async def test_duplicate_and_concurrent_consumers_keep_one_index_and_original_events(
    knowledge_case: KnowledgeCase,
):
    case = knowledge_case
    baseline = await facts(case)
    factory = create_session_factory(case.engine)
    await asyncio.gather(
        *(consume_knowledge_events(factory, settings=case.settings) for _ in range(2))
    )
    await drain(case)
    indexed = await index_facts(case)
    assert len(indexed[0]) == 1 and indexed[0][0].status == "ready" and indexed[1]
    # 重投同一事件ID模拟投递回执丢失，保留原事件内容与唯一键。
    async with factory() as session, session.begin():
        rows = (
            await session.scalars(
                select(KnowledgeOutboxEvent).where(
                    KnowledgeOutboxEvent.document_id == case.document_id
                )
            )
        ).all()
        for row in rows:
            row.processed_at = None
    await drain(case)
    assert await index_facts(case) == indexed
    assert await facts(case) == baseline


async def test_old_publish_event_after_new_version_does_not_restore_old_index(
    knowledge_case: KnowledgeCase,
):
    case = knowledge_case
    await drain(case)
    old_index = await index_facts(case)
    target = await candidate(case)
    factory = create_session_factory(case.engine)
    async with factory() as session, session.begin():
        await publish(session, case, target)
    await drain(case)
    current_index = await index_facts(case)
    assert len(current_index[0]) == 2
    assert sum(row.status == "ready" for row in current_index[0]) == 1
    async with factory() as session, session.begin():
        old = await session.scalar(
            select(KnowledgeOutboxEvent).where(
                KnowledgeOutboxEvent.document_id == case.document_id,
                KnowledgeOutboxEvent.version_id == case.version_id,
                KnowledgeOutboxEvent.event_type == "knowledge.published",
            )
        )
        old.processed_at = None
    await drain(case)
    assert await index_facts(case) == current_index
    assert {row.id for row in old_index[1]} <= {row.id for row in current_index[1]}
