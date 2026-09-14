from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from supportops_core.auth import IdentityContext, Principal
from supportops_core.enums import (
    KnowledgeAclSubjectType,
    KnowledgeSourceEnvironment,
    KnowledgeVersionStatus,
)
from supportops_core.knowledge_services import (
    KnowledgeConflictError,
    KnowledgeRevisionConflictError,
    create_knowledge_document,
    create_knowledge_source,
    create_knowledge_version,
    knowledge_access_allowed,
    publish_knowledge_version,
    rollback_knowledge_version,
    submit_knowledge_version_for_review,
)
from supportops_core.models import (
    Base,
    KnowledgeAclEntry,
    KnowledgeOutboxEvent,
    KnowledgeVersion,
    User,
)


@pytest.fixture
async def engine(tmp_path):
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}")
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield test_engine
    await test_engine.dispose()


@pytest.fixture
async def knowledge_context(engine) -> tuple[AsyncSession, User, User]:
    session = AsyncSession(engine, expire_on_commit=False)
    tenant_id = uuid4()
    owner = User(
        id=uuid4(),
        tenant_id=tenant_id,
        email=f"owner-{tenant_id}@example.test",
        password_hash="hash",
        display_name="owner",
        roles=["knowledge_admin"],
    )
    customer = User(
        id=uuid4(),
        tenant_id=tenant_id,
        email=f"customer-{tenant_id}@example.test",
        password_hash="hash",
        display_name="customer",
        roles=["customer"],
    )
    from supportops_core.models import Tenant

    session.add(Tenant(id=tenant_id, name="Knowledge tenant"))
    session.add_all([owner, customer])
    await session.commit()
    yield session, owner, customer
    await session.close()


@pytest.mark.asyncio
async def test_publish_snapshot_and_acl(engine, knowledge_context) -> None:
    session, owner, customer = knowledge_context
    tenant_id = owner.tenant_id
    source = await create_knowledge_source(
        session,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        external_key="support-handbook",
        name="Support handbook",
        source_type="manual",
        environment=KnowledgeSourceEnvironment.DEV,
        owner_user_id=owner.id,
        correlation_id="corr-1",
    )
    document = await create_knowledge_document(
        session,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        source_id=source.id,
        external_key="429-guide",
        owner_user_id=owner.id,
        title="429 guide",
        content_markdown="# 429\n\nCheck RPM",
        correlation_id="corr-2",
    )
    session.add(
        KnowledgeAclEntry(
            tenant_id=tenant_id,
            document_id=document.id,
            subject_type=KnowledgeAclSubjectType.ROLE,
            subject_id="customer",
            created_by=owner.id,
        )
    )
    await session.flush()
    version = await session.scalar(
        select(KnowledgeVersion).where(KnowledgeVersion.document_id == document.id)
    )
    assert version is not None
    await submit_knowledge_version_for_review(
        session,
        version_id=version.id,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        correlation_id="corr-3",
    )
    snapshot = await publish_knowledge_version(
        session,
        version_id=version.id,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        runtime_environment="dev",
        correlation_id="corr-4",
    )
    assert snapshot.content_digest == version.content_digest
    assert await knowledge_access_allowed(
        session,
        document_id=document.id,
        tenant_id=tenant_id,
        identity=IdentityContext(
            principal=Principal(customer.id, tenant_id, ("customer",), "customer"),
            user=customer,
        ),
    )
    assert await session.scalar(select(KnowledgeOutboxEvent).where(KnowledgeOutboxEvent.version_id == version.id))


@pytest.mark.asyncio
async def test_version_revision_and_duplicate_are_rejected(engine, knowledge_context) -> None:
    session, owner, _customer = knowledge_context
    tenant_id = owner.tenant_id
    source = await create_knowledge_source(
        session,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        external_key="source",
        name="Source",
        source_type="manual",
        environment=KnowledgeSourceEnvironment.DEV,
        owner_user_id=owner.id,
        correlation_id="corr-1",
    )
    document = await create_knowledge_document(
        session,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        source_id=source.id,
        external_key="doc",
        owner_user_id=owner.id,
        title="Doc",
        content_markdown="initial",
        correlation_id="corr-2",
    )
    with pytest.raises(KnowledgeRevisionConflictError):
        await create_knowledge_version(
            session,
            document_id=document.id,
            tenant_id=tenant_id,
            actor_user_id=owner.id,
            title="Doc",
            content_markdown="changed",
            expected_revision=0,
            correlation_id="corr-3",
        )
    version = await create_knowledge_version(
        session,
        document_id=document.id,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        title="Doc",
        content_markdown="changed",
        expected_revision=1,
        correlation_id="corr-4",
    )
    assert version.status == KnowledgeVersionStatus.DRAFT
    with pytest.raises(KnowledgeConflictError):
        await create_knowledge_version(
            session,
            document_id=document.id,
            tenant_id=tenant_id,
            actor_user_id=owner.id,
            title="Doc",
            content_markdown="changed",
            expected_revision=2,
            correlation_id="corr-5",
        )


@pytest.mark.asyncio
async def test_rollback_creates_new_version_with_same_content(engine, knowledge_context) -> None:
    session, owner, _customer = knowledge_context
    tenant_id = owner.tenant_id
    source = await create_knowledge_source(
        session,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        external_key="source",
        name="Source",
        source_type="manual",
        environment=KnowledgeSourceEnvironment.DEV,
        owner_user_id=owner.id,
        correlation_id="corr-1",
    )
    document = await create_knowledge_document(
        session,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        source_id=source.id,
        external_key="doc",
        owner_user_id=owner.id,
        title="Doc",
        content_markdown="initial",
        correlation_id="corr-2",
    )
    original = await session.scalar(
        select(KnowledgeVersion).where(KnowledgeVersion.document_id == document.id)
    )
    assert original is not None
    rolled = await rollback_knowledge_version(
        session,
        document_id=document.id,
        source_version_id=original.id,
        tenant_id=tenant_id,
        actor_user_id=owner.id,
        expected_revision=1,
        correlation_id="corr-3",
    )
    assert rolled.version_number == 2
    assert rolled.content_digest == original.content_digest
