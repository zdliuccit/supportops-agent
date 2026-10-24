"""真实迁移库中的知识版本、审核修订、不可变历史与有效期契约。"""

import os
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from supportops_core.auth import IdentityContext, Principal
from supportops_core.db import create_session_factory
from supportops_core.enums import KnowledgeSourceEnvironment, KnowledgeVersionStatus
from supportops_core.knowledge_services import (
    KnowledgeAccessDeniedError,
    KnowledgeConflictError,
    KnowledgeRevisionConflictError,
    create_knowledge_version,
    decide_knowledge_review,
    get_effective_knowledge_snapshot,
    knowledge_access_allowed,
    publish_knowledge_version,
    rollback_knowledge_version,
)
from supportops_core.models import (
    KnowledgeDocument,
    KnowledgeSnapshot,
    KnowledgeSource,
    KnowledgeVersion,
    User,
)

from tests.api.test_knowledge_access import BODY, KnowledgeCase
from tests.api.test_knowledge_access import knowledge_case as knowledge_case
from tests.postgres.test_knowledge_access import engine as engine
from tests.postgres.test_knowledge_access import settings as settings
from tests.postgres.test_knowledge_lifecycle import Candidate, approve, candidate, facts, publish

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)


@pytest.mark.parametrize("operation", ["create", "publish", "review", "rollback"])
async def test_stale_revision_is_rejected_without_product_changes(
    knowledge_case: KnowledgeCase, operation: str
):
    case = knowledge_case
    target = await candidate(case)
    baseline = await facts(case)
    async with create_session_factory(case.engine)() as session:
        with pytest.raises(KnowledgeRevisionConflictError):
            async with session.begin():
                if operation == "create":
                    await create_knowledge_version(
                        session,
                        document_id=case.document_id,
                        tenant_id=case.tenant_id,
                        actor_user_id=case.owner_id,
                        title="旧修订",
                        content_markdown="stale revision",
                        expected_revision=1,
                        correlation_id="stale",
                    )
                elif operation == "publish":
                    await publish(session, case, Candidate(target.id, target.revision - 1))
                elif operation == "rollback":
                    await rollback_knowledge_version(
                        session,
                        document_id=case.document_id,
                        source_version_id=case.version_id,
                        tenant_id=case.tenant_id,
                        actor_user_id=case.owner_id,
                        expected_revision=1,
                        correlation_id="stale",
                    )
                else:
                    version = await session.get(KnowledgeVersion, target.id)
                    await decide_knowledge_review(
                        session,
                        version_id=target.id,
                        tenant_id=case.tenant_id,
                        actor_user_id=uuid5(NAMESPACE_URL, f"{case.tenant_id}:knowledge-reviewer"),
                        decision="approved",
                        expected_review_revision=target.revision - 1,
                        expected_content_digest=version.content_digest,
                        comment="",
                        correlation_id="stale",
                    )
    assert await facts(case) == baseline


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_markdown", "mutated body"),
        ("title", "mutated title"),
        ("content_digest", "0" * 64),
        ("section_anchors", []),
        ("effective_from", datetime(2020, 1, 1, tzinfo=UTC)),
    ],
)
@pytest.mark.parametrize("path", ["orm", "sql"])
async def test_published_content_cannot_be_rewritten(
    knowledge_case: KnowledgeCase, field: str, value, path: str
):
    case = knowledge_case
    factory = create_session_factory(case.engine)
    with pytest.raises((ValueError, SQLAlchemyError), match="IMMUTABLE|不可|immutable"):
        async with factory() as session, session.begin():
            if path == "orm":
                version = await session.get(KnowledgeVersion, case.version_id)
                setattr(version, field, value)
                await session.flush()
            else:
                await session.execute(
                    update(KnowledgeVersion)
                    .where(KnowledgeVersion.id == case.version_id)
                    .values({field: value})
                )
    async with factory() as session:
        version = await session.get(KnowledgeVersion, case.version_id)
        assert version.content_markdown == BODY
        snapshot = await session.scalar(
            select(KnowledgeSnapshot).where(KnowledgeSnapshot.version_id == case.version_id)
        )
        assert version.content_digest == snapshot.content_digest
        assert (
            version.title == snapshot.title and version.section_anchors == snapshot.section_anchors
        )


async def test_rollback_creates_unapproved_draft_and_preserves_original_until_republished(
    knowledge_case: KnowledgeCase,
):
    case = knowledge_case
    factory = create_session_factory(case.engine)
    target = await candidate(case)
    async with factory() as session, session.begin():
        await publish(session, case, target)
    baseline = await facts(case)
    async with factory() as session, session.begin():
        rolled = await rollback_knowledge_version(
            session,
            document_id=case.document_id,
            source_version_id=case.version_id,
            tenant_id=case.tenant_id,
            actor_user_id=case.owner_id,
            expected_revision=2,
            correlation_id="rollback",
        )
        assert rolled.id not in {target.id, case.version_id} and rolled.version_number == 3
        assert rolled.content_markdown == BODY and rolled.status == KnowledgeVersionStatus.DRAFT
        assert rolled.review_decision is None and rolled.reviewed_by is None
        assert rolled.published_at is None
        rolled_id = rolled.id
    assert (await facts(case))[0] == target.id
    assert (await facts(case))[3] == baseline[3]
    with pytest.raises(KnowledgeConflictError):
        async with factory() as session, session.begin():
            await publish(session, case, Candidate(rolled_id, 0))
    target = await approve(case, rolled_id)
    async with factory() as session, session.begin():
        await publish(session, case, target)
    async with factory() as session:
        old = await session.get(KnowledgeVersion, case.version_id)
        new = await session.get(KnowledgeVersion, rolled_id)
        assert old.status == KnowledgeVersionStatus.SUPERSEDED and old.content_markdown == BODY
        assert new.status == KnowledgeVersionStatus.PUBLISHED
        assert old.content_digest == new.content_digest


@pytest.mark.parametrize("state", ["future", "active", "expired", "at_start", "at_end"])
async def test_effective_interval_controls_snapshot_at_exact_boundaries(
    knowledge_case: KnowledgeCase, monkeypatch: pytest.MonkeyPatch, state: str
):
    from supportops_core import knowledge_services

    case = knowledge_case
    factory = create_session_factory(case.engine)
    now = datetime.now(UTC)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    if state in {"future", "at_start"}:
        start = now + timedelta(minutes=1)
    if state in {"expired", "at_end"}:
        end = now - timedelta(minutes=1)
    async with factory() as session, session.begin():
        version = await create_knowledge_version(
            session,
            document_id=case.document_id,
            tenant_id=case.tenant_id,
            actor_user_id=case.owner_id,
            title=f"有效期{state}",
            content_markdown=f"validity {state}",
            expected_revision=1,
            effective_from=start,
            effective_until=end,
            correlation_id="validity",
        )
        version_id = version.id
    target = await approve(case, version_id)
    async with factory() as session, session.begin():
        await publish(session, case, target)
    clock_value = start if state == "at_start" else end if state == "at_end" else now

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock_value.astimezone(tz) if tz else clock_value.replace(tzinfo=None)

    monkeypatch.setattr(knowledge_services, "datetime", FixedDateTime)
    async with factory() as session, session.begin():
        reader = await session.get(User, case.reader_id)
        identity = IdentityContext(
            Principal(reader.id, case.tenant_id, tuple(reader.roles), reader.display_name), reader
        )
        allowed = state in {"active", "at_start"}
        assert (
            await knowledge_access_allowed(
                session, document_id=case.document_id, tenant_id=case.tenant_id, identity=identity
            )
            is allowed
        )
        if allowed:
            snapshot = await get_effective_knowledge_snapshot(
                session, document_id=case.document_id, tenant_id=case.tenant_id, identity=identity
            )
            assert snapshot.version_id == target.id
        else:
            with pytest.raises(KnowledgeAccessDeniedError):
                await get_effective_knowledge_snapshot(
                    session,
                    document_id=case.document_id,
                    tenant_id=case.tenant_id,
                    identity=identity,
                )


async def test_invalid_effective_range_is_rejected_atomically(knowledge_case: KnowledgeCase):
    case = knowledge_case
    baseline = await facts(case)
    now = datetime.now(UTC)
    with pytest.raises((ValueError, SQLAlchemyError)):
        async with create_session_factory(case.engine)() as session, session.begin():
            await create_knowledge_version(
                session,
                document_id=case.document_id,
                tenant_id=case.tenant_id,
                actor_user_id=case.owner_id,
                title="非法有效期",
                content_markdown="invalid dates",
                expected_revision=1,
                effective_from=now,
                effective_until=now,
                correlation_id="invalid",
            )
    assert await facts(case) == baseline


async def test_mock_publish_in_production_is_rejected_and_test_publish_still_requires_review(
    knowledge_case: KnowledgeCase,
):
    case = knowledge_case
    factory = create_session_factory(case.engine)
    async with factory() as session, session.begin():
        document = await session.get(KnowledgeDocument, case.document_id)
        source = await session.get(KnowledgeSource, document.source_id)
        source.environment = KnowledgeSourceEnvironment.MOCK
    target = await candidate(case)
    baseline = await facts(case)
    with pytest.raises(KnowledgeConflictError, match="Mock"):
        async with factory() as session, session.begin():
            await publish_knowledge_version(
                session,
                version_id=target.id,
                tenant_id=case.tenant_id,
                actor_user_id=case.owner_id,
                expected_review_revision=target.revision,
                runtime_environment="production",
                correlation_id="reject-mock",
            )
    assert await facts(case) == baseline
    async with factory() as session, session.begin():
        await publish(session, case, target)
    assert (await facts(case))[0] == target.id
