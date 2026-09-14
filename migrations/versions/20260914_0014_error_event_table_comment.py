"""补齐错误事件表的数据字典表级描述。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260914_0014"
down_revision: str | None = "20260914_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "COMMENT ON TABLE agent_error_events IS 'Agent 运行失败事件，保存脱敏上下文供管理员分析。'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON TABLE agent_error_events IS NULL")
