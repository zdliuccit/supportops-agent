"""保存有界排查升级包，不回填虚构历史事实。"""
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "20261013_0033"
down_revision: str | None = "20261013_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("ticket_escalation_packages",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="升级包 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="所属租户。"),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="原资源主体。"),
        sa.Column("action_id", sa.Uuid(), sa.ForeignKey("business_actions.id"), nullable=False, comment="原业务动作。"),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False, comment="本次排查来源。"),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("run_attempts.id"), nullable=True, comment="实际排查尝试。"),
        sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False, comment="有界升级包。"),
        sa.Column("payload_digest", sa.String(64), nullable=False, comment="规范化包摘要。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="保存时间。"),
        sa.UniqueConstraint("action_id", "run_id", name="uq_ticket_escalation_source"), comment="支持排查事实与未确认事项，独立于目标工单。")
    for column in ("tenant_id", "action_id", "run_id"):
        op.create_index("ix_ticket_escalation_packages_" + column, "ticket_escalation_packages", [column])


def downgrade() -> None:
    op.drop_table("ticket_escalation_packages")
