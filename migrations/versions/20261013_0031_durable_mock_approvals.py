"""显式 Mock 资源人员授权、冻结审批与一次性消费。"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0031"
down_revision: str | None = "20261013_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("mock_resource_grants",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="授权 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="资源租户。"),
        sa.Column("project_id", sa.Uuid(), nullable=False, comment="服务器派生项目。"),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="资源主体。"),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="获准人员。"),
        sa.Column("permission", sa.String(32), nullable=False, comment="approve/ticket_handle。"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, comment="启停状态。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="授权版本。"),
        sa.Column("granted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="授权管理员。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="变更时间。"),
        sa.UniqueConstraint("tenant_id", "project_id", "user_id", "permission", name="uq_mock_resource_grant"),
        sa.CheckConstraint("permission IN ('approve', 'ticket_handle')", name="ck_mock_resource_permission"),
        comment="Mock 项目显式授权；不覆盖知识 ACL。")
    op.create_table("approval_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="申请 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="租户。"),
        sa.Column("action_id", sa.Uuid(), sa.ForeignKey("business_actions.id"), nullable=False, comment="逻辑动作。"),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False, comment="授权上下文。"),
        sa.Column("applicant_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="申请者。"),
        sa.Column("approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="指定审批人。"),
        sa.Column("tool_id", sa.String(100), nullable=False, comment="工具。"),
        sa.Column("implementation_version", sa.Integer(), nullable=False, comment="实现版本。"),
        sa.Column("catalog_version", sa.Integer(), nullable=False, comment="策略版本。"),
        sa.Column("parameters_digest", sa.String(64), nullable=False, comment="冻结参数摘要。"),
        sa.Column("scope_digest", sa.String(64), nullable=False, comment="完整批准范围摘要。"),
        sa.Column("status", sa.String(20), nullable=False, comment="审批状态。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="决策版本。"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="服务器期限。"),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False, comment="等待治理下次检查。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="申请时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="终止时间。"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled', 'invalidated', 'consumed')", name="ck_approval_request_status"),
        sa.CheckConstraint("applicant_id <> approver_id", name="ck_approval_nonself"),
        comment="副作用前的冻结审批范围和期限。")
    op.create_index("uq_approval_active_action", "approval_requests", ["action_id"], unique=True,
        postgresql_where=sa.text("status IN ('pending', 'approved')"))
    op.create_table("approval_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="决定 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="租户。"),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("approval_requests.id"), nullable=False, comment="申请。"),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="审批人。"),
        sa.Column("decision", sa.String(20), nullable=False, comment="approved/rejected。"),
        sa.Column("reason", sa.String(1000), nullable=False, comment="脱敏理由。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="决定时间。"),
        sa.UniqueConstraint("request_id", name="uq_approval_decision_request"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_approval_decision"), comment="追加独立审批决定。")
    op.create_table("approval_consumptions",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="消费 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="租户。"),
        sa.Column("request_id", sa.Uuid(), sa.ForeignKey("approval_requests.id"), nullable=False, comment="批准申请。"),
        sa.Column("action_id", sa.Uuid(), sa.ForeignKey("business_actions.id"), nullable=False, comment="逻辑动作。"),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("action_attempts.id"), nullable=False, comment="唯一出站许可。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="消费 generation。"),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False, comment="消费时间。"),
        sa.UniqueConstraint("request_id", name="uq_approval_consumption_request"),
        sa.UniqueConstraint("action_id", name="uq_approval_consumption_action"), comment="一次性消费与执行许可原子提交。")
    for table, columns in {
        "mock_resource_grants": ("tenant_id", "project_id", "user_id"),
        "approval_requests": ("tenant_id", "action_id", "applicant_id", "approver_id"),
        "approval_decisions": ("tenant_id",), "approval_consumptions": ("tenant_id",),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("approval_consumptions", "approval_decisions", "approval_requests", "mock_resource_grants"):
        op.drop_table(table)
