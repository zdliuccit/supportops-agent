"""工具来源allowlist、版本化发现目录及失效围栏，不连接远端。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261019_0051"
down_revision: str | None = "20261018_0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("tool_discovery_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="不可变发现修订ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="服务端身份固定租户。"),
        sa.Column("source_key", sa.String(64), nullable=False, comment="批准来源的稳定别名，不是URL或执行命令。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="来源单调修订/失效围栏。"),
        sa.Column("parent_revision", sa.Integer(), nullable=False, comment="直接前驱修订，首次为0。"),
        sa.Column("event_kind", sa.String(32), nullable=False, comment="配置、listChanged、手工刷新或目录结果。"),
        sa.Column("status", sa.String(16), nullable=False, comment="pending/ready/disabled/rejected，过期在读取时判断。"),
        sa.Column("policy", postgresql.JSONB(), nullable=False, comment="封闭来源指纹/工具摘要allowlist/期限，不含地址或凭据。"),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment="固定租户/来源和批准策略摘要。"),
        sa.Column("catalog", postgresql.JSON(none_as_null=True), nullable=True, comment="仅获批工具的封闭描述与稳定别名，不是执行许可。"),
        sa.Column("catalog_digest", sa.String(64), nullable=True, comment="固定来源/批准策略/目录完整摘要。"),
        sa.Column("failure_code", sa.String(64), nullable=True, comment="固定拒绝码，不含远端诊断。"),
        sa.Column("observed_count", sa.Integer(), nullable=False, comment="本页观察工具数，不保留未知工具描述。"),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="当前显式管理员操作人。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="服务器追加时间。"),
        sa.UniqueConstraint("tenant_id", "source_key", "revision", name="uq_tool_discovery_revision"),
        sa.CheckConstraint("revision = parent_revision + 1 AND parent_revision >= 0", name="ck_tool_discovery_revision"),
        sa.CheckConstraint("status IN ('pending','ready','disabled','rejected')", name="ck_tool_discovery_status"),
        sa.CheckConstraint("observed_count BETWEEN 0 AND 100", name="ck_tool_discovery_count"),
        sa.CheckConstraint("(status = 'ready' AND catalog IS NOT NULL AND catalog_digest IS NOT NULL) OR "
            "(status <> 'ready' AND catalog IS NULL AND catalog_digest IS NULL)", name="ck_tool_discovery_catalog_pair"),
        sa.CheckConstraint("(status = 'rejected' AND failure_code IS NOT NULL) OR "
            "(status <> 'rejected' AND failure_code IS NULL)", name="ck_tool_discovery_failure_pair"),
        comment="追加式工具来源策略/目录快照/失效事件；只有当前获批快照可解析，历史保留。")
    op.create_index("ix_tool_discovery_latest", "tool_discovery_revisions", ["tenant_id", "source_key", "revision", "id"])
    op.execute("""CREATE FUNCTION public.supportops_guard_tool_discovery() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        DECLARE previous public.tool_discovery_revisions%ROWTYPE;
        BEGIN
        IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'TOOL_DISCOVERY_HISTORY_IMMUTABLE'; END IF;
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'TOOL_DISCOVERY_ISOLATION_UNSUPPORTED'; END IF;
        PERFORM 1 FROM public.tenants WHERE id=NEW.tenant_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'TOOL_DISCOVERY_SCOPE_INVALID'; END IF;
        SELECT * INTO previous FROM public.tool_discovery_revisions
            WHERE tenant_id=NEW.tenant_id AND source_key=NEW.source_key ORDER BY revision DESC LIMIT 1;
        IF NEW.parent_revision <> COALESCE(previous.revision,0) THEN
            RAISE EXCEPTION 'TOOL_DISCOVERY_REVISION_CONFLICT'; END IF;
        IF NEW.event_kind = 'source_configured' THEN
            IF NEW.status NOT IN ('pending','disabled') THEN RAISE EXCEPTION 'TOOL_DISCOVERY_TRANSITION_INVALID'; END IF;
        ELSE
            IF previous.id IS NULL OR NEW.policy_digest IS DISTINCT FROM previous.policy_digest
                OR NEW.policy IS DISTINCT FROM previous.policy THEN RAISE EXCEPTION 'TOOL_DISCOVERY_POLICY_CHANGED'; END IF;
            IF NEW.event_kind IN ('manual_refresh','list_changed') THEN
                IF NEW.status <> 'pending' THEN RAISE EXCEPTION 'TOOL_DISCOVERY_TRANSITION_INVALID'; END IF;
            ELSIF NEW.event_kind IN ('catalog_recorded','catalog_rejected') THEN
                IF previous.status <> 'pending' OR
                    (NEW.event_kind='catalog_recorded' AND NEW.status <> 'ready') OR
                    (NEW.event_kind='catalog_rejected' AND NEW.status <> 'rejected') THEN
                    RAISE EXCEPTION 'TOOL_DISCOVERY_TRANSITION_INVALID'; END IF;
            ELSE RAISE EXCEPTION 'TOOL_DISCOVERY_EVENT_UNSUPPORTED'; END IF;
        END IF;
        RETURN NEW; END; $$""")
    op.execute("""CREATE TRIGGER tool_discovery_history_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.tool_discovery_revisions FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_tool_discovery()""")
    op.execute("""CREATE TRIGGER tool_discovery_truncate_guard BEFORE TRUNCATE
        ON public.tool_discovery_revisions FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_guard_tool_discovery()""")
    op.execute("ALTER TABLE public.tool_discovery_revisions ENABLE ALWAYS TRIGGER tool_discovery_history_guard")
    op.execute("ALTER TABLE public.tool_discovery_revisions ENABLE ALWAYS TRIGGER tool_discovery_truncate_guard")


def downgrade() -> None:
    op.execute("""LOCK TABLE public.tool_discovery_revisions IN ACCESS EXCLUSIVE MODE""")
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.tool_discovery_revisions) THEN
        RAISE EXCEPTION 'TOOL_DISCOVERY_HISTORY_MUST_BE_PRESERVED'; END IF; END $$""")
    op.drop_table("tool_discovery_revisions")
    op.execute("DROP FUNCTION public.supportops_guard_tool_discovery()")
