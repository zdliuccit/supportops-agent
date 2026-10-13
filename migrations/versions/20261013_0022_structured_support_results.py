"""助手消息保存完整 SupportResult，旧正文兼容为空。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261013_0022"
down_revision: str | None = "20261013_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("support_result", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True,
        comment="服务端校验的版本化 SupportResult；旧正文为空，不回填虚构证据。"))


def downgrade() -> None:
    op.drop_column("messages", "support_result")
