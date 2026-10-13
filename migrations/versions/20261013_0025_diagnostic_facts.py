"""保存隔离诊断的受限事实，旧工具调用不回填虚构结果。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261013_0025"
down_revision: str | None = "20261013_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_invocations", sa.Column("diagnostic_result", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True,
        comment="隔离诊断白名单事实；不保存真实日志、密钥或 Prompt。"))


def downgrade() -> None:
    op.drop_column("tool_invocations", "diagnostic_result")
