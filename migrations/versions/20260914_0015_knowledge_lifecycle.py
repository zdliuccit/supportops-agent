"""增加知识生命周期、ACL、发布快照和持久化事件。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260914_0015"
down_revision: str | None = "20260914_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Uuid(), nullable=False, comment="知识来源唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="来源所属租户。"),
        sa.Column("external_key", sa.String(length=200), nullable=False, comment="来源稳定外部标识。"),
        sa.Column("name", sa.String(length=200), nullable=False, comment="来源显示名称。"),
        sa.Column("source_type", sa.String(length=32), nullable=False, comment="来源类型。"),
        sa.Column("environment", sa.String(length=32), nullable=False, comment="来源环境。"),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False, comment="来源负责人。"),
        sa.Column("metadata", JSON, nullable=False, comment="来源治理元数据。"),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "external_key", name="uq_knowledge_sources_tenant_external"),
        comment="租户级知识来源、环境和治理负责人。",
    )
    op.create_index("ix_knowledge_sources_tenant_id", "knowledge_sources", ["tenant_id"])
    op.create_index("ix_knowledge_sources_environment", "knowledge_sources", ["environment"])

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), nullable=False, comment="知识文档唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("external_key", sa.String(length=200), nullable=False, comment="来源内稳定文档标识。"),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "source_id", "external_key", name="uq_knowledge_documents_external"),
        comment="知识文档稳定身份、受众、有效期和当前发布版本。",
    )
    for name, columns in (
        ("ix_knowledge_documents_tenant_id", ["tenant_id"]),
        ("ix_knowledge_documents_source_id", ["source_id"]),
        ("ix_knowledge_documents_status", ["status"]),
        ("ix_knowledge_documents_current_version_id", ["current_version_id"]),
    ):
        op.create_index(name, "knowledge_documents", columns)

    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("section_anchors", JSON, nullable=False),
        sa.Column("change_summary", sa.String(length=2000), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("published_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["published_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_knowledge_versions_number"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_knowledge_versions_effective_range",
        ),
        comment="不可变 Markdown 知识版本、章节锚点和有效期。",
    )
    for name, columns in (
        ("ix_knowledge_versions_tenant_id", ["tenant_id"]),
        ("ix_knowledge_versions_document_id", ["document_id"]),
        ("ix_knowledge_versions_status", ["status"]),
    ):
        op.create_index(name, "knowledge_versions", columns)
    op.create_foreign_key(
        "fk_knowledge_documents_current_version_id",
        "knowledge_documents",
        "knowledge_versions",
        ["current_version_id"],
        ["id"],
    )

    op.create_table(
        "knowledge_acl_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "document_id", "subject_type", "subject_id", name="uq_knowledge_acl_subject"
        ),
        comment="知识文档角色、部门或用户允许列表。",
    )
    op.create_index("ix_knowledge_acl_entries_tenant_id", "knowledge_acl_entries", ["tenant_id"])
    op.create_index("ix_knowledge_acl_entries_document_id", "knowledge_acl_entries", ["document_id"])

    op.create_table(
        "knowledge_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("section_anchors", JSON, nullable=False),
        sa.Column("acl_digest", sa.String(length=64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["knowledge_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version_id", name="uq_knowledge_snapshots_document_version"),
        comment="供后续检索使用的已发布知识不可变快照。",
    )
    for name, columns in (
        ("ix_knowledge_snapshots_tenant_id", ["tenant_id"]),
        ("ix_knowledge_snapshots_document_id", ["document_id"]),
        ("ix_knowledge_snapshots_version_id", ["version_id"]),
    ):
        op.create_index(name, "knowledge_snapshots", columns)

    op.create_table(
        "knowledge_outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["knowledge_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "event_key", name="uq_knowledge_outbox_event_key"),
        comment="知识生命周期事件，供索引器可靠、幂等消费。",
    )
    op.create_index("ix_knowledge_outbox_events_tenant_id", "knowledge_outbox_events", ["tenant_id"])
    op.create_index("ix_knowledge_outbox_events_event_type", "knowledge_outbox_events", ["event_type"])
    op.create_index("ix_knowledge_outbox_events_document_id", "knowledge_outbox_events", ["document_id"])
    op.create_index("ix_knowledge_outbox_events_available_at", "knowledge_outbox_events", ["available_at"])


def downgrade() -> None:
    for name in (
        "ix_knowledge_outbox_events_available_at",
        "ix_knowledge_outbox_events_document_id",
        "ix_knowledge_outbox_events_event_type",
        "ix_knowledge_outbox_events_tenant_id",
    ):
        op.drop_index(name, table_name="knowledge_outbox_events")
    op.drop_table("knowledge_outbox_events")
    for name in (
        "ix_knowledge_snapshots_version_id",
        "ix_knowledge_snapshots_document_id",
        "ix_knowledge_snapshots_tenant_id",
    ):
        op.drop_index(name, table_name="knowledge_snapshots")
    op.drop_table("knowledge_snapshots")
    op.drop_index("ix_knowledge_acl_entries_document_id", table_name="knowledge_acl_entries")
    op.drop_index("ix_knowledge_acl_entries_tenant_id", table_name="knowledge_acl_entries")
    op.drop_table("knowledge_acl_entries")
    op.drop_constraint(
        "fk_knowledge_documents_current_version_id", "knowledge_documents", type_="foreignkey"
    )
    for name in (
        "ix_knowledge_versions_status",
        "ix_knowledge_versions_document_id",
        "ix_knowledge_versions_tenant_id",
    ):
        op.drop_index(name, table_name="knowledge_versions")
    op.drop_table("knowledge_versions")
    for name in (
        "ix_knowledge_documents_current_version_id",
        "ix_knowledge_documents_status",
        "ix_knowledge_documents_source_id",
        "ix_knowledge_documents_tenant_id",
    ):
        op.drop_index(name, table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    op.drop_index("ix_knowledge_sources_environment", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_tenant_id", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
