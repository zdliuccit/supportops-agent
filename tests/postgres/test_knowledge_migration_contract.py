"""在完整迁移的隔离数据库对照知识生命周期 ORM 与实际 DDL。"""

import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.schema import SchemaItem
from supportops_core.models import Base

from tests.postgres.test_knowledge_access import engine as engine
from tests.postgres.test_knowledge_access import settings as settings

pytestmark = pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1", reason="需显式启用隔离 PostgreSQL 测试库"
)

TABLES = {
    "knowledge_sources",
    "knowledge_documents",
    "knowledge_versions",
    "knowledge_acl_entries",
    "knowledge_snapshots",
    "knowledge_outbox_events",
}


async def test_knowledge_lifecycle_migrated_schema_matches_models(engine: AsyncEngine) -> None:
    def compare(connection: Connection) -> None:
        assert TABLES <= set(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261022_0064"

        def include_object(
            obj: SchemaItem,
            name: str | None,
            kind: str,
            reflected: bool,
            compare_to: SchemaItem | None,
        ) -> bool:
            # 仅界定本项知识生命周期；全库 Alembic check 和注释检查继续独立运行。
            return name in TABLES if kind == "table" else True

        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "include_object": include_object,
            },
        )
        assert compare_metadata(context, Base.metadata) == []

    async with engine.connect() as connection:
        await connection.run_sync(compare)
