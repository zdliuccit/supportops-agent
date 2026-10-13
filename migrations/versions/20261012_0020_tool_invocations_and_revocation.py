"""工具原子许可/计数/脱敏审计与独立紧急撤销。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0020"
down_revision: str | None = "20261012_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_catalog_entries", sa.Column("is_revoked", sa.Boolean(), nullable=False,
                  server_default=sa.false(), comment="独立紧急撤销；普通重新启用不会解除。"))
    op.alter_column("tool_catalog_entries", "is_enabled", existing_type=sa.Boolean(), existing_nullable=False,
                    comment="是否允许绑定及后续执行，包括历史 Agent 版本。")
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.Uuid(), nullable=False, comment="工具调用唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="工具调用所属租户。"),
        sa.Column("run_id", sa.Uuid(), nullable=False, comment="计数关联整个业务 Run。"),
        sa.Column("attempt_id", sa.Uuid(), nullable=False, comment="本次实际执行尝试。"),
        sa.Column("tool_call_id", sa.String(200), nullable=False, comment="模型调用标识，同 Run 不重复执行。"),
        sa.Column("tool_id", sa.String(100), nullable=False, comment="稳定工具标识。"),
        sa.Column("implementation_version", sa.Integer(), nullable=False, comment="服务端可信实现版本。"),
        sa.Column("catalog_version", sa.Integer(), nullable=False, comment="执行时目录策略版本。"),
        sa.Column("risk_level", sa.String(2), nullable=False, comment="服务端复算的 R0–R4 风险。"),
        sa.Column("arguments_digest", sa.String(64), nullable=False, comment="规范化参数 SHA-256；不保存原始参数。"),
        sa.Column("result_digest", sa.String(64), nullable=True, comment="过滤后结果摘要，不保存完整工具正文。"),
        sa.Column("status", sa.String(20), nullable=False, comment="running/succeeded/failed/denied/cancelled/unknown。"),
        sa.Column("error_code", sa.String(100), nullable=True, comment="稳定授权/额度/执行错误码。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="许可或拒绝创建时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="调用收敛时间，结果未知时显式标记。"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["run_attempts.id"]),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_tool_invocations_call"),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed', 'denied', 'cancelled', 'unknown')", name="ck_tool_invocation_status"),
        sa.CheckConstraint("risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')", name="ck_tool_invocation_risk"),
        comment="工具许可/限额/策略版本/脱敏结果审计；失败或取消的许可仍消耗次数。",
    )
    op.create_index("ix_tool_invocations_run_tool", "tool_invocations", ["tenant_id", "run_id", "tool_id", "status"])
    op.create_index("ix_tool_invocations_tenant_time", "tool_invocations", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_tenant_time", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_run_tool", table_name="tool_invocations")
    op.drop_table("tool_invocations")
    op.alter_column("tool_catalog_entries", "is_enabled", existing_type=sa.Boolean(), existing_nullable=False,
                    comment="是否允许新 Agent 绑定该工具。")
    op.drop_column("tool_catalog_entries", "is_revoked")
