from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from supportops_core.config import Settings
from supportops_core.db import create_engine
from supportops_core.models import Base


@pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="设置 SUPPORTOPS_RUN_POSTGRES_TESTS=1 后检查真实 PostgreSQL 注释",
)
async def test_postgres_contains_all_model_table_and_column_comments() -> None:
    """确认全部业务表注释；独立保留全项目检查，不被知识范围检查替代。"""
    await _assert_postgres_comments()


@pytest.mark.skipif(
    os.getenv("SUPPORTOPS_RUN_POSTGRES_TESTS") != "1",
    reason="设置 SUPPORTOPS_RUN_POSTGRES_TESTS=1 后检查真实 PostgreSQL 知识注释",
)
async def test_postgres_knowledge_lifecycle_comments() -> None:
    """只验收知识生命周期的六张表，其他模块仍由上面的全库检查约束。"""
    await _assert_postgres_comments(
        {
            "knowledge_sources",
            "knowledge_documents",
            "knowledge_versions",
            "knowledge_acl_entries",
            "knowledge_snapshots",
            "knowledge_outbox_events",
        }
    )


async def _assert_postgres_comments(table_names: set[str] | None = None) -> None:
    settings = Settings()
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("当前 SUPPORTOPS_DATABASE_URL 不是 PostgreSQL")

    engine = create_engine(settings)
    mismatches: list[str] = []
    try:
        async with engine.connect() as connection:
            for table in Base.metadata.sorted_tables:
                if table_names is not None and table.name not in table_names:
                    continue
                table_comment = await connection.scalar(
                    text("SELECT obj_description(to_regclass(:table_name), 'pg_class')"),
                    {"table_name": table.name},
                )
                if table_comment != table.comment:
                    mismatches.append(f"{table.name}: {table_comment!r} != {table.comment!r}")
                for column in table.columns:
                    column_comment = await connection.scalar(
                        text(
                            "SELECT col_description(c.oid, a.attnum) "
                            "FROM pg_class c "
                            "JOIN pg_attribute a ON a.attrelid = c.oid "
                            "WHERE c.oid = to_regclass(:table_name) AND a.attname = :column_name"
                        ),
                        {"table_name": table.name, "column_name": column.name},
                    )
                    if column_comment != column.comment:
                        mismatches.append(
                            f"{table.name}.{column.name}: {column_comment!r} != {column.comment!r}"
                        )
            assert mismatches == [], "\n".join(mismatches)
    finally:
        await engine.dispose()
