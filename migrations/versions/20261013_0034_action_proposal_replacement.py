"""显式改参保留旧提案并重新审批，不改写历史参数。"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0034"
down_revision: str | None = "20261013_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("business_actions", sa.Column("supersedes_action_id", sa.Uuid(), nullable=True, comment="显式替代的未执行旧提案；历史参数保持不变。"))
    op.create_foreign_key("fk_business_action_supersedes", "business_actions", "business_actions", ["supersedes_action_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_business_action_supersedes", "business_actions", type_="foreignkey")
    op.drop_column("business_actions", "supersedes_action_id")
