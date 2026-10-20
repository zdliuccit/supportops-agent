"""删除传播核对的独立追加审计；不传播删除或授予维护角色。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_0057"
down_revision = "20261019_0056"
branch_labels = None
depends_on = None

_BODY = "BEGIN RAISE EXCEPTION 'RETENTION_PROPAGATION_HISTORY_IMMUTABLE'; END;"


def upgrade() -> None:
    op.create_table("retention_propagation_events",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="核对审计随机事件ID。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="原事实租户，无业务外键。"),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False, comment="原事实主体，无个人资料。"),
        sa.Column("request_id", sa.Uuid(), nullable=False, comment="固定传播核对请求ID。"),
        sa.Column("request_digest", sa.String(64), nullable=False, comment="完整原请求摘要，不保存原定位。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="连续追加序号，最多18条。"),
        sa.Column("event_type", sa.String(24), nullable=False, comment="开始、逐项目标观察或结束。"),
        sa.Column("audit_digest", sa.String(64), nullable=False, comment="原封闭审计消息摘要。"),
        sa.Column("previous_digest", sa.String(64), nullable=True, comment="前持久记录摘要，首条为空。"),
        sa.Column("record_digest", sa.String(64), nullable=False, comment="含前摘要和实际记录时间的完整摘要。"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, comment="最小核对摘要与结果，无内容、目标地址或秘密。"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, comment="实际追加时间。"),
        sa.UniqueConstraint("request_id", "revision", name="uq_retention_propagation_revision"),
        sa.CheckConstraint("revision BETWEEN 1 AND 18", name="ck_retention_propagation_revision"),
        sa.CheckConstraint("event_type IN ('inspection_started','target_observed','inspection_finished')",
            name="ck_retention_propagation_event"),
        sa.CheckConstraint("length(CAST(payload AS TEXT)) <= 8192", name="ck_retention_propagation_payload"),
        comment="删除传播只读核对的独立追加审计，不证明实际擦除完成。")
    op.create_index("ix_retention_propagation_subject", "retention_propagation_events",
        ["tenant_id", "subject_user_id", "recorded_at", "id"])
    op.execute("CREATE FUNCTION public.supportops_guard_propagation_history() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $propagation_guard$" + _BODY + "$propagation_guard$")
    op.execute("CREATE TRIGGER retention_propagation_history_guard BEFORE UPDATE OR DELETE "
        "ON public.retention_propagation_events FOR EACH ROW "
        "EXECUTE FUNCTION public.supportops_guard_propagation_history()")
    op.execute("CREATE TRIGGER retention_propagation_truncate_guard BEFORE TRUNCATE "
        "ON public.retention_propagation_events FOR EACH STATEMENT "
        "EXECUTE FUNCTION public.supportops_guard_propagation_history()")
    for trigger in ("retention_propagation_history_guard", "retention_propagation_truncate_guard"):
        op.execute(f"ALTER TABLE public.retention_propagation_events ENABLE ALWAYS TRIGGER {trigger}")


def downgrade() -> None:
    op.execute("LOCK TABLE public.retention_propagation_events IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.retention_propagation_events) THEN "
        "RAISE EXCEPTION 'RETENTION_PROPAGATION_HISTORY_MUST_BE_PRESERVED'; END IF; END $$")
    op.drop_table("retention_propagation_events")
    op.execute("DROP FUNCTION public.supportops_guard_propagation_history()")
