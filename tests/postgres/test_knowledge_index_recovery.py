"""真实 PG 下关键词索引失败、死信、进程中断和当前授权失效。"""

import asyncio
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from supportops_core import knowledge_index_services as indexing
from supportops_core.auth import IdentityContext, Principal
from supportops_core.db import create_session_factory
from supportops_core.knowledge_retrieval import search_keyword_knowledge
from supportops_core.knowledge_services import (
    archive_knowledge_document,
    create_knowledge_version,
    retire_knowledge_version,
)
from supportops_core.models import (
    AgentRun,
    KnowledgeChunk,
    KnowledgeIndexGeneration,
    KnowledgeOutboxEvent,
    RunAttempt,
    ToolInvocation,
    User,
)
from supportops_core.run_attempt_services import RunAttemptIdentity

from tests.api.test_knowledge_access import BODY, KnowledgeCase, create_evidence
from tests.api.test_knowledge_access import knowledge_case as knowledge_case
from tests.conftest import auth_headers
from tests.postgres.test_knowledge_access import engine as engine
from tests.postgres.test_knowledge_access import settings as settings
from tests.postgres.test_knowledge_lifecycle import approve, candidate, drain, index_facts, publish

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)


@pytest.fixture
async def index_case(knowledge_case: KnowledgeCase):
    """保持失败历史，延后本例剩余通知，避免影响下一例的全局消费批次。"""
    try:
        yield knowledge_case
    finally:
        async with create_session_factory(knowledge_case.engine)() as session, session.begin():
            rows = await session.scalars(
                select(KnowledgeOutboxEvent).where(
                    KnowledgeOutboxEvent.document_id == knowledge_case.document_id,
                    KnowledgeOutboxEvent.processed_at.is_(None),
                )
            )
            for row in rows:
                row.available_at = datetime.now(UTC) + timedelta(days=1)


async def job(case: KnowledgeCase, job_id: UUID) -> KnowledgeOutboxEvent:
    async with create_session_factory(case.engine)() as session:
        result = await session.get(KnowledgeOutboxEvent, job_id)
        assert result is not None
        return result


async def make_due(case: KnowledgeCase, job_id: UUID) -> None:
    async with create_session_factory(case.engine)() as session, session.begin():
        rows = await session.scalars(
            select(KnowledgeOutboxEvent).where(
                KnowledgeOutboxEvent.document_id == case.document_id,
                KnowledgeOutboxEvent.processed_at.is_(None),
            )
        )
        for row in rows:
            row.available_at = datetime.now(UTC) + timedelta(
                seconds=-1 if row.id == job_id else 3600
            )


async def consume(case: KnowledgeCase, factory=None) -> int:
    return await indexing.consume_knowledge_events(
        factory or create_session_factory(case.engine),
        settings=case.settings.model_copy(
            update={"knowledge_index_batch_size": 100, "knowledge_index_max_attempts": 3}
        ),
    )


async def rebuild(case: KnowledgeCase) -> UUID:
    response = await case.client.post(
        f"/v1/admin/knowledge/documents/{case.document_id}/index/rebuild",
        headers=case.owner_headers,
    )
    assert response.status_code == 200
    async with create_session_factory(case.engine)() as session:
        result = await session.scalar(
            select(KnowledgeOutboxEvent.id).where(
                KnowledgeOutboxEvent.document_id == case.document_id,
                KnowledgeOutboxEvent.event_type == "knowledge.rebuild",
                KnowledgeOutboxEvent.processed_at.is_(None),
                KnowledgeOutboxEvent.dead_at.is_(None),
            )
        )
        assert result is not None
    await make_due(case, result)
    return result


@pytest.mark.parametrize("failure", ["unexpected", "digest"])
async def test_first_build_has_bounded_backoff_dead_letter_and_safe_status(
    index_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch, failure: str, capsys
):
    case = index_case
    async with create_session_factory(case.engine)() as session:
        target = await session.scalar(
            select(KnowledgeOutboxEvent.id).where(
                KnowledgeOutboxEvent.document_id == case.document_id,
                KnowledgeOutboxEvent.event_type == "knowledge.published",
            )
        )
    assert target is not None
    await make_due(case, target)
    original = indexing.chunk_knowledge

    def fail(version, snapshot):
        if snapshot.document_id == case.document_id:
            if failure == "digest":
                raise indexing.KnowledgeIndexError("KNOWLEDGE_CONTENT_DIGEST_MISMATCH")
            raise RuntimeError("private-body-and-test-secret-must-not-escape")
        return original(version, snapshot)

    monkeypatch.setattr(indexing, "chunk_knowledge", fail)
    expected = (
        "KNOWLEDGE_CONTENT_DIGEST_MISMATCH"
        if failure == "digest"
        else "KNOWLEDGE_INDEX_BUILD_FAILED"
    )
    for attempt in range(1, 4):
        before = datetime.now(UTC)
        await consume(case)
        current = await job(case, target)
        assert current.attempts == attempt and current.error_code == expected
        assert current.processed_at is None
        assert await index_facts(case) == ([], [])
        if attempt < 3:
            assert current.dead_at is None
            assert before + timedelta(seconds=2**attempt) <= current.available_at
            assert current.available_at <= datetime.now(UTC) + timedelta(seconds=2**attempt)
            # 未到期不能反复增加尝试次数。
            await consume(case)
            assert (await job(case, target)).attempts == attempt
            await make_due(case, target)
        else:
            assert current.dead_at is not None
    await make_due(case, target)
    await consume(case)
    assert (await job(case, target)).attempts == 3
    status = await case.client.get(
        f"/v1/admin/knowledge/documents/{case.document_id}/index", headers=case.owner_headers
    )
    assert status.status_code == 200
    assert status.json()["generation"] is None and status.json()["dead_jobs"] == 1
    jobs = await case.client.get(
        f"/v1/admin/knowledge/index-jobs?document_id={case.document_id}",
        headers=case.owner_headers,
    )
    item = next(item for item in jobs.json()["items"] if item["id"] == str(target))
    assert item["retry_available"] is True and item["error_code"] == expected
    assert "private-body" not in jobs.text + status.text + capsys.readouterr().err


async def test_dead_letter_retry_is_single_new_job_and_preserves_original(
    index_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch
):
    case = index_case
    await drain(case)
    target = await rebuild(case)
    original = indexing.chunk_knowledge

    def fail(version, snapshot):
        raise indexing.KnowledgeIndexError("KNOWLEDGE_INDEX_EMPTY")

    with monkeypatch.context() as patch:
        patch.setattr(indexing, "chunk_knowledge", fail)
        for _ in range(3):
            await make_due(case, target)
            await consume(case)
    assert indexing.chunk_knowledge is original
    dead = await job(case, target)
    path = f"/v1/admin/knowledge/index-jobs/{target}/retry"
    forbidden = await case.client.post(
        path, headers=case.reader_headers, json={"expected_attempts": 3}
    )
    stale = await case.client.post(path, headers=case.owner_headers, json={"expected_attempts": 2})
    assert forbidden.status_code == 403 and stale.status_code == 409
    foreign_headers = await auth_headers(
        case.engine, case.settings, uuid4(), "foreign-index-admin", roles=("platform_admin",)
    )
    foreign = await case.client.post(path, headers=foreign_headers, json={"expected_attempts": 3})
    assert foreign.status_code == 404
    responses = await asyncio.gather(
        *(
            case.client.post(path, headers=case.owner_headers, json={"expected_attempts": 3})
            for _ in range(2)
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    replacement = UUID(
        next(response.json()["id"] for response in responses if response.status_code == 200)
    )
    retained = await job(case, target)
    assert (retained.attempts, retained.dead_at, retained.error_code) == (
        dead.attempts,
        dead.dead_at,
        dead.error_code,
    )
    assert retained.payload["replacement_event_id"] == str(replacement)
    assert (await job(case, replacement)).payload == {"previous_event_id": str(target)}
    await consume(case)
    assert (await job(case, replacement)).processed_at is not None
    assert (await job(case, target)).processed_at is None
    generations, _ = await index_facts(case)
    assert len(generations) == 2 and sum(row.status == "ready" for row in generations) == 1


@pytest.mark.parametrize("boundary", ["generation", "chunks", "switch", "commit"])
async def test_failed_rebuild_preserves_old_index_and_retries_atomically(
    index_case: KnowledgeCase, boundary: str
):
    case = index_case
    await drain(case)
    baseline = await index_facts(case)
    original_generation_ids = {row.id for row in baseline[0]}
    target = await rebuild(case)
    injected = False

    class FaultSession(Session):
        pass

    def fail_flush(session, context):
        nonlocal injected
        generations = [
            row
            for row in [*session.identity_map.values(), *session.new]
            if isinstance(row, KnowledgeIndexGeneration)
        ]
        hit = any(
            row.document_id == case.document_id
            and row.id not in original_generation_ids
            and (
                (boundary == "generation" and row.status == "pending" and row.chunk_count == 0)
                or (
                    boundary == "chunks"
                    and row.status == "pending"
                    and any(isinstance(item, KnowledgeChunk) for item in session.new)
                )
                or (boundary == "switch" and row.status == "ready" and row.chunk_count > 0)
            )
            for row in generations
        )
        if hit and not injected:
            injected = True
            raise RuntimeError("injected-index-transaction-failure")

    def fail_commit(session):
        nonlocal injected
        if (
            boundary == "commit"
            and not session.in_nested_transaction()
            and any(
                isinstance(row, KnowledgeOutboxEvent) and row.id == target
                for row in session.identity_map.values()
            )
        ):
            injected = True
            raise RuntimeError("injected-index-commit-failure")

    # after_flush 观察实际已写入数据库的阶段，提交失败则观察外层事务。
    event.listen(FaultSession, "after_flush", fail_flush)
    event.listen(FaultSession, "before_commit", fail_commit)
    factory = async_sessionmaker(
        case.engine, class_=AsyncSession, sync_session_class=FaultSession, expire_on_commit=False
    )
    try:
        if boundary == "commit":
            with pytest.raises(RuntimeError, match="injected-index-commit"):
                await consume(case, factory)
        else:
            await consume(case, factory)
        assert injected, f"未命中故障边界：{boundary}"
    finally:
        event.remove(FaultSession, "after_flush", fail_flush)
        event.remove(FaultSession, "before_commit", fail_commit)
    assert await index_facts(case) == baseline
    failed = await job(case, target)
    assert failed.processed_at is None
    assert failed.attempts == (0 if boundary == "commit" else 1)
    assert failed.error_code == (None if boundary == "commit" else "KNOWLEDGE_INDEX_BUILD_FAILED")
    await make_due(case, target)
    await consume(case)
    generations, chunks = await index_facts(case)
    assert len(generations) == 2 and sum(row.status == "ready" for row in generations) == 1
    assert {row.id for row in baseline[1]} < {row.id for row in chunks}
    assert (await job(case, target)).processed_at is not None


async def test_cancellation_before_commit_rolls_back_attempt_and_index(
    index_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch
):
    case = index_case
    await drain(case)
    baseline = await index_facts(case)
    target = await rebuild(case)
    entered = asyncio.Event()
    original = indexing._reconcile_document

    async def pause(session, document, *, force):
        await original(session, document, force=force)
        if document.id == case.document_id:
            await session.flush()
            entered.set()
            await asyncio.Event().wait()

    with monkeypatch.context() as patch:
        patch.setattr(indexing, "_reconcile_document", pause)
        task = asyncio.create_task(consume(case))
        try:
            async with asyncio.timeout(5):
                await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await index_facts(case) == baseline
    assert (await job(case, target)).attempts == 0
    await consume(case)
    assert (await job(case, target)).processed_at is not None


_CONSUMER_PROGRAM = r"""
import asyncio, sys
from uuid import UUID
from supportops_core import knowledge_index_services as indexing
from supportops_core.config import Settings
from supportops_core.db import create_engine, create_session_factory

async def main():
    settings = Settings()
    engine = create_engine(settings)
    mode, document_id = sys.argv[1], UUID(sys.argv[2])
    original = indexing._reconcile_document
    async def pause(session, document, *, force):
        await original(session, document, force=force)
        if document.id == document_id:
            await session.flush()
            print("INDEX_BEFORE_COMMIT", flush=True)
            await asyncio.Event().wait()
    if mode == "before":
        indexing._reconcile_document = pause
    try:
        await indexing.consume_knowledge_events(create_session_factory(engine), settings=settings)
        if mode == "after":
            print("INDEX_AFTER_COMMIT", flush=True)
            await asyncio.Event().wait()
    finally:
        await engine.dispose()
asyncio.run(main())
"""


@pytest.mark.parametrize("boundary", ["before", "after"])
async def test_process_kill_and_fresh_process_resume_only_uncommitted_job(
    index_case: KnowledgeCase, boundary: str
):
    case = index_case
    await drain(case)
    baseline = await index_facts(case)
    target = await rebuild(case)
    env = {**os.environ, "SUPPORTOPS_KNOWLEDGE_INDEX_BATCH_SIZE": "100"}

    async def start(mode):
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _CONSUMER_PROGRAM,
            mode,
            str(case.document_id),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    process = await start(boundary)
    try:
        async with asyncio.timeout(10):
            marker = await process.stdout.readline()
        assert marker == f"INDEX_{boundary.upper()}_COMMIT\n".encode(), "子进程未到达注入点"
        observed = await job(case, target)
        assert observed.attempts == (0 if boundary == "before" else 1)
        assert (observed.processed_at is None) == (boundary == "before")
    finally:
        if process.returncode is None:
            process.kill()
        async with asyncio.timeout(5):
            await process.communicate()
    assert process.returncode != 0
    committed = await index_facts(case)
    assert (committed == baseline) == (boundary == "before")
    restarted = await start("resume")
    try:
        async with asyncio.timeout(10):
            await restarted.communicate()
        assert restarted.returncode == 0, "重启消费者失败"
    finally:
        if restarted.returncode is None:
            restarted.kill()
            await restarted.communicate()
    recovered = await job(case, target)
    assert recovered.attempts == 1 and recovered.processed_at is not None
    generations, chunks = await index_facts(case)
    assert len(generations) == 2 and sum(row.status == "ready" for row in generations) == 1
    assert {row.id for row in baseline[1]} < {row.id for row in chunks}
    if boundary == "after":
        assert await index_facts(case) == committed


async def search_again(case: KnowledgeCase, run_id: UUID):
    async with create_session_factory(case.engine)() as session, session.begin():
        user = await session.get(User, case.reader_id)
        run = await session.get(AgentRun, run_id)
        attempt = await session.scalar(select(RunAttempt).where(RunAttempt.run_id == run_id))
        invocation = await session.scalar(
            select(ToolInvocation).where(ToolInvocation.run_id == run_id)
        )
        identity = IdentityContext(
            Principal(user.id, case.tenant_id, tuple(user.roles), user.display_name), user
        )
        return await search_keyword_knowledge(
            session,
            identity=identity,
            attempt=RunAttemptIdentity(
                case.tenant_id,
                run.conversation_id,
                run_id,
                attempt.id,
                attempt.generation,
                attempt.owner_id,
            ),
            query="PrivateBody 429",
            invocation_id=invocation.id,
        )


@pytest.mark.parametrize("change", ["acl", "retire", "archive", "expire", "supersede"])
async def test_delayed_invalidation_and_old_publish_never_restore_revoked_evidence(
    index_case: KnowledgeCase, change: str, monkeypatch: pytest.MonkeyPatch
):
    case = index_case
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    if change == "expire":
        # 在发布前固定有效期；已发布正文/时间保护保持开启，不修改历史或等真实时钟。
        async with create_session_factory(case.engine)() as session, session.begin():
            version = await create_knowledge_version(
                session,
                document_id=case.document_id,
                tenant_id=case.tenant_id,
                actor_user_id=case.owner_id,
                title="ExpiryTitle",
                content_markdown=BODY + "\nexpiry",
                expected_revision=1,
                effective_until=expires_at,
                correlation_id="index-expiry-version",
            )
            version_id = version.id
        target = await approve(case, version_id)
        async with create_session_factory(case.engine)() as session, session.begin():
            await publish(session, case, target)
        case = replace(case, version_id=version_id)
    run_id, evidence_id = await create_evidence(case)
    baseline = await index_facts(case)
    async with create_session_factory(case.engine)() as session:
        old_publish = await session.scalar(
            select(KnowledgeOutboxEvent.id).where(
                KnowledgeOutboxEvent.document_id == case.document_id,
                KnowledgeOutboxEvent.event_type == "knowledge.published",
                KnowledgeOutboxEvent.version_id == case.version_id,
            )
        )
    if change == "acl":
        await case.acl([])
    elif change == "supersede":
        target = await candidate(case)
        async with create_session_factory(case.engine)() as session, session.begin():
            await publish(session, case, target)
    elif change == "expire":
        from supportops_core import knowledge_retrieval, knowledge_services

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return (
                    expires_at.astimezone(tz) if tz is not None else expires_at.replace(tzinfo=None)
                )

        for module in (indexing, knowledge_retrieval, knowledge_services):
            monkeypatch.setattr(module, "datetime", ExpiredClock)
    else:
        async with create_session_factory(case.engine)() as session, session.begin():
            if change == "archive":
                await archive_knowledge_document(
                    session,
                    document_id=case.document_id,
                    tenant_id=case.tenant_id,
                    actor_user_id=case.owner_id,
                    correlation_id="index-archive",
                )
            elif change == "retire":
                await retire_knowledge_version(
                    session,
                    version_id=case.version_id,
                    tenant_id=case.tenant_id,
                    actor_user_id=case.owner_id,
                    correlation_id="index-retire",
                )
    # 尚未消费失效事件时，旧 ready 索引仍在，但真实读取已拒绝。
    assert await index_facts(case) == baseline
    assert (await search_again(case, run_id))["evidence"] == []
    denied = await case.client.get(
        f"/v1/runs/{run_id}/evidence/{evidence_id}", headers=case.reader_headers
    )
    assert denied.status_code == 404 and "PrivateBody" not in denied.text
    async with create_session_factory(case.engine)() as session, session.begin():
        old = await session.get(KnowledgeOutboxEvent, old_publish)
        old.processed_at = None
    await make_due(case, old_publish)
    await consume(case)
    if change == "expire":
        await indexing.invalidate_expired_knowledge(create_session_factory(case.engine))
    generations, chunks = await index_facts(case)
    assert {row.id for row in baseline[1]} <= {row.id for row in chunks}
    if change in {"retire", "archive", "expire"}:
        assert all(row.status == "invalidated" for row in generations)
    elif change == "acl":
        assert await index_facts(case) == baseline
    else:
        assert sum(row.status == "ready" for row in generations) == 1
        assert all(
            row.status == "invalidated" for row in generations if row.id == baseline[0][0].id
        )
    # 后续新连接仍按当前事实拒绝原证据，而不是复活历史快照。
    denied = await case.client.get(
        f"/v1/runs/{run_id}/evidence/{evidence_id}", headers=case.reader_headers
    )
    assert denied.status_code == 404
    latest = await search_again(case, run_id)
    assert all("PrivateBody" not in item["content"] for item in latest["evidence"])


async def test_locked_document_is_skipped_without_spending_attempt(index_case: KnowledgeCase):
    case = index_case
    await drain(case)
    target = await rebuild(case)
    async with create_session_factory(case.engine)() as guard, guard.begin():
        from supportops_core.knowledge_services import lock_knowledge_document

        await lock_knowledge_document(guard, document_id=case.document_id, tenant_id=case.tenant_id)
        async with asyncio.timeout(3):
            await consume(case)
        assert (await job(case, target)).attempts == 0
    await consume(case)
    assert (await job(case, target)).processed_at is not None


async def test_late_retirement_event_does_not_invalidate_new_publication(index_case: KnowledgeCase):
    case = index_case
    await drain(case)
    async with create_session_factory(case.engine)() as session, session.begin():
        await retire_knowledge_version(
            session,
            version_id=case.version_id,
            tenant_id=case.tenant_id,
            actor_user_id=case.owner_id,
            correlation_id="late-index-retire",
        )
        retired_event = await session.scalar(
            select(KnowledgeOutboxEvent).where(
                KnowledgeOutboxEvent.document_id == case.document_id,
                KnowledgeOutboxEvent.event_type == "knowledge.retired",
            )
        )
        assert retired_event is not None
        retired_event.available_at = datetime.now(UTC) + timedelta(hours=1)
        retired_id = retired_event.id
    target = await candidate(case)
    async with create_session_factory(case.engine)() as session, session.begin():
        await publish(session, case, target)
    await consume(case)
    before_late_event = await index_facts(case)
    assert sum(row.status == "ready" for row in before_late_event[0]) == 1
    await make_due(case, retired_id)
    await consume(case)
    assert (await job(case, retired_id)).processed_at is not None
    assert await index_facts(case) == before_late_event
