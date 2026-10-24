"""专业知识子调用完整回执及可选密封回答；不启用执行器。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261022_0063"
down_revision = "20261020_0062"
branch_labels = None
depends_on = None

_ROW_BODY = """BEGIN
IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'SPECIALIST_EXECUTION_IMMUTABLE'; END IF;
IF NEW.payload->>'schema_version' IS DISTINCT FROM 'specialist_execution_record_v1'
  OR NEW.payload->>'call_id' IS DISTINCT FROM NEW.call_id::text
  OR NEW.payload->>'tenant_id' IS DISTINCT FROM NEW.tenant_id::text
  OR NEW.payload->>'run_id' IS DISTINCT FROM NEW.run_id::text
  OR NEW.payload->>'attempt_id' IS DISTINCT FROM NEW.attempt_id::text
  OR NEW.payload->>'generation' IS DISTINCT FROM NEW.generation::text
  OR (NEW.payload->>'recorded_at')::timestamptz IS DISTINCT FROM NEW.created_at
  OR octet_length(NEW.payload::text) > 16384
  OR octet_length(NEW.sealed_answer) > 524288
  OR (NEW.sealed_answer IS NULL) IS DISTINCT FROM (NEW.payload->>'ciphertext_digest' IS NULL)
  OR NOT EXISTS(SELECT 1 FROM public.model_call_ledger l JOIN public.run_attempts a ON a.id=l.attempt_id
    WHERE l.id=NEW.call_id AND l.tenant_id=NEW.tenant_id AND l.run_id=NEW.run_id AND l.attempt_id=NEW.attempt_id
      AND l.policy_version='specialist_knowledge_budget_v1' AND l.status<>'reserved'
      AND a.tenant_id=NEW.tenant_id AND a.run_id=NEW.run_id AND a.generation=NEW.generation)
THEN RAISE EXCEPTION 'SPECIALIST_EXECUTION_SCOPE_INVALID'; END IF;
RETURN NEW;
END;"""

_TRUNCATE_BODY = """BEGIN
RAISE EXCEPTION 'SPECIALIST_EXECUTION_IMMUTABLE';
END;"""


def upgrade() -> None:
    op.create_table("specialist_execution_observations",
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("model_call_ledger.id"), primary_key=True, comment="原物理调用；一次记录，不重派。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="原调用租户。"),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False, comment="原父Run。"),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("run_attempts.id"), nullable=False, comment="原物理调用尝试。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="原尝试围栏代次。"),
        sa.Column("record_digest", sa.String(64), nullable=False, comment="封闭元数据规范摘要。"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, comment="完整回执与密封正文定位，无回答明文。"),
        sa.Column("sealed_answer", sa.Text(), nullable=True, comment="脱敏回答的专用信封；缺失不表示原SDK没有回答。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="原记录首次持久化时间。"),
        sa.CheckConstraint("generation >= 1", name="ck_specialist_execution_generation"),
        comment="原call唯一追加的完整执行回执与可选脱敏密封回答；不授予运行许可。")
    op.create_index("ix_specialist_execution_tenant_run", "specialist_execution_observations", ["tenant_id", "run_id", "created_at"])
    op.execute("CREATE FUNCTION public.supportops_guard_specialist_execution() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $row$" + _ROW_BODY + "$row$")
    op.execute("CREATE FUNCTION public.supportops_reject_specialist_execution_truncate() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $truncate$" + _TRUNCATE_BODY + "$truncate$")
    op.execute("CREATE TRIGGER specialist_execution_history_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.specialist_execution_observations FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_specialist_execution()")
    op.execute("CREATE TRIGGER specialist_execution_truncate_guard BEFORE TRUNCATE "
        "ON public.specialist_execution_observations FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_reject_specialist_execution_truncate()")
    for trigger in ("specialist_execution_history_guard", "specialist_execution_truncate_guard"):
        op.execute(f"ALTER TABLE public.specialist_execution_observations ENABLE ALWAYS TRIGGER {trigger}")


def downgrade() -> None:
    op.execute("LOCK TABLE public.specialist_execution_observations IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.specialist_execution_observations) "
        "OR EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid='public.specialist_execution_observations'::regclass "
        "AND (relrowsecurity OR relforcerowsecurity)) THEN RAISE EXCEPTION 'SPECIALIST_EXECUTION_HISTORY_MUST_BE_PRESERVED'; "
        "END IF; END $$")
    op.drop_table("specialist_execution_observations")
    op.execute("DROP FUNCTION public.supportops_reject_specialist_execution_truncate()")
    op.execute("DROP FUNCTION public.supportops_guard_specialist_execution()")
