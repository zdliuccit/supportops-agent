"""独立运行告警和有界扫描，不回填虚构历史告警或确认。"""
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "20261014_0039"
down_revision: str | None = "20261013_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("runtime_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="告警 ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="告警租户。"),
        sa.Column("rule_code", sa.String(64), nullable=False, comment="固定规则标识。"),
        sa.Column("scope_kind", sa.String(16), nullable=False, comment="run 或 action。"),
        sa.Column("scope_id", sa.Uuid(), nullable=False, comment="来源业务标识，不作为执行或授权许可。"),
        sa.Column("run_id", sa.Uuid(), nullable=True, comment="来源 Run 的诊断引用。"),
        sa.Column("severity", sa.String(16), nullable=False, comment="告警级别。"),
        sa.Column("is_active", sa.Boolean(), nullable=False, comment="当前是否命中规则。"),
        sa.Column("source_generation", sa.Integer(), nullable=False, comment="来源 generation。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="确认并发版本。"),
        sa.Column("metadata", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False, comment="白名单元数据，不含秘密/正文。"),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False, comment="首次观测时间。"),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False, comment="最近成功观测时间。"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True, comment="规则不再命中时间。"),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True, comment="管理员已阅时间。"),
        sa.Column("acknowledged_by", sa.Uuid(), sa.ForeignKey("users.id", name="fk_runtime_alert_ack_user"), nullable=True, comment="实际确认管理员。"),
        sa.UniqueConstraint("tenant_id", "rule_code", "scope_kind", "scope_id", name="uq_runtime_alert_scope"),
        sa.CheckConstraint("scope_kind IN ('run', 'action')", name="ck_runtime_alert_scope"),
        sa.CheckConstraint("severity IN ('info', 'warning', 'critical')", name="ck_runtime_alert_severity"),
        comment="独立运行告警，不修改业务状态、不发送外部消息。")
    op.create_index("ix_runtime_alerts_tenant_active_observed", "runtime_alerts", ["tenant_id", "is_active", "last_observed_at"])
    op.create_table("runtime_alert_scan_states",
        sa.Column("source_kind", sa.String(16), primary_key=True, comment="run 或 action。"),
        sa.Column("last_source_id", sa.Uuid(), nullable=True, comment="本轮最后 UUID，完整轮转后重新检查。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="最近扫描成功时间。"),
        sa.CheckConstraint("source_kind IN ('run', 'action')", name="ck_runtime_alert_scan_kind"),
        comment="独立观测的共享扫描游标，不承担业务许可。")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM runtime_alerts) THEN
            RAISE EXCEPTION 'runtime alert history must be preserved before downgrade';
        END IF;
    END $$""")
    op.drop_table("runtime_alert_scan_states")
    op.drop_table("runtime_alerts")
