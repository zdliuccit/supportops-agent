"""0063具名追加保护的只读核对；不把函数存在当作完整DB身份隔离证明。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ROW_BODY = """BEGIN
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

TRUNCATE_BODY = """BEGIN
RAISE EXCEPTION 'SPECIALIST_EXECUTION_IMMUTABLE';
END;"""


async def assert_specialist_execution_history(session: AsyncSession) -> None:
    if session.get_bind().dialect.name != "postgresql":
        raise ValueError("SPECIALIST_EXECUTION_POSTGRES_REQUIRED")
    rows = (await session.execute(text("""
        SELECT t.tgname,t.tgenabled,t.tgtype,t.tgisinternal,t.tgnargs,t.tgqual IS NULL AS no_condition,
            p.proname,p.prosrc,p.prosecdef,p.proconfig,l.lanname,c.relrowsecurity,c.relforcerowsecurity
        FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE n.nspname='public' AND c.relname='specialist_execution_observations' AND pn.nspname='public'
            AND t.tgname IN ('specialist_execution_history_guard','specialist_execution_truncate_guard')
    """))).mappings().all()
    expected = {
        "specialist_execution_history_guard": (31, "supportops_guard_specialist_execution", ROW_BODY),
        "specialist_execution_truncate_guard": (34, "supportops_reject_specialist_execution_truncate", TRUNCATE_BODY),
    }
    if len(rows) != 2 or {row["tgname"] for row in rows} != set(expected):
        raise ValueError("SPECIALIST_EXECUTION_HISTORY_GUARD_UNAVAILABLE")
    for row in rows:
        kind, function, body = expected[row["tgname"]]
        if (row["tgenabled"] != "A" or row["tgtype"] != kind or row["tgisinternal"] or row["tgnargs"] != 0
            or not row["no_condition"] or row["proname"] != function or row["prosrc"] != body
            or row["prosecdef"] or row["proconfig"] != ["search_path=pg_catalog"]
            or row["lanname"] != "plpgsql" or row["relrowsecurity"] or row["relforcerowsecurity"]):
            raise ValueError("SPECIALIST_EXECUTION_HISTORY_GUARD_UNAVAILABLE")
