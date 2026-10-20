"""独立清理批次的追加账本及其删除事实保护；不执行清理或生成历史。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261019_0056"
down_revision = "20261019_0055"
branch_labels = None
depends_on = None

_BODY = "BEGIN RAISE EXCEPTION 'RETENTION_CLEANUP_HISTORY_IMMUTABLE'; END;"


def upgrade() -> None:
    op.create_table("retention_cleanup_events",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="事件随机ID。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="原租户，无业务外键。"),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False, comment="原主体，无个人资料。"),
        sa.Column("job_id", sa.Uuid(), nullable=False, comment="可信维护作业ID。"),
        sa.Column("batch_id", sa.Uuid(), nullable=False, comment="精确资源批次ID。"),
        sa.Column("batch_sequence", sa.Integer(), nullable=False, comment="作业内连续序号。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="追加事件序号，最多32。"),
        sa.Column("state", sa.String(24), nullable=False, comment="事件当时状态。"),
        sa.Column("plan_digest", sa.String(64), nullable=False, comment="固定计划摘要。"),
        sa.Column("event_digest", sa.String(64), nullable=False, comment="完整事件SHA256。"),
        sa.Column("previous_digest", sa.String(64), nullable=True, comment="前事件摘要，首条为空。"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, comment="封闭维护元数据，无正文或秘密。"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, comment="实际追加时间。"),
        sa.UniqueConstraint("batch_id", "revision", name="uq_retention_cleanup_revision"),
        sa.CheckConstraint("revision BETWEEN 1 AND 32 AND batch_sequence BETWEEN 1 AND 1000000", name="ck_retention_cleanup_revision"),
        sa.CheckConstraint("state IN ('claimed','dispatched','verifying','completed','not_executed','needs_attention')", name="ck_retention_cleanup_state"),
        sa.CheckConstraint("length(CAST(payload AS TEXT)) <= 131072", name="ck_retention_cleanup_payload"),
        comment="独立维护批次的追加账本，不授予实际清理许可。")
    op.create_index("uq_retention_cleanup_sequence", "retention_cleanup_events",
        ["tenant_id", "subject_user_id", "job_id", "batch_sequence"], unique=True,
        postgresql_where=sa.text("revision = 1"))
    op.create_index("ix_retention_cleanup_subject", "retention_cleanup_events",
        ["tenant_id", "subject_user_id", "batch_id", "revision"])
    op.execute("CREATE FUNCTION public.supportops_guard_cleanup_history() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $cleanup_guard$" + _BODY + "$cleanup_guard$")
    for table, prefix in (("retention_cleanup_events", "retention_cleanup"), ("deletion_tombstones", "cleanup_tombstone")):
        op.execute(f"CREATE TRIGGER {prefix}_history_guard BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_cleanup_history()")
        op.execute(f"CREATE TRIGGER {prefix}_truncate_guard BEFORE TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_guard_cleanup_history()")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {prefix}_history_guard")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {prefix}_truncate_guard")


def downgrade() -> None:
    op.execute("LOCK TABLE public.retention_cleanup_events, public.deletion_tombstones IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.retention_cleanup_events) OR "
        "EXISTS(SELECT 1 FROM public.deletion_tombstones) THEN "
        "RAISE EXCEPTION 'RETENTION_CLEANUP_HISTORY_MUST_BE_PRESERVED'; END IF; END $$")
    for table, prefix in (("retention_cleanup_events", "retention_cleanup"), ("deletion_tombstones", "cleanup_tombstone")):
        op.execute(f"DROP TRIGGER {prefix}_history_guard ON public.{table}")
        op.execute(f"DROP TRIGGER {prefix}_truncate_guard ON public.{table}")
    op.drop_table("retention_cleanup_events")
    op.execute("DROP FUNCTION public.supportops_guard_cleanup_history()")
