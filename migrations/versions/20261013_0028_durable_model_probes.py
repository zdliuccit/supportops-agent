"""持久化模型探测；升级前须停止旧 API BackgroundTasks 执行者。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261013_0028"
down_revision: str | None = "20261013_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("model_probe_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="持久化探测任务。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="任务租户。"),
        sa.Column("test_run_id", sa.Uuid(), sa.ForeignKey("model_endpoint_test_runs.id"), nullable=False, comment="一对一产品探测记录。"),
        sa.Column("endpoint_id", sa.Uuid(), sa.ForeignKey("model_endpoints.id"), nullable=False, comment="固定端点。"),
        sa.Column("endpoint_model_id", sa.Uuid(), sa.ForeignKey("model_endpoint_models.id"), nullable=False, comment="稳定模型。"),
        sa.Column("model_version_id", sa.Uuid(), sa.ForeignKey("model_endpoint_versions.id"), nullable=False, comment="固定版本。"),
        sa.Column("credential_revision", sa.Integer(), nullable=False, comment="固定凭据 revision。"),
        sa.Column("status", sa.String(20), nullable=False, comment="queued/running/succeeded/failed/cancelled。"),
        sa.Column("stage", sa.String(32), nullable=False, comment="当前探测阶段。"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, comment="累计领取次数。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="写入 fencing generation。"),
        sa.Column("owner_id", sa.String(200), nullable=True, comment="当前 owner。"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True, comment="独立租约。"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, comment="有限退避时间。"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, comment="持久化取消意图。"),
        sa.Column("error_code", sa.String(100), nullable=True, comment="安全错误码。"),
        sa.Column("attempt_history", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False, comment="有界尝试摘要。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="创建时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="终态时间。"),
        sa.UniqueConstraint("test_run_id", name="uq_model_probe_test_run"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_model_probe_status"),
        sa.CheckConstraint("attempt_count >= 0 AND generation >= 0", name="ck_model_probe_counters"),
        comment="持久化端点探测，不使用聊天 Run/checkpoint。")
    op.create_index("ix_model_probe_jobs_tenant_id", "model_probe_jobs", ["tenant_id"])
    op.create_index("ix_model_probe_due", "model_probe_jobs", ["status", "next_attempt_at"])
    # 历史进行中没有 owner/fencing，不能伪造执行次数或沿用未知执行者。
    op.execute(sa.text("""UPDATE model_endpoint_test_runs SET status = 'FAILED', stage = 'failed',
        error_code = 'LEGACY_MODEL_PROBE_INTERRUPTED', error_message = '旧探测缺少持久化执行许可，升级后请重新发起',
        completed_at = CURRENT_TIMESTAMP WHERE lower(status) IN ('queued', 'running')"""))
    op.create_index("uq_model_probe_active_model", "model_probe_jobs", ["endpoint_model_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"), sqlite_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_table("model_probe_jobs")
