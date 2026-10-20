"""恢复会话隔离的单向撤销及历史保护，不创建事实或改写旧会话。"""

from alembic import op

revision = "20261019_0055"
down_revision = "20261019_0054"
branch_labels = None
depends_on = None

# 保持历史迁移自包含；运行时0055专项核对使用此版本的固定函数体。
_BODY = """
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


def upgrade() -> None:
    op.execute("CREATE FUNCTION public.supportops_guard_auth_session_history() RETURNS trigger "
        "LANGUAGE plpgsql SET search_path=pg_catalog AS $auth_session_guard$" + _BODY + "$auth_session_guard$")
    op.execute("CREATE TRIGGER auth_session_history_guard BEFORE UPDATE OR DELETE ON public.auth_sessions "
        "FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_auth_session_history()")
    op.execute("CREATE TRIGGER auth_session_history_truncate_guard BEFORE TRUNCATE ON public.auth_sessions "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_guard_auth_session_history()")
    op.execute("ALTER TABLE public.auth_sessions ENABLE ALWAYS TRIGGER auth_session_history_guard")
    op.execute("ALTER TABLE public.auth_sessions ENABLE ALWAYS TRIGGER auth_session_history_truncate_guard")


def downgrade() -> None:
    op.execute("LOCK TABLE public.auth_sessions IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.auth_sessions) THEN "
        "RAISE EXCEPTION 'AUTH_SESSION_HISTORY_MUST_BE_PRESERVED'; END IF; END $$")
    op.execute("DROP TRIGGER auth_session_history_guard ON public.auth_sessions")
    op.execute("DROP TRIGGER auth_session_history_truncate_guard ON public.auth_sessions")
    op.execute("DROP FUNCTION public.supportops_guard_auth_session_history()")
