"""Run 业务等待的索引/状态说明；不改写旧终态或虚构审批历史。"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0032"
down_revision: str | None = "20261013_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # RunStatus 使用 VARCHAR 非原生 Enum，数据库保存枚举名称；旧数据保持原值。
    op.alter_column("agent_runs", "status", existing_type=sa.String(32), existing_nullable=False,
        comment="queued/running/cancelling/waiting_approval/waiting_action/completed/failed/cancelled；业务等待不占 Worker。")
    op.create_index("ix_agent_runs_business_wait", "agent_runs", ["tenant_id", "conversation_id", "id"],
        postgresql_where=sa.text("status IN ('WAITING_APPROVAL', 'WAITING_ACTION')"))


def downgrade() -> None:
    # 旧代码无法读取新状态；拒绝把活跃等待伪装为完成或抹掉批准。
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM agent_runs WHERE status IN ('WAITING_APPROVAL', 'WAITING_ACTION')) THEN
            RAISE EXCEPTION 'active business waits must be resolved before downgrade';
        END IF;
    END $$""")
    op.drop_index("ix_agent_runs_business_wait", table_name="agent_runs")
