"""隔离连接探测任务与追加式熔断/恢复事实。"""

import sqlalchemy as sa
from alembic import op

revision = "20261019_0052"
down_revision = "20261019_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("tool_connection_probe_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="探测任务ID兼作派发随机标识。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="可信租户。"),
        sa.Column("source_key", sa.String(64), nullable=False, comment="固定来源别名。"),
        sa.Column("source_record_id", sa.Uuid(), sa.ForeignKey("tool_discovery_revisions.id"), nullable=False, comment="固定目录修订。"),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment="固定批准策略摘要。"),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="发起管理员。"),
        sa.Column("auth_session_id", sa.Uuid(), sa.ForeignKey("auth_sessions.id"), nullable=False, comment="会话引用，不是令牌。"),
        sa.Column("auth_generation", sa.Integer(), nullable=False, comment="登录许可代次。"),
        sa.Column("status", sa.String(16), nullable=False, comment="任务状态。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="任务修订。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="执行围栏。"),
        sa.Column("owner_id", sa.Uuid(), nullable=True, comment="执行者许可。"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True, comment="当前租约。"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="固定截止。"),
        sa.Column("result_digest", sa.String(64), nullable=True, comment="报告摘要，不含正文。"),
        sa.Column("error_code", sa.String(64), nullable=True, comment="固定失败码。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="最后状态时间。"),
        sa.CheckConstraint("status IN ('queued','running','passed','failed','cancelled')", name="ck_tool_connection_job_status"),
        sa.CheckConstraint("generation >= 0 AND revision >= 1", name="ck_tool_connection_job_revision"),
        sa.CheckConstraint("(status = 'running' AND owner_id IS NOT NULL AND lease_until IS NOT NULL) OR (status <> 'running' AND owner_id IS NULL AND lease_until IS NULL)", name="ck_tool_connection_job_lease"),
        sa.CheckConstraint("(status = 'passed' AND result_digest IS NOT NULL) OR (status <> 'passed' AND result_digest IS NULL)", name="ck_tool_connection_job_result"),
        comment="一次性隔离连接探测任务，不含凭据或业务正文。")
    op.create_index("uq_tool_connection_active_job", "tool_connection_probe_jobs", ["tenant_id", "source_key"],
        unique=True, postgresql_where=sa.text("status IN ('queued','running')"))
    op.create_table("tool_connection_events",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="追加事件ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="可信租户。"),
        sa.Column("source_key", sa.String(64), nullable=False, comment="固定来源。"),
        sa.Column("source_record_id", sa.Uuid(), sa.ForeignKey("tool_discovery_revisions.id"), nullable=False, comment="对应目录修订。"),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment="固定策略摘要。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="来源连接修订。"),
        sa.Column("parent_revision", sa.Integer(), nullable=False, comment="直接前驱。"),
        sa.Column("kind", sa.String(32), nullable=False, comment="人工熔断或实际探测事件。"),
        sa.Column("circuit_state", sa.String(16), nullable=False, comment="closed/open/half_open。"),
        sa.Column("health_state", sa.String(16), nullable=False, comment="unknown/healthy/unhealthy。"),
        sa.Column("failure_count", sa.Integer(), nullable=False, comment="连续失败，最多3次。"),
        sa.Column("probe_job_id", sa.Uuid(), sa.ForeignKey("tool_connection_probe_jobs.id"), nullable=True, comment="实际任务。"),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="操作人。"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True, comment="确认时间。"),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True, comment="观察有效期。"),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True, comment="恢复最早时间。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="服务器追加时间。"),
        sa.UniqueConstraint("tenant_id", "source_key", "revision", name="uq_tool_connection_event_revision"),
        sa.CheckConstraint("revision = parent_revision + 1 AND parent_revision >= 0", name="ck_tool_connection_event_revision"),
        sa.CheckConstraint("circuit_state IN ('closed','open','half_open') AND health_state IN ('unknown','healthy','unhealthy')", name="ck_tool_connection_event_state"),
        sa.CheckConstraint("failure_count BETWEEN 0 AND 3", name="ck_tool_connection_event_failures"),
        sa.CheckConstraint("(health_state = 'healthy' AND checked_at IS NOT NULL AND valid_until IS NOT NULL AND circuit_state = 'closed') OR (health_state <> 'healthy' AND checked_at IS NULL AND valid_until IS NULL)", name="ck_tool_connection_event_health"),
        comment="追加式连接状态，不授予业务执行许可。")
    op.create_index("ix_tool_connection_event_latest", "tool_connection_events", ["tenant_id", "source_key", "revision"])
    op.execute("""CREATE FUNCTION public.supportops_guard_tool_connection() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      DECLARE source public.tool_discovery_revisions%ROWTYPE; previous public.tool_connection_events%ROWTYPE;
      BEGIN
        IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'TOOL_CONNECTION_HISTORY_IMMUTABLE'; END IF;
        IF TG_OP='UPDATE' THEN
          IF TG_TABLE_NAME='tool_connection_events' THEN RAISE EXCEPTION 'TOOL_CONNECTION_HISTORY_IMMUTABLE'; END IF;
          IF OLD.status IN ('passed','failed','cancelled') OR
             (to_jsonb(NEW)-ARRAY['status','revision','generation','owner_id','lease_until','result_digest','error_code','updated_at']) IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['status','revision','generation','owner_id','lease_until','result_digest','error_code','updated_at']) OR
             NEW.revision<>OLD.revision+1 OR NEW.generation<OLD.generation OR
             (OLD.status='queued' AND NEW.status NOT IN ('running','failed','cancelled')) OR
             (OLD.status='running' AND NEW.status NOT IN ('passed','failed','cancelled')) THEN
            RAISE EXCEPTION 'TOOL_CONNECTION_JOB_TRANSITION_INVALID'; END IF;
          IF NEW.status='running' AND (NEW.generation<>OLD.generation+1 OR NEW.lease_until>NEW.expires_at) THEN
            RAISE EXCEPTION 'TOOL_CONNECTION_JOB_LEASE_INVALID'; END IF;
          IF NEW.status='passed' AND (OLD.lease_until<=statement_timestamp() OR OLD.expires_at<=statement_timestamp()) THEN
            RAISE EXCEPTION 'TOOL_CONNECTION_JOB_EXPIRED'; END IF;
          RETURN NEW;
        END IF;
        IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'TOOL_CONNECTION_ISOLATION_UNSUPPORTED'; END IF;
        PERFORM 1 FROM public.tenants WHERE id=NEW.tenant_id FOR UPDATE;
        SELECT * INTO source FROM public.tool_discovery_revisions WHERE tenant_id=NEW.tenant_id AND source_key=NEW.source_key ORDER BY revision DESC LIMIT 1;
        IF source.id IS DISTINCT FROM NEW.source_record_id OR source.policy_digest IS DISTINCT FROM NEW.policy_digest THEN
          RAISE EXCEPTION 'TOOL_CONNECTION_SOURCE_CHANGED'; END IF;
        IF TG_TABLE_NAME='tool_connection_probe_jobs' THEN
          IF NEW.status<>'queued' OR NEW.revision<>1 OR NEW.generation<>0 OR NEW.expires_at<=NEW.created_at OR NEW.expires_at>NEW.created_at+interval '60 seconds' OR NOT EXISTS (
            SELECT 1 FROM public.auth_sessions a JOIN public.users u ON u.id=a.user_id WHERE a.id=NEW.auth_session_id
              AND a.user_id=NEW.requested_by AND a.tenant_id=NEW.tenant_id AND u.tenant_id=NEW.tenant_id
              AND a.auth_generation=NEW.auth_generation AND u.auth_generation=NEW.auth_generation) THEN
            RAISE EXCEPTION 'TOOL_CONNECTION_JOB_INVALID'; END IF;
        ELSE
          SELECT * INTO previous FROM public.tool_connection_events WHERE tenant_id=NEW.tenant_id AND source_key=NEW.source_key ORDER BY revision DESC LIMIT 1;
          IF NEW.parent_revision<>COALESCE(previous.revision,0) OR NEW.kind NOT IN ('manual_open','probe_queued','probe_passed','probe_failed','probe_cancelled') THEN
            RAISE EXCEPTION 'TOOL_CONNECTION_EVENT_INVALID'; END IF;
          IF (NEW.kind='manual_open') IS DISTINCT FROM (NEW.probe_job_id IS NULL) THEN RAISE EXCEPTION 'TOOL_CONNECTION_EVENT_JOB_INVALID'; END IF;
          IF NEW.kind='manual_open' AND (NEW.circuit_state<>'open' OR NEW.health_state<>'unknown') THEN RAISE EXCEPTION 'TOOL_CONNECTION_EVENT_INVALID'; END IF;
          IF NEW.probe_job_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.tool_connection_probe_jobs j WHERE j.id=NEW.probe_job_id AND j.tenant_id=NEW.tenant_id AND j.source_record_id=NEW.source_record_id) THEN
            RAISE EXCEPTION 'TOOL_CONNECTION_EVENT_JOB_INVALID'; END IF;
          IF NEW.health_state='healthy' AND (NEW.kind<>'probe_passed' OR NEW.valid_until>NEW.checked_at+interval '60 seconds' OR NOT EXISTS (
            SELECT 1 FROM public.tool_connection_probe_jobs j WHERE j.id=NEW.probe_job_id AND j.tenant_id=NEW.tenant_id AND j.source_record_id=NEW.source_record_id AND j.status='passed')) THEN
            RAISE EXCEPTION 'TOOL_CONNECTION_HEALTH_INVALID'; END IF;
        END IF;
        RETURN NEW;
      END $$""")
    for table in ("tool_connection_probe_jobs", "tool_connection_events"):
        op.execute(f"CREATE TRIGGER tool_connection_guard BEFORE INSERT OR UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_tool_connection()")
        op.execute(f"CREATE TRIGGER tool_connection_truncate_guard BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_guard_tool_connection()")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER tool_connection_guard")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER tool_connection_truncate_guard")


def downgrade() -> None:
    op.execute("LOCK TABLE public.tool_connection_events, public.tool_connection_probe_jobs IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.tool_connection_events) OR EXISTS(SELECT 1 FROM public.tool_connection_probe_jobs) THEN
      RAISE EXCEPTION 'TOOL_CONNECTION_HISTORY_MUST_BE_PRESERVED'; END IF; END $$""")
    op.drop_table("tool_connection_events")
    op.drop_table("tool_connection_probe_jobs")
    op.execute("DROP FUNCTION public.supportops_guard_tool_connection()")
