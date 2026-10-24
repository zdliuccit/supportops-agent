"""持久路由熔断历史与恢复唯一领取；不启用模型路由。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261022_0064"
down_revision = "20261022_0063"
branch_labels = None
depends_on = None

_ROW_BODY = """DECLARE
history_count integer;
history_max integer;
previous_row public.model_routing_circuit_events%ROWTYPE;
BEGIN
IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_IMMUTABLE'; END IF;
PERFORM 1 FROM public.tenants WHERE id=NEW.tenant_id FOR UPDATE;
IF NOT FOUND THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_SCOPE_INVALID'; END IF;
IF NEW.payload->>'schema_version' IS DISTINCT FROM 'routing_circuit_stored_event_v1'
  OR NEW.payload->'scope'->>'tenant_id' IS DISTINCT FROM NEW.tenant_id::text
  OR NEW.payload->'scope'->>'policy_version_id' IS DISTINCT FROM NEW.policy_version_id::text
  OR NEW.payload->'scope'->'candidate'->>'candidate_id' IS DISTINCT FROM NEW.candidate_id::text
  OR NEW.payload->>'run_id' IS DISTINCT FROM NEW.run_id::text
  OR NEW.payload->'event'->>'event_id' IS DISTINCT FROM NEW.id::text
  OR NEW.payload->'event'->>'scope_digest' IS DISTINCT FROM NEW.scope_digest
  OR NEW.payload->'event'->>'call_id' IS DISTINCT FROM NEW.call_id::text
  OR NEW.payload->'event'->>'kind' IS DISTINCT FROM NEW.kind
  OR NEW.payload->'event'->>'sequence' IS DISTINCT FROM NEW.sequence::text
  OR NEW.payload->'event'->>'previous_event_digest' IS DISTINCT FROM NEW.previous_event_digest
  OR (NEW.payload->'event'->>'recorded_at')::timestamptz IS DISTINCT FROM NEW.created_at
  OR octet_length(NEW.payload::text) > 32768
  OR NOT EXISTS(SELECT 1 FROM public.model_routing_policy_versions p
    WHERE p.id=NEW.policy_version_id AND p.tenant_id=NEW.tenant_id
      AND p.policy_digest=NEW.payload->'scope'->>'policy_digest'
      AND p.payload->'definition'->>'fallback_policy_digest'=NEW.payload->'scope'->>'controls_digest'
      AND EXISTS(SELECT 1 FROM jsonb_array_elements(p.payload->'definition'->'candidates') c
        WHERE c=NEW.payload->'scope'->'candidate'))
  OR NOT EXISTS(SELECT 1 FROM public.model_call_ledger l WHERE l.id=NEW.call_id
    AND l.tenant_id=NEW.tenant_id AND l.run_id=NEW.run_id AND l.policy_version='routing_priority_v1')
THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_SCOPE_INVALID'; END IF;
IF EXISTS(SELECT 1 FROM public.model_routing_circuit_events WHERE tenant_id=NEW.tenant_id
    AND call_id=NEW.call_id AND kind=NEW.kind)
THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_CALL_ALREADY_RECORDED'; END IF;
SELECT count(*), coalesce(max(sequence),0) INTO history_count,history_max
  FROM public.model_routing_circuit_events WHERE scope_digest=NEW.scope_digest;
IF history_count<>history_max OR NEW.sequence<>history_count+1 OR history_count>=256
THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_CHAIN_INVALID'; END IF;
IF history_count=0 THEN
  IF NEW.previous_event_digest IS NOT NULL THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_CHAIN_INVALID'; END IF;
ELSE
  SELECT * INTO previous_row FROM public.model_routing_circuit_events
    WHERE scope_digest=NEW.scope_digest ORDER BY sequence DESC LIMIT 1;
  IF NEW.previous_event_digest IS DISTINCT FROM previous_row.event_digest
    OR NEW.payload->'scope' IS DISTINCT FROM previous_row.payload->'scope'
    OR NEW.payload->'controls' IS DISTINCT FROM previous_row.payload->'controls'
    OR NEW.created_at<previous_row.created_at
  THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_CHAIN_INVALID'; END IF;
END IF;
IF NEW.kind='observation' THEN
  IF NOT EXISTS(SELECT 1 FROM public.run_events e WHERE e.tenant_id=NEW.tenant_id
    AND e.run_id=NEW.run_id AND e.event_type='budget.model_routing_accounted'
    AND e.data->>'call_id'=NEW.call_id::text
    AND e.data->>'routing_accounting_digest'=NEW.payload->>'source_accounting_digest'
    AND e.data->'routing_accounting'->'receipt'=NEW.payload->'event'->'receipt'
    AND ((e.data->'routing_accounting'->'receipt'->'usage'='null'::jsonb
        AND NEW.payload->'event'->>'accounting_digest' IS NULL
        AND NEW.payload->'event'->>'accounting_within_bounds' IS NULL)
      OR (e.data->'routing_accounting'->'receipt'->'usage'<>'null'::jsonb
        AND NEW.payload->'event'->>'accounting_digest'=NEW.payload->>'source_accounting_digest'
        AND (NEW.payload->'event'->>'accounting_within_bounds')::boolean IS NOT DISTINCT FROM
          (e.data->'routing_accounting'->>'status'='settled'
            AND (e.data->'routing_accounting'->>'reservation_exceeded')::boolean=false
            AND e.data->'routing_accounting'->>'error_code' IS NULL))))
  THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_ACCOUNTING_MISSING'; END IF;
ELSE
  IF NOT EXISTS(SELECT 1 FROM public.model_call_ledger l WHERE l.id=NEW.call_id AND l.status='reserved')
    OR NOT EXISTS(SELECT 1 FROM public.model_routing_policy_versions p
      WHERE p.id=NEW.policy_version_id AND p.valid_until>NEW.created_at
        AND NOT EXISTS(SELECT 1 FROM public.model_routing_policy_revocations r WHERE r.policy_version_id=p.id))
  THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_RECOVERY_ALREADY_CONSUMED'; END IF;
END IF;
RETURN NEW;
END;"""

_TRUNCATE_BODY = """BEGIN
RAISE EXCEPTION 'ROUTING_CIRCUIT_IMMUTABLE';
END;"""


def upgrade() -> None:
    op.create_table("model_routing_circuit_events",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="原操作固定ID；未知提交仅查原ID。"),
        sa.Column("scope_digest", sa.String(64), nullable=False, comment="租户/原策略/完整候选/控制摘要。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="原调用租户。"),
        sa.Column("policy_version_id", sa.Uuid(), sa.ForeignKey("model_routing_policy_versions.id"), nullable=False, comment="不可变原策略。"),
        sa.Column("candidate_id", sa.Uuid(), nullable=False, comment="原完整候选ID。"),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False, comment="产生观察或领取的原Run。"),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("model_call_ledger.id"), nullable=False, comment="原物理预留调用。"),
        sa.Column("sequence", sa.Integer(), nullable=False, comment="同范围从1连续递增，最多256条。"),
        sa.Column("previous_event_digest", sa.String(64), nullable=True, comment="直接前条事件摘要，首条为空。"),
        sa.Column("kind", sa.String(30), nullable=False, comment="observation或recovery_claimed。"),
        sa.Column("event_digest", sa.String(64), nullable=False, comment="事件规范摘要，供后条引用。"),
        sa.Column("record_digest", sa.String(64), nullable=False, comment="完整范围/控制/事件/批准摘要。"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, comment="封闭元数据，无模型正文或凭据。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="首次持久化时间。"),
        sa.UniqueConstraint("scope_digest", "sequence", name="uq_routing_circuit_sequence"),
        sa.UniqueConstraint("tenant_id", "call_id", "kind", name="uq_routing_circuit_call_kind"),
        sa.CheckConstraint("sequence BETWEEN 1 AND 256", name="ck_routing_circuit_sequence"),
        sa.CheckConstraint("kind IN ('observation','recovery_claimed')", name="ck_routing_circuit_kind"),
        sa.CheckConstraint("(sequence=1) = (previous_event_digest IS NULL)", name="ck_routing_circuit_previous"),
        comment="不可变熔断观察与恢复领取历史；领取不授予模型派发许可。")
    op.create_index("ix_routing_circuit_tenant_run", "model_routing_circuit_events", ["tenant_id", "run_id", "created_at"])
    op.execute("CREATE FUNCTION public.supportops_guard_routing_circuit() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $row$" + _ROW_BODY + "$row$")
    op.execute("CREATE FUNCTION public.supportops_reject_routing_circuit_truncate() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $truncate$" + _TRUNCATE_BODY + "$truncate$")
    op.execute("CREATE TRIGGER routing_circuit_history_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON public.model_routing_circuit_events FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_routing_circuit()")
    op.execute("CREATE TRIGGER routing_circuit_truncate_guard BEFORE TRUNCATE "
        "ON public.model_routing_circuit_events FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_reject_routing_circuit_truncate()")
    for trigger in ("routing_circuit_history_guard", "routing_circuit_truncate_guard"):
        op.execute(f"ALTER TABLE public.model_routing_circuit_events ENABLE ALWAYS TRIGGER {trigger}")


def downgrade() -> None:
    op.execute("LOCK TABLE public.model_routing_circuit_events IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.model_routing_circuit_events) "
        "OR EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid='public.model_routing_circuit_events'::regclass "
        "AND (relrowsecurity OR relforcerowsecurity)) THEN RAISE EXCEPTION 'ROUTING_CIRCUIT_HISTORY_MUST_BE_PRESERVED'; "
        "END IF; END $$")
    op.drop_table("model_routing_circuit_events")
    op.execute("DROP FUNCTION public.supportops_reject_routing_circuit_truncate()")
    op.execute("DROP FUNCTION public.supportops_guard_routing_circuit()")
