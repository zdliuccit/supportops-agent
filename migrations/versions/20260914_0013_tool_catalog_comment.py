"""修正工具目录启用字段的数据字典描述。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260914_0013"
down_revision: str | None = "20260914_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "COMMENT ON COLUMN tool_catalog_entries.is_enabled IS '是否允许新 Agent 绑定该工具。'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN tool_catalog_entries.is_enabled IS '是否允许新 Agent 绑定。'")
