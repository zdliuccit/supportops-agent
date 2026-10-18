"""保护证据上游的版本原文、发布快照和索引代次定位；不执行擦除。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261018_0050"
down_revision: str | None = "20261018_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("knowledge_versions", "knowledge_snapshots", "knowledge_index_generations")


def upgrade() -> None:
    op.execute("""CREATE FUNCTION public.supportops_guard_knowledge_reference_chain()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE mutable_fields text[];
        BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'KNOWLEDGE_REFERENCE_DELETE_FORBIDDEN';
        END IF;
        IF NEW IS NOT DISTINCT FROM OLD THEN RETURN NEW; END IF;
        IF TG_TABLE_NAME = 'knowledge_versions' THEN
            mutable_fields := ARRAY['status', 'effective_until', 'deleted_at',
                'submitted_by', 'submitted_at', 'reviewed_by', 'reviewed_at',
                'review_decision', 'review_revision', 'review_scope_digest', 'review_comment',
                'published_by', 'published_at'];
        ELSIF TG_TABLE_NAME = 'knowledge_snapshots' THEN
            mutable_fields := ARRAY['deleted_at'];
        ELSIF TG_TABLE_NAME = 'knowledge_index_generations' THEN
            mutable_fields := ARRAY['status', 'chunk_count', 'error_code', 'ready_at'];
        ELSE
            RAISE EXCEPTION 'KNOWLEDGE_REFERENCE_TABLE_UNSUPPORTED';
        END IF;
        IF (to_jsonb(NEW) - mutable_fields) IS DISTINCT FROM
            (to_jsonb(OLD) - mutable_fields) THEN
            RAISE EXCEPTION 'KNOWLEDGE_REFERENCE_CONTENT_IMMUTABLE';
        END IF;
        IF TG_TABLE_NAME IN ('knowledge_versions', 'knowledge_snapshots') THEN
            IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
                RAISE EXCEPTION 'KNOWLEDGE_REFERENCE_DELETION_IRREVERSIBLE';
            END IF;
        END IF;
        IF TG_TABLE_NAME = 'knowledge_versions' THEN
            IF ROW(NEW.published_at, NEW.published_by) IS DISTINCT FROM
                ROW(OLD.published_at, OLD.published_by) THEN
                IF OLD.published_at IS NOT NULL OR OLD.published_by IS NOT NULL
                    OR NEW.published_at IS NULL OR NEW.published_by IS NULL
                    OR NEW.status IS DISTINCT FROM 'PUBLISHED' THEN
                    RAISE EXCEPTION 'KNOWLEDGE_PUBLICATION_IMMUTABLE';
                END IF;
            END IF;
            IF NEW.effective_until IS DISTINCT FROM OLD.effective_until THEN
                IF OLD.published_at IS NULL OR NEW.status IS DISTINCT FROM 'RETIRED'
                    OR NEW.effective_until IS NULL
                    OR (OLD.effective_until IS NOT NULL AND NEW.effective_until > OLD.effective_until) THEN
                    RAISE EXCEPTION 'KNOWLEDGE_VALIDITY_EXTENSION_FORBIDDEN';
                END IF;
            END IF;
        END IF;
        RETURN NEW;
        END; $$""")
    # 复用0049固定的TRUNCATE拒绝函数，保留原三表保护及原函数体。
    for table in _TABLES:
        op.execute(f"""CREATE TRIGGER reference_history_guard BEFORE UPDATE OR DELETE
            ON public.{table} FOR EACH ROW
            EXECUTE FUNCTION public.supportops_guard_knowledge_reference_chain()""")
        op.execute(f"""CREATE TRIGGER reference_history_truncate_guard BEFORE TRUNCATE
            ON public.{table} FOR EACH STATEMENT
            EXECUTE FUNCTION public.supportops_reject_reference_history_truncate()""")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER reference_history_guard")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER reference_history_truncate_guard")


def downgrade() -> None:
    # 先锁定三表，避免判空之后、移除触发器之前并发插入历史。
    op.execute("""LOCK TABLE public.knowledge_versions, public.knowledge_snapshots,
        public.knowledge_index_generations IN ACCESS EXCLUSIVE MODE""")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.knowledge_versions)
            OR EXISTS (SELECT 1 FROM public.knowledge_snapshots)
            OR EXISTS (SELECT 1 FROM public.knowledge_index_generations) THEN
            RAISE EXCEPTION 'KNOWLEDGE_REFERENCE_HISTORY_PREVENTS_DOWNGRADE';
        END IF;
        END; $$""")
    for table in reversed(_TABLES):
        op.execute(f"DROP TRIGGER reference_history_truncate_guard ON public.{table}")
        op.execute(f"DROP TRIGGER reference_history_guard ON public.{table}")
    op.execute("DROP FUNCTION public.supportops_guard_knowledge_reference_chain()")
