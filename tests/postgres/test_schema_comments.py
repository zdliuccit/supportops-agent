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
    """确认 Alembic 已把 ORM 数据字典同步到真实 PostgreSQL。"""
    settings = Settings()
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("当前 SUPPORTOPS_DATABASE_URL 不是 PostgreSQL")

    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            for table in Base.metadata.sorted_tables:
                table_comment = await connection.scalar(
                    text("SELECT obj_description(to_regclass(:table_name), 'pg_class')"),
                    {"table_name": table.name},
                )
                assert table_comment == table.comment
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
                    assert column_comment == column.comment
    finally:
        await engine.dispose()
