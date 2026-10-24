"""0064追加保护的具名核对；不替代独立数据库身份和DDL权限隔离。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ROW_BODY = """DECLARE
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

TRUNCATE_BODY = """BEGIN
RAISE EXCEPTION 'ROUTING_CIRCUIT_IMMUTABLE';
END;"""


async def assert_routing_circuit_history(session: AsyncSession) -> None:
    if session.get_bind().dialect.name != "postgresql":
        raise ValueError("ROUTING_CIRCUIT_POSTGRES_REQUIRED")
    rows = (await session.execute(text("""
        SELECT t.tgname,t.tgenabled,t.tgtype,t.tgisinternal,t.tgnargs,t.tgqual IS NULL AS no_condition,
            p.proname,p.prosrc,p.prosecdef,p.proconfig,l.lanname,c.relrowsecurity,c.relforcerowsecurity
        FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE n.nspname='public' AND c.relname='model_routing_circuit_events' AND pn.nspname='public'
            AND t.tgname IN ('routing_circuit_history_guard','routing_circuit_truncate_guard')
    """))).mappings().all()
    expected = {
        "routing_circuit_history_guard": (31, "supportops_guard_routing_circuit", ROW_BODY),
        "routing_circuit_truncate_guard": (34, "supportops_reject_routing_circuit_truncate", TRUNCATE_BODY),
    }
    if len(rows) != 2 or {row["tgname"] for row in rows} != set(expected):
        raise ValueError("ROUTING_CIRCUIT_HISTORY_GUARD_UNAVAILABLE")
    for row in rows:
        kind, function, body = expected[row["tgname"]]
        if (row["tgenabled"] != "A" or row["tgtype"] != kind or row["tgisinternal"] or row["tgnargs"] != 0
            or not row["no_condition"] or row["proname"] != function or row["prosrc"] != body
            or row["prosecdef"] or row["proconfig"] != ["search_path=pg_catalog"]
            or row["lanname"] != "plpgsql" or row["relrowsecurity"] or row["relforcerowsecurity"]):
            raise ValueError("ROUTING_CIRCUIT_HISTORY_GUARD_UNAVAILABLE")
