"""动作/尝试/回执独立于模型 Run，不回填虚构执行事实。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261013_0029"
down_revision: str | None = "20261013_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    op.create_table("business_actions",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="稳定业务动作 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="动作租户。"),
        sa.Column("proposed_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="可信提案主体。"),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False, comment="原始业务 Run。"),
        sa.Column("agent_version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id"), nullable=False, comment="固定 Agent 版本。"),
        sa.Column("tool_id", sa.String(100), nullable=False, comment="动作工具 ID。"),
        sa.Column("implementation_version", sa.Integer(), nullable=False, comment="固定实现版本。"),
        sa.Column("catalog_version", sa.Integer(), nullable=False, comment="冻结目录策略版本。"),
        sa.Column("execution_catalog_version", sa.Integer(), nullable=True, comment="提交执行意图复检的策略版本。"),
        sa.Column("execution_run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=True, comment="重新授权的执行上下文。"),
        sa.Column("execution_agent_version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id"), nullable=True, comment="执行意图的固定 Agent 版本。"),
        sa.Column("logical_key", sa.String(64), nullable=False, comment="主体/资源/业务意图 SHA-256。"),
        sa.Column("parameters", json_type, nullable=False, comment="严格 Schema 冻结参数。"),
        sa.Column("parameters_digest", sa.String(64), nullable=False, comment="规范化参数 SHA-256。"),
        sa.Column("parameter_schema_version", sa.String(32), nullable=False, comment="参数 Schema 版本。"),
        sa.Column("resource_kind", sa.String(64), nullable=False, comment="可信资源类别。"),
        sa.Column("resource_id", sa.String(200), nullable=False, comment="可信资源身份。"),
        sa.Column("risk_level", sa.String(2), nullable=False, comment="R2–R4。"),
        sa.Column("approval_required", sa.Boolean(), nullable=False, comment="默认要求审批。"),
        sa.Column("status", sa.String(32), nullable=False, comment="独立动作生命周期。"),
        sa.Column("current_attempt_id", sa.Uuid(), nullable=True, comment="当前动作尝试。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="动作 fencing generation。"),
        sa.Column("reconciliation_count", sa.Integer(), nullable=False, comment="累计对账次数。"),
        sa.Column("reconciliation_limit", sa.Integer(), nullable=False, comment="当前有限对账许可，不清零累计次数。"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, comment="受限调度时间。"),
        sa.Column("external_id", sa.String(200), nullable=True, comment="可信外部业务 ID。"),
        sa.Column("error_code", sa.String(100), nullable=True, comment="安全原因码。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="原提案时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="确认终态时间。"),
        sa.UniqueConstraint("tenant_id", "tool_id", "logical_key", name="uq_business_action_key"),
        sa.CheckConstraint("risk_level IN ('R2', 'R3', 'R4')", name="ck_business_action_risk"),
        sa.CheckConstraint("status IN ('proposed', 'waiting_approval', 'queued', 'running', 'unknown', 'reconciling', 'succeeded', 'failed', 'needs_attention', 'cancelled')", name="ck_business_action_status"),
        comment="跨 Run 的稳定业务动作与冻结参数账本。")
    op.create_index("ix_business_actions_tenant_id", "business_actions", ["tenant_id"])
    op.create_index("ix_business_actions_run_id", "business_actions", ["run_id"])
    op.create_index("ix_business_action_due", "business_actions", ["status", "next_attempt_at"])
    op.create_table("action_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="动作尝试 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="尝试租户。"),
        sa.Column("action_id", sa.Uuid(), sa.ForeignKey("business_actions.id"), nullable=False, comment="稳定动作。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="唯一许可 generation。"),
        sa.Column("kind", sa.String(20), nullable=False, comment="execute/reconcile/verify。"),
        sa.Column("owner_id", sa.String(200), nullable=False, comment="当前 owner。"),
        sa.Column("status", sa.String(20), nullable=False, comment="尝试/未知/隔离状态。"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False, comment="独立动作租约。"),
        sa.Column("parameters_digest", sa.String(64), nullable=False, comment="冻结参数摘要。"),
        sa.Column("error_code", sa.String(100), nullable=True, comment="安全原因码。"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, comment="实际领取时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="尝试收敛时间。"),
        sa.UniqueConstraint("action_id", "generation", name="uq_action_attempt_generation"),
        sa.CheckConstraint("kind IN ('execute', 'reconcile', 'verify')", name="ck_action_attempt_kind"),
        comment="执行/对账许可，与模型 RunAttempt 分开。")
    op.create_index("ix_action_attempts_tenant_id", "action_attempts", ["tenant_id"])
    op.create_index("ix_action_attempts_action_id", "action_attempts", ["action_id"])
    op.create_foreign_key("fk_business_action_attempt", "business_actions", "action_attempts", ["current_attempt_id"], ["id"])
    op.create_table("action_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="追加回执 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="回执租户。"),
        sa.Column("action_id", sa.Uuid(), sa.ForeignKey("business_actions.id"), nullable=False, comment="原逻辑动作。"),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("action_attempts.id"), nullable=False, comment="实际提供证明的尝试。"),
        sa.Column("connector_version", sa.String(64), nullable=False, comment="可信连接器版本。"),
        sa.Column("external_id", sa.String(200), nullable=True, comment="已知外部 ID。"),
        sa.Column("status", sa.String(32), nullable=False, comment="submitted/verified/conflict/not_executed/unknown。"),
        sa.Column("result_digest", sa.String(64), nullable=False, comment="白名单结果摘要。"),
        sa.Column("verified_fields", json_type, nullable=False, comment="只读查询获准的最小字段。"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, comment="实际响应/查询时间。"),
        comment="追加回执和独立验证，不保存原始外部响应。")
    op.create_index("ix_action_receipts_tenant_id", "action_receipts", ["tenant_id"])
    op.create_index("ix_action_receipts_action_id", "action_receipts", ["action_id"])
    op.add_column("tool_invocations", sa.Column("business_action_id", sa.Uuid(), nullable=True, comment="提案/动作关联，不以工具成功冒充业务执行。"))
    op.create_foreign_key("fk_tool_invocation_action", "tool_invocations", "business_actions", ["business_action_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_tool_invocation_action", "tool_invocations", type_="foreignkey")
    op.drop_column("tool_invocations", "business_action_id")
    op.drop_table("action_receipts")
    op.drop_constraint("fk_business_action_attempt", "business_actions", type_="foreignkey")
    op.drop_table("action_attempts")
    op.drop_table("business_actions")
