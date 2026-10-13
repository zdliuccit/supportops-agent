"""隔离目标系统，独立事务提供幂等与查询证明。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0030"
down_revision: str | None = "20261013_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("mock_remote_operations",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="模拟接收端操作。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="目标租户。"),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="可信资源主体。"),
        sa.Column("logical_key", sa.String(64), nullable=False, comment="目标端幂等键。"),
        sa.Column("parameters_digest", sa.String(64), nullable=False, comment="冻结参数摘要。"),
        sa.Column("execution_fence", sa.Integer(), nullable=False, comment="拒绝迟到旧 generation 的目标端证明。"),
        sa.Column("status", sa.String(20), nullable=False, comment="executed/not_executed/unknown。"),
        sa.Column("external_id", sa.String(200), nullable=True, comment="已创建目标 ID。"),
        sa.Column("fault_consumed", sa.Boolean(), nullable=False, comment="隔离一次性故障已消费。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="目标登记时间。"),
        sa.UniqueConstraint("tenant_id", "logical_key", name="uq_mock_remote_operation_key"),
        comment="仅 Mock 的独立幂等登记，真实目标须确认自身契约。")
    op.create_index("ix_mock_remote_operations_tenant_id", "mock_remote_operations", ["tenant_id"])
    op.create_table("mock_support_tickets",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="模拟工单 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="模拟租户。"),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="工单主体。"),
        sa.Column("project_id", sa.Uuid(), nullable=False, comment="派生隔离项目。"),
        sa.Column("logical_key", sa.String(64), nullable=False, comment="目标稳定业务键。"),
        sa.Column("external_id", sa.String(200), nullable=False, comment="MOCK 业务编号。"),
        sa.Column("title", sa.String(200), nullable=False, comment="受限标题。"),
        sa.Column("summary", sa.String(2000), nullable=False, comment="最小支持摘要。"),
        sa.Column("issue_key", sa.String(128), nullable=False, comment="可信业务意图。"),
        sa.Column("status", sa.String(20), nullable=False, comment="模拟工单状态。"),
        sa.Column("version", sa.Integer(), nullable=False, comment="状态版本。"),
        sa.Column("assigned_to", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True, comment="显式处理人。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="实际模拟创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="模拟状态更新时间。"),
        sa.UniqueConstraint("tenant_id", "logical_key", name="uq_mock_ticket_business_key"),
        sa.UniqueConstraint("tenant_id", "external_id", name="uq_mock_ticket_external_id"), comment="隔离模拟工单，不连接真实系统。")
    op.create_index("ix_mock_support_tickets_tenant_id", "mock_support_tickets", ["tenant_id"])
    op.create_index("ix_mock_support_tickets_owner_user_id", "mock_support_tickets", ["owner_user_id"])


def downgrade() -> None:
    op.drop_table("mock_support_tickets")
    op.drop_table("mock_remote_operations")
