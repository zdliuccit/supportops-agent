"""显式客服接管与原子 Mock 命令，不回填虚构历史接管。"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0035"
down_revision: str | None = "20261013_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("approval_requests", sa.Column("resource_revision", sa.Integer(), nullable=True, comment="冻结的 Mock 问题版本；旧未知范围不可直接执行。"))
    op.create_table("mock_support_cases",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="问题 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="问题租户。"),
        sa.Column("project_id", sa.Uuid(), nullable=False, comment="服务器派生项目。"),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="问题主体。"),
        sa.Column("issue_key", sa.String(128), nullable=False, comment="稳定问题键。"),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=True, comment="服务端来源会话。"),
        sa.Column("handler_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True, comment="明确接管人员。"),
        sa.Column("is_active", sa.Boolean(), nullable=False, comment="接管时冻结 Agent 写。"),
        sa.Column("version", sa.Integer(), nullable=False, comment="接管版本。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="变化时间。"),
        sa.UniqueConstraint("tenant_id", "project_id", "issue_key", name="uq_mock_support_case"), comment="客服与 Agent 共用问题边界。")
    op.create_table("mock_ticket_commands",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="命令 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="租户。"),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("mock_support_tickets.id"), nullable=False, comment="目标工单。"),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="实际人员。"),
        sa.Column("idempotency_key", sa.String(200), nullable=False, comment="人工幂等键。"),
        sa.Column("request_digest", sa.String(64), nullable=False, comment="种类/脱敏正文/原版本摘要。"),
        sa.Column("kind", sa.String(20), nullable=False, comment="受限操作。"),
        sa.Column("body", sa.String(1000), nullable=False, comment="受限脱敏正文，备注仅客服可读。"),
        sa.Column("ticket_version", sa.Integer(), nullable=False, comment="提交后目标版本。"),
        sa.Column("ticket_status", sa.String(20), nullable=False, comment="提交后目标状态。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="实际操作时间。"),
        sa.UniqueConstraint("ticket_id", "idempotency_key", name="uq_mock_ticket_command"),
        sa.CheckConstraint("kind IN ('claim', 'note', 'reply', 'close', 'escalate', 'release')", name="ck_mock_ticket_command_kind"),
        comment="人工 Mock 操作与目标状态同事务的幂等账本。")
    for table, columns in {"mock_support_cases": ("tenant_id", "conversation_id"), "mock_ticket_commands": ("tenant_id", "ticket_id")}.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("mock_ticket_commands")
    op.drop_table("mock_support_cases")
    op.drop_column("approval_requests", "resource_revision")
