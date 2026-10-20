"""恢复知识隔离的唯一意图与追加审计；不执行重施、创建角色或开放恢复环境。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_0058"
down_revision = "20261020_0057"
branch_labels = None
depends_on = None

_BODY = "BEGIN RAISE EXCEPTION 'RESTORE_KNOWLEDGE_HISTORY_IMMUTABLE'; END;"


def upgrade() -> None:
    op.create_table("restore_knowledge_events",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="恢复审计随机事件ID。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="原事实租户，无业务外键。"),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False, comment="原事实主体，无个人资料。"),
        sa.Column("restore_id", sa.Uuid(), nullable=False, comment="原恢复操作ID。"),
        sa.Column("request_id", sa.Uuid(), nullable=False, comment="原固定重施请求ID，不可重新领取。"),
        sa.Column("request_digest", sa.String(64), nullable=False, comment="完整恢复请求摘要。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="连续追加序号，最多8条。"),
        sa.Column("event_type", sa.String(32), nullable=False, comment="唯一意图、提交观察或只读结果。"),
        sa.Column("audit_digest", sa.String(64), nullable=False, comment="原最小审计消息摘要。"),
        sa.Column("previous_digest", sa.String(64), nullable=True, comment="前持久记录摘要；首条为空。"),
        sa.Column("record_digest", sa.String(64), nullable=False, comment="原主体、事件、序号及前摘要链。"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, comment="最小恢复证明，无定位、正文、角色或秘密。"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, comment="实际追加时间。"),
        sa.UniqueConstraint("request_id", "revision", name="uq_restore_knowledge_revision"),
        sa.CheckConstraint("revision BETWEEN 1 AND 8", name="ck_restore_knowledge_revision"),
        sa.CheckConstraint("event_type IN ('quarantine_requested','commit_observed','quarantine_verified',"
            "'target_not_observed','quarantine_not_confirmed')", name="ck_restore_knowledge_event"),
        sa.CheckConstraint("length(CAST(payload AS TEXT)) <= 8192", name="ck_restore_knowledge_payload"),
        comment="恢复知识隔离的唯一意图和最小追加审计，不代表擦除或恢复开放。")
    op.create_index("ix_restore_knowledge_subject", "restore_knowledge_events",
        ["tenant_id", "subject_user_id", "restore_id", "recorded_at", "id"])
    op.execute("CREATE FUNCTION public.supportops_guard_restore_knowledge_history() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $restore_knowledge_guard$" + _BODY + "$restore_knowledge_guard$")
    op.execute("CREATE TRIGGER restore_knowledge_history_guard BEFORE UPDATE OR DELETE "
        "ON public.restore_knowledge_events FOR EACH ROW "
        "EXECUTE FUNCTION public.supportops_guard_restore_knowledge_history()")
    op.execute("CREATE TRIGGER restore_knowledge_truncate_guard BEFORE TRUNCATE "
        "ON public.restore_knowledge_events FOR EACH STATEMENT "
        "EXECUTE FUNCTION public.supportops_guard_restore_knowledge_history()")
    for trigger in ("restore_knowledge_history_guard", "restore_knowledge_truncate_guard"):
        op.execute(f"ALTER TABLE public.restore_knowledge_events ENABLE ALWAYS TRIGGER {trigger}")


def downgrade() -> None:
    op.execute("LOCK TABLE public.restore_knowledge_events IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.restore_knowledge_events) THEN "
        "RAISE EXCEPTION 'RESTORE_KNOWLEDGE_HISTORY_MUST_BE_PRESERVED'; END IF; END $$")
    op.drop_table("restore_knowledge_events")
    op.execute("DROP FUNCTION public.supportops_guard_restore_knowledge_history()")
