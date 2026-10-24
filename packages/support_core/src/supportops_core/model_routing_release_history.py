"""v3候选历史的具名PG保护核对；不认证完整数据库权限或授予发布许可。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.model_routing_policy_services import RoutingPolicyConflict

# 与0062的不可变DDL逐字对应；迁移不导入会随应用升级的源码。
ROW_GUARD_BODY = """BEGIN
IF TG_OP = 'INSERT' THEN
  IF NEW.schema_version = '3' AND (
    NEW.config->>'schema_version' IS DISTINCT FROM '3'
    OR NEW.release_manifest->>'schema_version' IS DISTINCT FROM 'agent_release_manifest_v2'
    OR NEW.release_manifest->>'agent_id' IS DISTINCT FROM NEW.agent_id::text
    OR NEW.release_manifest->>'agent_version_id' IS DISTINCT FROM NEW.id::text
    OR NEW.release_manifest->>'config_digest' IS DISTINCT FROM NEW.config_digest
    OR NEW.release_manifest#>>'{model,model_version_id}' IS DISTINCT FROM NEW.model_endpoint_version_id::text
    OR NEW.release_manifest#>>'{candidate_origin,candidate_only}' IS DISTINCT FROM 'true'
    OR NEW.release_manifest#>>'{candidate_origin,runtime_supported_at_publication}' IS DISTINCT FROM 'false'
    OR NEW.withdrawn_at IS NOT NULL OR NEW.withdrawn_by IS NOT NULL
  ) THEN RAISE EXCEPTION 'V3_RELEASE_SHAPE_INVALID'; END IF;
  RETURN NEW;
END IF;
IF TG_OP = 'DELETE' THEN
  IF OLD.schema_version = '3' THEN RAISE EXCEPTION 'V3_RELEASE_HISTORY_IMMUTABLE'; END IF;
  RETURN OLD;
END IF;
IF OLD.schema_version <> '3' AND NEW.schema_version = '3' THEN
  RAISE EXCEPTION 'V3_RELEASE_REQUIRES_NEW_VERSION';
END IF;
IF OLD.schema_version = '3' THEN
  IF (to_jsonb(NEW) - 'withdrawn_at' - 'withdrawn_by') IS DISTINCT FROM
     (to_jsonb(OLD) - 'withdrawn_at' - 'withdrawn_by') THEN
    RAISE EXCEPTION 'V3_RELEASE_HISTORY_IMMUTABLE';
  END IF;
  IF ROW(NEW.withdrawn_at, NEW.withdrawn_by) IS DISTINCT FROM ROW(OLD.withdrawn_at, OLD.withdrawn_by) AND (
    OLD.withdrawn_at IS NOT NULL OR OLD.withdrawn_by IS NOT NULL
    OR NEW.withdrawn_at IS NULL OR NEW.withdrawn_by IS NULL
  ) THEN RAISE EXCEPTION 'V3_RELEASE_WITHDRAWAL_IMMUTABLE'; END IF;
END IF;
RETURN NEW;
END;"""

TRUNCATE_GUARD_BODY = """BEGIN
IF EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid = TG_RELID AND (relrowsecurity OR relforcerowsecurity))
  OR EXISTS(SELECT 1 FROM public.agent_versions WHERE schema_version = '3') THEN
  RAISE EXCEPTION 'V3_RELEASE_HISTORY_MUST_BE_PRESERVED';
END IF;
RETURN NULL;
END;"""


async def assert_v3_release_history_guard(session: AsyncSession) -> None:
    if session.get_bind().dialect.name != "postgresql":
        raise RoutingPolicyConflict("ROUTING_RELEASE_POSTGRES_REQUIRED")
    rows = (await session.execute(text("""
        SELECT t.tgname, t.tgenabled, t.tgtype, t.tgisinternal, t.tgnargs, t.tgqual IS NULL AS no_condition,
            p.proname, p.prosrc, p.prosecdef, p.proconfig, l.lanname,
            c.relrowsecurity, c.relforcerowsecurity
        FROM pg_catalog.pg_trigger t
        JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
        JOIN pg_catalog.pg_namespace pn ON pn.oid = p.pronamespace
        JOIN pg_catalog.pg_language l ON l.oid = p.prolang
        WHERE n.nspname = 'public' AND c.relname = 'agent_versions' AND pn.nspname = 'public'
            AND t.tgname IN ('v3_release_history_guard', 'v3_release_truncate_guard')
    """))).mappings().all()
    expected = {
        "v3_release_history_guard": (31, "supportops_guard_v3_release_history", ROW_GUARD_BODY),
        "v3_release_truncate_guard": (34, "supportops_reject_v3_release_truncate", TRUNCATE_GUARD_BODY),
    }
    if len(rows) != 2 or {row["tgname"] for row in rows} != set(expected):
        raise RoutingPolicyConflict("ROUTING_RELEASE_HISTORY_GUARD_UNAVAILABLE")
    for row in rows:
        kind, function, body = expected[row["tgname"]]
        if (row["tgenabled"] != "A" or row["tgtype"] != kind or row["tgisinternal"] or row["tgnargs"] != 0
            or not row["no_condition"] or row["proname"] != function or row["prosrc"] != body
            or row["prosecdef"] or row["proconfig"] != ["search_path=pg_catalog"]
            or row["lanname"] != "plpgsql" or row["relrowsecurity"] or row["relforcerowsecurity"]):
            raise RoutingPolicyConflict("ROUTING_RELEASE_HISTORY_GUARD_UNAVAILABLE")
