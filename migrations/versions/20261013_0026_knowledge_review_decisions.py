"""分离审核提交与批准，不把旧提交伪造为审核通过。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0026"
down_revision: str | None = "20261013_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_versions", sa.Column("submitted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True, comment="提交审核人，与审核人分离。"))
    op.add_column("knowledge_versions", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True, comment="最近提交审核时间。"))
    op.add_column("knowledge_versions", sa.Column("review_decision", sa.String(16), nullable=True, comment="pending/approved/rejected；旧正式版本不伪造批准。"))
    op.add_column("knowledge_versions", sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0", comment="提交与审核乐观并发 revision。"))
    op.add_column("knowledge_versions", sa.Column("review_scope_digest", sa.String(64), nullable=True, comment="审核内容/标题/有效期/ACL 摘要。"))
    op.add_column("knowledge_versions", sa.Column("review_comment", sa.Text(), nullable=True, comment="受限审核说明。"))
    op.execute(sa.text("""UPDATE knowledge_versions SET submitted_by = reviewed_by, submitted_at = reviewed_at,
        reviewed_by = NULL, reviewed_at = NULL, review_decision = 'pending'
        WHERE lower(status) = 'in_review'"""))
    op.alter_column("knowledge_versions", "review_revision", server_default=None)


def downgrade() -> None:
    for name in ("review_comment", "review_scope_digest", "review_revision", "review_decision", "submitted_at", "submitted_by"):
        op.drop_column("knowledge_versions", name)
