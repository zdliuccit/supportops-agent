"""统一业务资源软删除，保留关联历史并释放已删除资源的唯一名称。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0017"
down_revision: str | None = "20260914_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "organization_units",
    "model_credentials",
    "model_credential_revisions",
    "model_endpoints",
    "model_endpoint_versions",
    "model_endpoint_models",
    "model_endpoint_test_runs",
    "agent_access_grants",
    "knowledge_documents",
    "knowledge_versions",
    "knowledge_acl_entries",
    "knowledge_snapshots",
    "conversations",
)
ACTIVE_UNIQUES = (
    (
        "organization_units",
        "uq_organization_units_sibling_name",
        ("tenant_id", "parent_id", "name"),
    ),
    ("model_endpoints", "uq_model_endpoints_tenant_name", ("tenant_id", "name")),
    (
        "agent_access_grants",
        "uq_agent_access_grants_active_subject",
        ("agent_id", "subject_type", "subject_id"),
    ),
    (
        "knowledge_documents",
        "uq_knowledge_documents_external",
        ("tenant_id", "source_id", "external_key"),
    ),
    (
        "knowledge_acl_entries",
        "uq_knowledge_acl_subject",
        ("tenant_id", "document_id", "subject_type", "subject_id"),
    ),
)


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "deleted_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="软删除时间；为空表示未删除。",
            ),
        )
    inspector = sa.inspect(op.get_bind())
    for table, name, columns in ACTIVE_UNIQUES:
        # 旧约束有命名与数据库自动命名两种形式，按列匹配真实名称。
        for constraint in inspector.get_unique_constraints(table):
            if tuple(constraint["column_names"]) == columns:
                op.drop_constraint(constraint["name"], table, type_="unique")
        op.create_index(
            name,
            table,
            list(columns),
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
            sqlite_where=sa.text("deleted_at IS NULL"),
        )
    # 旧知识 DELETE 已使用 archived 状态，迁移后同样从默认查询中隐藏。
    op.execute(
        sa.text("UPDATE knowledge_documents SET deleted_at = updated_at WHERE status = 'ARCHIVED'")
    )
    for table in ("knowledge_versions", "knowledge_acl_entries", "knowledge_snapshots"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET deleted_at = (SELECT deleted_at FROM knowledge_documents "
                f"WHERE knowledge_documents.id = {table}.document_id) "
                "WHERE document_id IN (SELECT id FROM knowledge_documents "
                "WHERE deleted_at IS NOT NULL)"
            )
        )


def downgrade() -> None:
    # 删除后可出现同名历史，恢复旧全量唯一约束会冲突，因此禁止有历史数据时回退。
    connection = op.get_bind()
    for table in TABLES:
        if connection.scalar(sa.text(f"SELECT count(*) FROM {table} WHERE deleted_at IS NOT NULL")):
            raise RuntimeError("存在软删除历史，不能移除删除标记；请先备份并显式处理历史数据")
    for table, name, columns in reversed(ACTIVE_UNIQUES):
        op.drop_index(name, table_name=table)
        op.create_unique_constraint(name, table, list(columns))
    for table in reversed(TABLES):
        op.drop_column(table, "deleted_at")
