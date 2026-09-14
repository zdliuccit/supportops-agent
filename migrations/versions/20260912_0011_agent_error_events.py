"""增加 Agent 错误事件分析表。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0011"
down_revision: str | None = "20260911_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_error_events",
        sa.Column("id", sa.Uuid(), nullable=False, comment="错误事件唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="事件所属租户。"),
        sa.Column("run_id", sa.Uuid(), nullable=False, comment="关联的 Agent Run。"),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, comment="关联会话。"),
        sa.Column("agent_id", sa.Uuid(), nullable=False, comment="失败的 Agent。"),
        sa.Column("agent_version_id", sa.Uuid(), nullable=False, comment="运行固定的 Agent 版本。"),
        sa.Column(
            "model_endpoint_version_id", sa.Uuid(), nullable=False, comment="运行固定的模型版本。"
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="受影响的用户。"),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, comment="错误发生时间。"
        ),
        sa.Column(
            "severity",
            sa.String(length=16),
            nullable=False,
            comment="info、warning、error 或 critical。",
        ),
        sa.Column("error_code", sa.String(length=100), nullable=False, comment="稳定脱敏错误码。"),
        sa.Column(
            "reason", sa.String(length=1000), nullable=False, comment="面向管理员的脱敏错误原因。"
        ),
        sa.Column("stage", sa.String(length=64), nullable=False, comment="发生错误的运行阶段。"),
        sa.Column(
            "resolution_status",
            sa.String(length=32),
            nullable=False,
            comment="unresolved、acknowledged 或 resolved。",
        ),
        sa.Column("retry_count", sa.Integer(), nullable=True, comment="失败前已发生的重试次数。"),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True, comment="失败时累计端到端耗时。"),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="白名单脱敏运行上下文。",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, comment="事件写入时间。"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["agent_version_id"], ["agent_versions.id"]),
        sa.ForeignKeyConstraint(["model_endpoint_version_id"], ["model_endpoint_versions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_agent_error_events_tenant_id", ["tenant_id"]),
        ("ix_agent_error_events_run_id", ["run_id"]),
        ("ix_agent_error_events_conversation_id", ["conversation_id"]),
        ("ix_agent_error_events_agent_id", ["agent_id"]),
        ("ix_agent_error_events_agent_version_id", ["agent_version_id"]),
        ("ix_agent_error_events_model_endpoint_version_id", ["model_endpoint_version_id"]),
        ("ix_agent_error_events_user_id", ["user_id"]),
        ("ix_agent_error_events_occurred_at", ["occurred_at"]),
        ("ix_agent_error_events_severity", ["severity"]),
        ("ix_agent_error_events_error_code", ["error_code"]),
        ("ix_agent_error_events_resolution_status", ["resolution_status"]),
        ("ix_agent_error_events_tenant_occurred", ["tenant_id", "occurred_at"]),
        ("ix_agent_error_events_agent_occurred", ["agent_id", "occurred_at"]),
        (
            "ix_agent_error_events_tenant_severity_occurred",
            ["tenant_id", "severity", "occurred_at"],
        ),
        ("ix_agent_error_events_tenant_code_occurred", ["tenant_id", "error_code", "occurred_at"]),
    ):
        op.create_index(name, "agent_error_events", columns)


def downgrade() -> None:
    for name in (
        "ix_agent_error_events_tenant_code_occurred",
        "ix_agent_error_events_tenant_severity_occurred",
        "ix_agent_error_events_agent_occurred",
        "ix_agent_error_events_tenant_occurred",
        "ix_agent_error_events_resolution_status",
        "ix_agent_error_events_error_code",
        "ix_agent_error_events_severity",
        "ix_agent_error_events_occurred_at",
        "ix_agent_error_events_user_id",
        "ix_agent_error_events_model_endpoint_version_id",
        "ix_agent_error_events_agent_version_id",
        "ix_agent_error_events_agent_id",
        "ix_agent_error_events_conversation_id",
        "ix_agent_error_events_run_id",
        "ix_agent_error_events_tenant_id",
    ):
        op.drop_index(name, table_name="agent_error_events")
    op.drop_table("agent_error_events")
