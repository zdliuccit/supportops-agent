"""跨 Worker 的数据库租户轮转游标。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0027"
down_revision: str | None = "20261013_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("run_scheduler_states",
        sa.Column("queue_name", sa.String(200), primary_key=True, comment="当前数据库通知队列名称。"),
        sa.Column("last_tenant_id", sa.Uuid(), nullable=True, comment="轮转游标，不绑定已删除租户外键。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="最近轮转时间。"),
        comment="持久化租户轮转，不是服务或执行租约。")


def downgrade() -> None:
    op.drop_table("run_scheduler_states")
