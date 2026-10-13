"""发布快照固定本地工具实现版本，历史配置与 digest 保持原样。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261013_0024"
down_revision: str | None = "20261013_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_versions", sa.Column("resolved_tool_versions", sa.JSON().with_variant(JSONB(), "postgresql"),
        nullable=False, server_default=sa.text("'{}'"), comment="发布时固定的服务端工具实现版本，不随目录元数据升级。"))
    # 仅回填已存在且有明确 v1 实现的本地工具；不把未知工具推断为当前版本。
    historical_v1 = {"current_identity_summary", "support_ticket_lookup", "search_knowledge"}
    versions = sa.table("agent_versions", sa.column("id", sa.Uuid()), sa.column("resolved_tool_ids", sa.JSON()),
                        sa.column("resolved_tool_versions", sa.JSON()))
    connection = op.get_bind()
    for version_id, tool_ids in connection.execute(sa.select(versions.c.id, versions.c.resolved_tool_ids)):
        pinned = {tool_id: 1 for tool_id in (tool_ids or []) if tool_id in historical_v1}
        connection.execute(versions.update().where(versions.c.id == version_id).values(resolved_tool_versions=pinned))


def downgrade() -> None:
    op.drop_column("agent_versions", "resolved_tool_versions")
