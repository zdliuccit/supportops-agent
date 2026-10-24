"""保护v3候选版本与清单历史；不启用v3运行、不改写v2快照。"""

from alembic import op

revision = "20261020_0062"
down_revision = "20261020_0061"
branch_labels = None
depends_on = None

_ROW_BODY = """BEGIN
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

_TRUNCATE_BODY = """BEGIN
IF EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid = TG_RELID AND (relrowsecurity OR relforcerowsecurity))
  OR EXISTS(SELECT 1 FROM public.agent_versions WHERE schema_version = '3') THEN
  RAISE EXCEPTION 'V3_RELEASE_HISTORY_MUST_BE_PRESERVED';
END IF;
RETURN NULL;
END;"""


def upgrade() -> None:
    op.execute("CREATE FUNCTION public.supportops_guard_v3_release_history() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $v3_row$" + _ROW_BODY + "$v3_row$")
    op.execute("CREATE FUNCTION public.supportops_reject_v3_release_truncate() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $v3_truncate$" + _TRUNCATE_BODY + "$v3_truncate$")
    op.execute("CREATE TRIGGER v3_release_history_guard BEFORE INSERT OR UPDATE OR DELETE ON public.agent_versions "
        "FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_v3_release_history()")
    op.execute("CREATE TRIGGER v3_release_truncate_guard BEFORE TRUNCATE ON public.agent_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_reject_v3_release_truncate()")
    for trigger in ("v3_release_history_guard", "v3_release_truncate_guard"):
        op.execute(f"ALTER TABLE public.agent_versions ENABLE ALWAYS TRIGGER {trigger}")


def downgrade() -> None:
    op.execute("LOCK TABLE public.agent_versions IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_catalog.pg_class "
        "WHERE oid = 'public.agent_versions'::regclass AND (relrowsecurity OR relforcerowsecurity)) "
        "OR EXISTS(SELECT 1 FROM public.agent_versions WHERE schema_version = '3' "
        "OR release_manifest->>'schema_version' = 'agent_release_manifest_v2') THEN "
        "RAISE EXCEPTION 'V3_RELEASE_HISTORY_MUST_BE_PRESERVED'; END IF; END $$")
    op.execute("DROP TRIGGER v3_release_truncate_guard ON public.agent_versions")
    op.execute("DROP TRIGGER v3_release_history_guard ON public.agent_versions")
    op.execute("DROP FUNCTION public.supportops_reject_v3_release_truncate()")
    op.execute("DROP FUNCTION public.supportops_guard_v3_release_history()")
