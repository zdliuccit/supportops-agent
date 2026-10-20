"""恢复会话隔离要求的0055单向撤销保护；只读核对固定函数与两触发器。"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

AUTH_SESSION_GUARD_BODY = """
BEGIN
  IF TG_OP IN ('DELETE','TRUNCATE') THEN
    RAISE EXCEPTION 'AUTH_SESSION_HISTORY_IMMUTABLE';
  END IF;
  IF (to_jsonb(NEW)-ARRAY['revoked_at','revoked_by','revocation_code']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['revoked_at','revoked_by','revocation_code']) THEN
    RAISE EXCEPTION 'AUTH_SESSION_SCOPE_IMMUTABLE';
  END IF;
  IF OLD.revoked_at IS NOT NULL THEN
    IF ROW(NEW.revoked_at,NEW.revoked_by,NEW.revocation_code) IS DISTINCT FROM
       ROW(OLD.revoked_at,OLD.revoked_by,OLD.revocation_code) THEN
      RAISE EXCEPTION 'AUTH_SESSION_REVOCATION_IMMUTABLE';
    END IF;
  ELSIF OLD.revoked_by IS NOT NULL OR OLD.revocation_code IS NOT NULL OR
        (NEW.revoked_at IS NULL AND (NEW.revoked_by IS NOT NULL OR NEW.revocation_code IS NOT NULL)) OR
        (NEW.revoked_at IS NOT NULL AND (NEW.revoked_at<OLD.created_at OR
         NEW.revocation_code IS NULL OR NEW.revocation_code!~'^[A-Z][A-Z0-9_]{0,31}$')) THEN
    RAISE EXCEPTION 'AUTH_SESSION_REVOCATION_INVALID';
  END IF;
  RETURN NEW;
END;
"""


async def require_restore_session_history_guard(connection: AsyncConnection) -> None:
    rows = (await connection.execute(text("""
        SELECT p.oid AS function_id,p.prosrc,p.prosecdef,p.proconfig,p.pronargs,
            p.prorettype='pg_catalog.trigger'::regtype AS returns_trigger,l.lanname,
            p.proowner=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user) AS owns_function,
            t.tgname,t.tgtype,t.tgenabled,t.tgisinternal,t.tgdeferrable,t.tginitdeferred,
            t.tgconstraint,t.tgnargs,t.tgargs,t.tgattr::text AS column_filter,t.tgqual IS NULL AS unconditional
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        JOIN pg_catalog.pg_trigger t ON t.tgfoid=p.oid
        JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE pn.nspname='public' AND p.proname='supportops_guard_auth_session_history'
            AND n.nspname='public' AND c.relname='auth_sessions'
            AND t.tgname IN ('auth_session_history_guard','auth_session_history_truncate_guard')
        LIMIT 3
    """))).mappings().all()
    expected = {"auth_session_history_guard": 27, "auth_session_history_truncate_guard": 34}
    if len(rows) != 2 or {row["tgname"] for row in rows} != set(expected) or len({row["function_id"] for row in rows}) != 1:
        raise ValueError()
    for row in rows:
        if (not isinstance(row["prosrc"], str) or len(row["prosrc"]) > 8192
            or row["prosrc"].strip() != AUTH_SESSION_GUARD_BODY.strip() or row["prosecdef"]
            or list(row["proconfig"] or []) != ["search_path=pg_catalog"] or row["pronargs"] != 0
            or not row["returns_trigger"] or row["lanname"] != "plpgsql" or row["owns_function"]
            or row["tgtype"] != expected[row["tgname"]] or row["tgenabled"] != "A" or row["tgisinternal"]
            or row["tgdeferrable"] or row["tginitdeferred"] or row["tgconstraint"] != 0
            or row["tgnargs"] != 0 or bytes(row["tgargs"]) != b"" or row["column_filter"] != ""
            or not row["unconditional"]):
            raise ValueError()
