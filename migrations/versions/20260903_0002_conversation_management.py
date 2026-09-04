"""添加会话置顶能力。

Revision ID: 20260903_0002
Revises: 20260902_0001
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0002"
down_revision: str | None = "20260902_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index(
        "ix_conversations_tenant_user_pinned_updated",
        "conversations",
        ["tenant_id", "user_id", "is_pinned", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_tenant_user_pinned_updated", table_name="conversations")
    op.drop_column("conversations", "is_pinned")
