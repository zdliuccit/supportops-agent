"""保护固定凭据和知识证据历史；没有擦除入口，不扫描或修复既有内容。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261018_0049"
down_revision: str | None = "20261017_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("model_credential_revisions", "knowledge_evidence", "knowledge_chunks")


def upgrade() -> None:
    op.execute("""CREATE FUNCTION public.supportops_guard_reference_history()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'REFERENCE_HISTORY_DELETE_FORBIDDEN';
        END IF;
        IF NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW; END IF;
        IF TG_TABLE_NAME <> 'model_credential_revisions' THEN
            RAISE EXCEPTION 'REFERENCE_HISTORY_UPDATE_FORBIDDEN';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['deleted_at', 'revoked_at', 'revoked_by', 'revocation_reason'])
            IS DISTINCT FROM
            (to_jsonb(OLD) - ARRAY['deleted_at', 'revoked_at', 'revoked_by', 'revocation_reason']) THEN
            RAISE EXCEPTION 'CREDENTIAL_REVISION_IMMUTABLE';
        END IF;
        IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
            RAISE EXCEPTION 'CREDENTIAL_DELETION_IRREVERSIBLE';
        END IF;
        IF OLD.revoked_at IS NOT NULL THEN
            IF ROW(NEW.revoked_at, NEW.revoked_by, NEW.revocation_reason)
                IS DISTINCT FROM ROW(OLD.revoked_at, OLD.revoked_by, OLD.revocation_reason) THEN
                RAISE EXCEPTION 'CREDENTIAL_REVOCATION_IMMUTABLE';
            END IF;
        ELSIF NEW.revoked_at IS NULL THEN
            IF NEW.revoked_by IS NOT NULL OR NEW.revocation_reason IS NOT NULL THEN
                RAISE EXCEPTION 'CREDENTIAL_REVOCATION_INCOMPLETE';
            END IF;
        ELSIF NEW.revoked_by IS NULL OR NEW.revocation_reason IS NULL
            OR length(btrim(NEW.revocation_reason)) = 0 THEN
            RAISE EXCEPTION 'CREDENTIAL_REVOCATION_INCOMPLETE';
        END IF;
        RETURN NEW;
        END; $$""")
    op.execute("""CREATE FUNCTION public.supportops_reject_reference_history_truncate()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN RAISE EXCEPTION 'REFERENCE_HISTORY_TRUNCATE_FORBIDDEN'; END; $$""")
    # 名称来自固定目录，不接受运行时表名；普通调用者没有禁用保护的配置开关。
    for table in _TABLES:
        op.execute(f"""CREATE TRIGGER reference_history_guard BEFORE UPDATE OR DELETE
            ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.supportops_guard_reference_history()""")
        op.execute(f"""CREATE TRIGGER reference_history_truncate_guard BEFORE TRUNCATE
            ON public.{table} FOR EACH STATEMENT
            EXECUTE FUNCTION public.supportops_reject_reference_history_truncate()""")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER reference_history_guard")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER reference_history_truncate_guard")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.model_credential_revisions)
            OR EXISTS (SELECT 1 FROM public.knowledge_evidence)
            OR EXISTS (SELECT 1 FROM public.knowledge_chunks) THEN
            RAISE EXCEPTION 'REFERENCE_HISTORY_PREVENTS_DOWNGRADE';
        END IF;
        END; $$""")
    for table in reversed(_TABLES):
        op.execute(f"DROP TRIGGER reference_history_truncate_guard ON public.{table}")
        op.execute(f"DROP TRIGGER reference_history_guard ON public.{table}")
    op.execute("DROP FUNCTION public.supportops_reject_reference_history_truncate()")
    op.execute("DROP FUNCTION public.supportops_guard_reference_history()")
