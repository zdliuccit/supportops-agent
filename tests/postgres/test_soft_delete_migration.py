"""在独立 PostgreSQL 数据库中验证真实旧版升级与历史数据保留。"""

import asyncio
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, select, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from supportops_core.config import Settings
from supportops_core.db import create_session_factory
from supportops_core.enums import (
    KnowledgeDocumentStatus,
    KnowledgeSourceEnvironment,
    KnowledgeVersionStatus,
    MessageRole,
    RunStatus,
)
from supportops_core.models import (
    Agent,
    AgentRun,
    Base,
    Conversation,
    KnowledgeDocument,
    KnowledgeVersion,
    Message,
    ModelEndpoint,
    User,
    utc_now,
)
from supportops_core.services import (
    content_digest,
    create_message_and_run,
    delete_conversation,
    get_conversation,
)

from tests.conftest import provision_agent


@pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="设置 SUPPORTOPS_RUN_POSTGRES_TESTS=1 后验证真实 PostgreSQL 迁移",
)
async def test_soft_delete_migration_preserves_and_filters_archived_history() -> None:
    settings = Settings()
    url = make_url(settings.database_url)
    if not url.drivername.startswith("postgresql") or url.host not in ("localhost", "127.0.0.1"):
        pytest.skip("隔离迁移测试只使用本地 PostgreSQL")
    assert settings.environment == "test" and url.database and url.database.endswith("_test")
    database = f"soft_delete_{uuid4().hex}_test"
    admin_engine = create_async_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    engine = create_async_engine(url.set(database=database))
    tenant_id, user_id, source_id, document_id = (uuid4() for _ in range(4))
    created = False

    def migrate(connection: Connection, revision: str) -> None:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, revision)

    def prepare_previous_schema(connection: Connection) -> MetaData:
        # 历史迁移使用固定 public/checkpoint schema，因此必须在本测试独占的空库执行。
        # 先走真实迁移到0016，不能用当前ORM建全部表再stamp旧版本。
        migrate(connection, "20260914_0016")
        metadata = MetaData()
        metadata.reflect(connection)
        assert "run_notification_outbox" not in metadata.tables
        assert "deleted_at" not in metadata.tables["knowledge_documents"].c
        assert "review_revision" not in metadata.tables["knowledge_versions"].c
        # 只为旧库实际存在的列复用插入默认值和类型转换，不修改数据库结构。
        for table in metadata.tables.values():
            current = Base.metadata.tables.get(table.name)
            if current is None:
                continue
            for column in table.columns:
                if column.name in current.c:
                    column.default = current.c[column.name].default
                    column.type = current.c[column.name].type
        return metadata

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT current_database()")) == database
            previous = await connection.run_sync(prepare_previous_schema)
            await connection.execute(
                previous.tables["tenants"].insert().values(id=tenant_id, name="迁移测试")
            )
            await connection.execute(
                previous.tables["users"]
                .insert()
                .values(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=f"{user_id}@example.test",
                    password_hash="test",
                    display_name="迁移测试",
                )
            )
            await connection.execute(
                previous.tables["knowledge_sources"]
                .insert()
                .values(
                    id=source_id,
                    tenant_id=tenant_id,
                    external_key="source",
                    name="测试来源",
                    source_type="manual",
                    environment=KnowledgeSourceEnvironment.DEV,
                    owner_user_id=user_id,
                    created_by=user_id,
                )
            )
            await connection.execute(
                previous.tables["knowledge_documents"]
                .insert()
                .values(
                    id=document_id,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    external_key="old-delete",
                    owner_user_id=user_id,
                    created_by=user_id,
                    status=KnowledgeDocumentStatus.ARCHIVED,
                )
            )
            await connection.execute(
                previous.tables["knowledge_versions"]
                .insert()
                .values(
                    tenant_id=tenant_id,
                    document_id=document_id,
                    version_number=1,
                    title="历史正文",
                    content_markdown="# 保留",
                    content_digest="test",
                    created_by=user_id,
                    status=KnowledgeVersionStatus.IN_REVIEW,
                    reviewed_by=user_id,
                    reviewed_at=utc_now(),
                )
            )
            await connection.run_sync(migrate, "head")
            assert (
                await connection.scalar(
                    select(KnowledgeDocument.deleted_at).where(KnowledgeDocument.id == document_id)
                )
                is not None
            )
            assert (
                await connection.scalar(
                    select(KnowledgeVersion.deleted_at).where(
                        KnowledgeVersion.document_id == document_id
                    )
                )
                is not None
            )
            assert await connection.scalar(select(KnowledgeVersion.content_markdown)) == "# 保留"
            review = (
                await connection.execute(
                    select(
                        KnowledgeVersion.submitted_by,
                        KnowledgeVersion.submitted_at,
                        KnowledgeVersion.reviewed_by,
                        KnowledgeVersion.reviewed_at,
                        KnowledgeVersion.review_decision,
                        KnowledgeVersion.review_revision,
                        KnowledgeVersion.review_scope_digest,
                    )
                )
            ).one()
            assert review.submitted_by == user_id and review.submitted_at is not None
            assert review.reviewed_by is None and review.reviewed_at is None
            assert review.review_decision == "pending" and review.review_revision == 0
            assert review.review_scope_digest is None
            endpoint_id = uuid4()
            payload = dict(
                tenant_id=tenant_id, name="同名连接", created_by=user_id, updated_by=user_id
            )
            await connection.execute(
                ModelEndpoint.__table__.insert().values(id=endpoint_id, **payload)
            )
            await connection.execute(
                ModelEndpoint.__table__.update()
                .where(ModelEndpoint.id == endpoint_id)
                .values(
                    deleted_at=KnowledgeDocument.__table__.select()
                    .with_only_columns(KnowledgeDocument.deleted_at)
                    .scalar_subquery()
                )
            )
            await connection.execute(ModelEndpoint.__table__.insert().values(**payload))
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(ModelEndpoint.__table__.insert().values(**payload))
        # PostgreSQL 的外键插入需要 KEY SHARE 锁；验证删除与 Worker 提交不会形成死锁。
        agent_id = await provision_agent(engine, settings, tenant_id, subject="concurrent-worker")
        factory = create_session_factory(engine)
        async with factory() as session, session.begin():
            agent = await session.get(Agent, agent_id)
            user = await session.scalar(
                select(User).where(User.display_name == "concurrent-worker")
            )
            assert agent is not None and user is not None and agent.active_version_id is not None
            conversation = Conversation(
                tenant_id=tenant_id,
                user_id=user.id,
                agent_id=agent_id,
                agent_version_id=agent.active_version_id,
            )
            session.add(conversation)
            await session.flush()
            result = await create_message_and_run(
                session,
                conversation=conversation,
                user_id=user.id,
                identity_roles=("customer",),
                content="并发删除",
                idempotency_key="concurrent",
                correlation_id="concurrent",
            )
            result.run.status = RunStatus.RUNNING
            run_id, conversation_id, owner_id = result.run.id, conversation.id, user.id
        run_locked, conversation_locked = asyncio.Event(), asyncio.Event()

        async def worker_commit() -> None:
            async with factory() as session, session.begin():
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                assert run is not None
                run_locked.set()
                await conversation_locked.wait()
                session.add(
                    Message(
                        tenant_id=tenant_id,
                        user_id=owner_id,
                        conversation_id=conversation_id,
                        role=MessageRole.ASSISTANT,
                        content="已提交回答",
                        content_hash=content_digest("已提交回答"),
                        idempotency_key="worker-reply",
                        correlation_id="concurrent",
                    )
                )
                run.status = RunStatus.COMPLETED
                await session.flush()

        async def concurrent_delete() -> None:
            await run_locked.wait()
            async with factory() as session, session.begin():
                await get_conversation(
                    session,
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    user_id=owner_id,
                    lock=True,
                )
                conversation_locked.set()
                await delete_conversation(
                    session,
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    user_id=owner_id,
                )

        await asyncio.wait_for(asyncio.gather(worker_commit(), concurrent_delete()), timeout=5)
        async with factory() as session:
            assert await session.get(Conversation, conversation_id) is None
            run = await session.get(AgentRun, run_id)
            assert run is not None and run.status == RunStatus.COMPLETED
    finally:
        await engine.dispose()
        if created:
            async with admin_engine.connect() as connection:
                # 仅清理本测试已确认随机创建的数据库，不接触宿主测试库或应用数据库。
                await connection.execute(text(f'DROP DATABASE "{database}"'))
        await admin_engine.dispose()
