"""同步知识生命周期表的列注释。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260914_0016"
down_revision: str | None = "20260914_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COMMENTS = (
    ("knowledge_sources", "id", "知识来源唯一标识。"),
    ("knowledge_sources", "tenant_id", "来源所属租户。"),
    ("knowledge_sources", "external_key", "来源稳定外部标识。"),
    ("knowledge_sources", "name", "来源显示名称。"),
    ("knowledge_sources", "source_type", "来源类型，如 manual、mock 或 cms。"),
    ("knowledge_sources", "environment", "来源环境；mock 不得发布到 production。"),
    ("knowledge_sources", "owner_user_id", "来源负责人。"),
    ("knowledge_sources", "metadata", "来源治理元数据。"),
    ("knowledge_sources", "created_by", "创建人。"),
    ("knowledge_sources", "created_at", "来源创建时间。"),
    ("knowledge_sources", "updated_at", "来源最近更新时间。"),
    ("knowledge_documents", "id", "知识文档唯一标识。"),
    ("knowledge_documents", "tenant_id", "文档所属租户。"),
    ("knowledge_documents", "source_id", "文档来源。"),
    ("knowledge_documents", "external_key", "来源内稳定文档标识。"),
    ("knowledge_documents", "owner_user_id", "文档负责人。"),
    ("knowledge_documents", "status", "文档当前治理状态。"),
    ("knowledge_documents", "current_version_id", "当前已发布版本。"),
    ("knowledge_documents", "review_due_at", "下次复核时间。"),
    ("knowledge_documents", "created_by", "文档创建人。"),
    ("knowledge_documents", "created_at", "文档创建时间。"),
    ("knowledge_documents", "updated_at", "文档最近更新时间。"),
    ("knowledge_versions", "id", "知识版本唯一标识。"),
    ("knowledge_versions", "tenant_id", "版本所属租户。"),
    ("knowledge_versions", "document_id", "所属文档。"),
    ("knowledge_versions", "version_number", "文档内单调递增版本号。"),
    ("knowledge_versions", "status", "知识版本生命周期状态。"),
    ("knowledge_versions", "title", "知识标题。"),
    ("knowledge_versions", "content_markdown", "UTF-8 Markdown 正文。"),
    ("knowledge_versions", "content_digest", "规范化内容 SHA-256。"),
    ("knowledge_versions", "section_anchors", "确定性章节定位锚点。"),
    ("knowledge_versions", "change_summary", "版本变更说明。"),
    ("knowledge_versions", "effective_from", "版本生效时间。"),
    ("knowledge_versions", "effective_until", "版本失效时间。"),
    ("knowledge_versions", "created_by", "版本创建人。"),
    ("knowledge_versions", "reviewed_by", "审核人。"),
    ("knowledge_versions", "published_by", "发布人。"),
    ("knowledge_versions", "created_at", "版本创建时间。"),
    ("knowledge_versions", "reviewed_at", "审核时间。"),
    ("knowledge_versions", "published_at", "发布时间。"),
    ("knowledge_acl_entries", "id", "ACL 记录唯一标识。"),
    ("knowledge_acl_entries", "tenant_id", "ACL 所属租户。"),
    ("knowledge_acl_entries", "document_id", "受保护文档。"),
    ("knowledge_acl_entries", "subject_type", "允许主体类型。"),
    ("knowledge_acl_entries", "subject_id", "角色、组织单元或用户标识。"),
    ("knowledge_acl_entries", "created_by", "ACL 创建人。"),
    ("knowledge_acl_entries", "created_at", "ACL 创建时间。"),
    ("knowledge_snapshots", "id", "知识快照唯一标识。"),
    ("knowledge_snapshots", "tenant_id", "快照所属租户。"),
    ("knowledge_snapshots", "document_id", "快照文档。"),
    ("knowledge_snapshots", "version_id", "快照版本。"),
    ("knowledge_snapshots", "content_digest", "内容 SHA-256。"),
    ("knowledge_snapshots", "title", "快照标题。"),
    ("knowledge_snapshots", "section_anchors", "快照章节定位。"),
    ("knowledge_snapshots", "acl_digest", "发布时 ACL 摘要。"),
    ("knowledge_snapshots", "effective_from", "快照生效时间。"),
    ("knowledge_snapshots", "effective_until", "快照失效时间。"),
    ("knowledge_snapshots", "published_at", "快照发布时间。"),
    ("knowledge_snapshots", "created_at", "快照创建时间。"),
    ("knowledge_outbox_events", "id", "事件唯一标识。"),
    ("knowledge_outbox_events", "tenant_id", "事件所属租户。"),
    ("knowledge_outbox_events", "event_key", "租户内幂等事件键。"),
    ("knowledge_outbox_events", "event_type", "知识生命周期事件类型。"),
    ("knowledge_outbox_events", "document_id", "关联文档。"),
    ("knowledge_outbox_events", "version_id", "关联版本。"),
    ("knowledge_outbox_events", "payload", "事件脱敏载荷。"),
    ("knowledge_outbox_events", "attempts", "已尝试消费次数。"),
    ("knowledge_outbox_events", "available_at", "下次可消费时间。"),
    ("knowledge_outbox_events", "processed_at", "成功消费时间。"),
    ("knowledge_outbox_events", "created_at", "事件创建时间。"),
)

_BASELINE_COMMENTS = {
    ("knowledge_sources", "id"): "知识来源唯一标识。",
    ("knowledge_sources", "tenant_id"): "来源所属租户。",
    ("knowledge_sources", "external_key"): "来源稳定外部标识。",
    ("knowledge_sources", "name"): "来源显示名称。",
    ("knowledge_sources", "source_type"): "来源类型。",
    ("knowledge_sources", "environment"): "来源环境。",
    ("knowledge_sources", "owner_user_id"): "来源负责人。",
    ("knowledge_sources", "metadata"): "来源治理元数据。",
    ("knowledge_documents", "id"): "知识文档唯一标识。",
    ("knowledge_documents", "external_key"): "来源内稳定文档标识。",
}


def upgrade() -> None:
    for table, column, comment in _COMMENTS:
        op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{comment}';")


def downgrade() -> None:
    for table, column, _comment in _COMMENTS:
        baseline = _BASELINE_COMMENTS.get((table, column))
        if baseline is None:
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS NULL;")
        else:
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{baseline}';")
