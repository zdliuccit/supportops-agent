"""本人显式单次Worker导入意图；领取永久消费，不回填或自动排队旧任务。"""

import sqlalchemy as sa
from alembic import op

revision = "20261019_0054"
down_revision = "20261019_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("knowledge_ingestion_execution_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="一次执行意图ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="可信租户。"),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("knowledge_ingestion_jobs.id"), nullable=False, comment="本人原导入任务，全局唯一。"),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="原任务申请人。"),
        sa.Column("auth_session_id", sa.Uuid(), sa.ForeignKey("auth_sessions.id"), nullable=False, comment="原登录会话引用，无令牌。"),
        sa.Column("auth_generation", sa.Integer(), nullable=False, comment="原登录许可代次。"),
        sa.Column("expected_job_revision", sa.Integer(), nullable=False, comment="本人确认的任务修订。"),
        sa.Column("scope_digest", sa.String(64), nullable=False, comment="原任务范围摘要。"),
        sa.Column("record_digest", sa.String(64), nullable=False, comment="固定意图摘要，不是授权。"),
        sa.Column("status", sa.String(16), nullable=False, comment="queued/claimed/completed/rejected/unconfirmed。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="单向状态修订。"),
        sa.Column("claim_id", sa.Uuid(), nullable=True, comment="唯一领取随机值。"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True, comment="原领取时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="意图终态时间，不替代Job结果。"),
        sa.Column("error_code", sa.String(48), nullable=True, comment="固定拒绝/未确认原因。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="本人显式提交时间。"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="最多60秒且不超过原范围窗口。"),
        sa.UniqueConstraint("job_id", name="uq_ingestion_execution_request_job"),
        sa.CheckConstraint("status IN ('queued','claimed','completed','rejected','unconfirmed')", name="ck_ingestion_execution_request_status"),
        sa.CheckConstraint("revision >= 1 AND expected_job_revision >= 1 AND auth_generation >= 0", name="ck_ingestion_execution_request_revision"),
        sa.CheckConstraint("expires_at > created_at", name="ck_ingestion_execution_request_window"),
        sa.CheckConstraint("(status IN ('claimed','completed','unconfirmed') AND claim_id IS NOT NULL AND claimed_at IS NOT NULL) OR (status IN ('queued','rejected') AND claim_id IS NULL AND claimed_at IS NULL)", name="ck_ingestion_execution_request_claim"),
        sa.CheckConstraint("(status IN ('completed','rejected','unconfirmed') AND finished_at IS NOT NULL) OR (status IN ('queued','claimed') AND finished_at IS NULL)", name="ck_ingestion_execution_request_finish"),
        sa.CheckConstraint("(status IN ('rejected','unconfirmed') AND error_code IS NOT NULL) OR (status IN ('queued','claimed','completed') AND error_code IS NULL)", name="ck_ingestion_execution_request_error"),
        comment="人工单次导入派发账本，不是定时更新或来源批准。")
    op.create_index("ix_ingestion_execution_request_queue", "knowledge_ingestion_execution_requests", ["status", "created_at", "id"])
    op.execute("""CREATE FUNCTION public.supportops_guard_ingestion_execution_request() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      BEGIN
        IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'INGESTION_EXECUTION_HISTORY_IMMUTABLE'; END IF;
        IF NEW.error_code IS NOT NULL AND NEW.error_code NOT IN
          ('REQUEST_EXPIRED','AUTHORIZATION_OR_SCOPE_CHANGED','JOB_CHANGED','EXECUTION_UNCONFIRMED') THEN
          RAISE EXCEPTION 'INGESTION_EXECUTION_ERROR_INVALID'; END IF;
        IF TG_OP='UPDATE' THEN
          IF (to_jsonb(NEW)-ARRAY['status','revision','claim_id','claimed_at','finished_at','error_code']) IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['status','revision','claim_id','claimed_at','finished_at','error_code']) OR
             NEW.revision<>OLD.revision+1 OR
             NOT ((OLD.status='queued' AND NEW.status IN ('claimed','rejected')) OR
                  (OLD.status='claimed' AND NEW.status IN ('completed','unconfirmed'))) THEN
            RAISE EXCEPTION 'INGESTION_EXECUTION_TRANSITION_INVALID'; END IF;
          IF OLD.status='claimed' AND (NEW.claim_id IS DISTINCT FROM OLD.claim_id OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at) THEN
            RAISE EXCEPTION 'INGESTION_EXECUTION_CLAIM_IMMUTABLE'; END IF;
          IF NEW.status='claimed' AND (NEW.expires_at<=statement_timestamp() OR NEW.claimed_at<NEW.created_at) THEN
            RAISE EXCEPTION 'INGESTION_EXECUTION_EXPIRED'; END IF;
          IF NEW.status='completed' AND NEW.expires_at<=statement_timestamp() THEN
            RAISE EXCEPTION 'INGESTION_EXECUTION_EXPIRED'; END IF;
          IF NEW.status='completed' AND NOT EXISTS (SELECT 1 FROM public.knowledge_ingestion_jobs j
            WHERE j.id=NEW.job_id AND j.tenant_id=NEW.tenant_id AND j.requested_by=NEW.requested_by AND j.status='completed') THEN
            RAISE EXCEPTION 'INGESTION_EXECUTION_RESULT_INVALID'; END IF;
          RETURN NEW;
        END IF;
        IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'INGESTION_EXECUTION_ISOLATION_INVALID'; END IF;
        PERFORM 1 FROM public.users WHERE id=NEW.requested_by AND tenant_id=NEW.tenant_id FOR UPDATE;
        IF NEW.status<>'queued' OR NEW.revision<>1 OR NEW.expires_at<=statement_timestamp() OR
           NEW.expires_at>NEW.created_at+interval '60 seconds' OR NEW.scope_digest!~'^[0-9a-f]{64}$' OR
           NEW.record_digest!~'^[0-9a-f]{64}$' OR NOT EXISTS (
            SELECT 1 FROM public.knowledge_ingestion_jobs j
              JOIN public.users u ON u.id=NEW.requested_by
              JOIN public.auth_sessions a ON a.id=NEW.auth_session_id
            WHERE j.id=NEW.job_id AND j.tenant_id=NEW.tenant_id AND j.requested_by=NEW.requested_by
              AND j.status='queued' AND j.attempts=0 AND j.revision=NEW.expected_job_revision
              AND NEW.expires_at<=j.deadline_at AND u.tenant_id=NEW.tenant_id AND u.status='active'
              AND u.roles::jsonb ? 'platform_admin'
              AND a.tenant_id=NEW.tenant_id AND a.user_id=u.id AND a.auth_generation=NEW.auth_generation
              AND u.auth_generation=NEW.auth_generation AND a.revoked_at IS NULL AND NEW.expires_at<=a.expires_at
           ) THEN RAISE EXCEPTION 'INGESTION_EXECUTION_BINDING_INVALID'; END IF;
        RETURN NEW;
      END $$""")
    op.execute("CREATE TRIGGER ingestion_execution_request_guard BEFORE INSERT OR UPDATE OR DELETE ON public.knowledge_ingestion_execution_requests FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_ingestion_execution_request()")
    op.execute("CREATE TRIGGER ingestion_execution_request_truncate_guard BEFORE TRUNCATE ON public.knowledge_ingestion_execution_requests FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_guard_ingestion_execution_request()")
    op.execute("ALTER TABLE public.knowledge_ingestion_execution_requests ENABLE ALWAYS TRIGGER ingestion_execution_request_guard")
    op.execute("ALTER TABLE public.knowledge_ingestion_execution_requests ENABLE ALWAYS TRIGGER ingestion_execution_request_truncate_guard")


def downgrade() -> None:
    op.execute("LOCK TABLE public.knowledge_ingestion_execution_requests IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.knowledge_ingestion_execution_requests) THEN RAISE EXCEPTION 'INGESTION_EXECUTION_HISTORY_MUST_BE_PRESERVED'; END IF; END $$")
    op.drop_table("knowledge_ingestion_execution_requests")
    op.execute("DROP FUNCTION public.supportops_guard_ingestion_execution_request()")
