"""HTTP MCP固定派发、一次领取与不可替换令牌元数据；不回填或启用执行。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261019_0053"
down_revision = "20261019_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("http_mcp_dispatches",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="网关随机派发ID，不单独授予许可。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="可信租户。"),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="原主体。"),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False, comment="固定Run。"),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("run_attempts.id"), nullable=False, comment="固定尝试。"),
        sa.Column("invocation_id", sa.Uuid(), sa.ForeignKey("tool_invocations.id"), nullable=False, comment="唯一工具许可。"),
        sa.Column("source_record_id", sa.Uuid(), sa.ForeignKey("tool_discovery_revisions.id"), nullable=False, comment="固定来源修订。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="固定执行围栏。"),
        sa.Column("executor_uid", sa.Integer(), nullable=False, comment="固定本机执行UID。"),
        sa.Column("request_digest", sa.String(64), nullable=False, comment="请求SHA。"),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False, comment="封闭非秘密配置/调用引用，不含参数正文或令牌。"),
        sa.Column("status", sa.String(16), nullable=False, comment="单向状态。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="并发修订。"),
        sa.Column("claim_id", sa.Uuid(), nullable=True, comment="一次领取nonce。"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True, comment="实际取密前已提交时间。"),
        sa.Column("token_state", postgresql.JSONB(none_as_null=True), nullable=True, comment="一次绑定的非秘密令牌元数据。"),
        sa.Column("token_state_digest", sa.String(64), nullable=True, comment="固定令牌元数据SHA。"),
        sa.Column("lease_id", sa.Uuid(), nullable=True, comment="一次固定提供者lease，不可跨派发复用。"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, comment="提供者确认的首次撤销时间。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="创建时间。"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="固定截止，不续期。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="状态时间。"),
        sa.UniqueConstraint("invocation_id", name="uq_http_mcp_dispatch_invocation"),
        sa.UniqueConstraint("lease_id", name="uq_http_mcp_dispatch_lease"),
        sa.CheckConstraint("status IN ('authorized','claimed','bound','blocked','revoked')", name="ck_http_mcp_dispatch_status"),
        sa.CheckConstraint("revision >= 1 AND generation >= 1 AND executor_uid = 10004", name="ck_http_mcp_dispatch_revision"),
        sa.CheckConstraint("(status = 'authorized' AND claim_id IS NULL AND claimed_at IS NULL) OR (status <> 'authorized' AND claim_id IS NOT NULL AND claimed_at IS NOT NULL)", name="ck_http_mcp_dispatch_claim"),
        sa.CheckConstraint("(status IN ('bound','revoked') AND token_state IS NOT NULL AND token_state_digest IS NOT NULL AND lease_id IS NOT NULL) OR (status IN ('authorized','claimed','blocked') AND token_state IS NULL AND token_state_digest IS NULL AND lease_id IS NULL)", name="ck_http_mcp_dispatch_token"),
        sa.CheckConstraint("expires_at > created_at", name="ck_http_mcp_dispatch_expiry"),
        sa.CheckConstraint("(status = 'revoked' AND revoked_at IS NOT NULL) OR (status <> 'revoked' AND revoked_at IS NULL)", name="ck_http_mcp_dispatch_revoked"),
        comment="HTTP MCP一次派发；无访问/刷新令牌，未知领取/取密不重做。")
    op.create_index("ix_http_mcp_dispatch_scope", "http_mcp_dispatches", ["tenant_id", "actor_user_id", "created_at"])
    op.execute("""CREATE FUNCTION public.supportops_guard_http_mcp_dispatch() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog AS $$
      BEGIN
        IF TG_OP IN ('DELETE','TRUNCATE') THEN RAISE EXCEPTION 'MCP_DISPATCH_HISTORY_IMMUTABLE'; END IF;
        IF current_setting('transaction_isolation') <> 'read committed' THEN RAISE EXCEPTION 'MCP_DISPATCH_ISOLATION_UNSUPPORTED'; END IF;
        IF TG_OP='UPDATE' THEN
          IF (to_jsonb(NEW)-ARRAY['status','revision','claim_id','claimed_at','token_state','token_state_digest','lease_id','revoked_at','updated_at']) IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['status','revision','claim_id','claimed_at','token_state','token_state_digest','lease_id','revoked_at','updated_at']) OR
             NEW.revision<>OLD.revision+1 OR NEW.updated_at<OLD.updated_at THEN RAISE EXCEPTION 'MCP_DISPATCH_SCOPE_IMMUTABLE'; END IF;
          IF NOT ((OLD.status='authorized' AND NEW.status='claimed') OR (OLD.status='claimed' AND NEW.status IN ('bound','blocked')) OR
              (OLD.status='bound' AND NEW.status='revoked')) THEN RAISE EXCEPTION 'MCP_DISPATCH_TRANSITION_INVALID'; END IF;
          IF OLD.claim_id IS NOT NULL AND (NEW.claim_id IS DISTINCT FROM OLD.claim_id OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at) THEN
            RAISE EXCEPTION 'MCP_DISPATCH_ALREADY_CLAIMED'; END IF;
          IF OLD.token_state IS NOT NULL AND (NEW.token_state IS DISTINCT FROM OLD.token_state OR NEW.token_state_digest IS DISTINCT FROM OLD.token_state_digest OR NEW.lease_id IS DISTINCT FROM OLD.lease_id) THEN
            RAISE EXCEPTION 'MCP_DISPATCH_TOKEN_IMMUTABLE'; END IF;
          IF NEW.status IN ('claimed','bound') AND NEW.expires_at<=statement_timestamp() THEN RAISE EXCEPTION 'MCP_DISPATCH_EXPIRED'; END IF;
          IF NEW.status='revoked' AND (NEW.revoked_at>statement_timestamp() OR NEW.revoked_at < (OLD.token_state->>'issued_at')::timestamptz) THEN
            RAISE EXCEPTION 'MCP_DISPATCH_REVOCATION_INVALID'; END IF;
          RETURN NEW;
        END IF;
        IF NEW.status<>'authorized' OR NEW.revision<>1 OR NEW.created_at>statement_timestamp() OR NEW.expires_at<=statement_timestamp() OR
           NEW.expires_at>NEW.created_at+interval '300 seconds' OR octet_length(NEW.request_payload::text)>16384 THEN
          RAISE EXCEPTION 'MCP_DISPATCH_INVALID'; END IF;
        IF NOT EXISTS (SELECT 1 FROM public.tool_invocations i JOIN public.run_attempts a ON a.id=i.attempt_id
          JOIN public.agent_runs r ON r.id=i.run_id JOIN public.conversations c ON c.id=r.conversation_id
          JOIN public.tool_discovery_revisions s ON s.id=NEW.source_record_id
          WHERE i.id=NEW.invocation_id AND i.tenant_id=NEW.tenant_id AND i.run_id=NEW.run_id AND i.attempt_id=NEW.attempt_id AND i.status='running'
            AND a.tenant_id=NEW.tenant_id AND a.run_id=r.id AND a.generation=NEW.generation AND a.status='running' AND a.lease_expires_at>statement_timestamp()
            AND NEW.expires_at<=a.lease_expires_at AND i.risk_level IN ('R0','R1') AND i.business_action_id IS NULL
            AND r.tenant_id=NEW.tenant_id AND r.status='RUNNING' AND r.current_attempt_id=a.id AND r.execution_generation=NEW.generation
            AND c.tenant_id=NEW.tenant_id AND c.user_id=NEW.actor_user_id AND c.active_run_id=r.id AND c.active_attempt_id=a.id
            AND c.deleted_at IS NULL AND s.tenant_id=NEW.tenant_id AND s.status='ready') THEN RAISE EXCEPTION 'MCP_DISPATCH_BINDING_INVALID'; END IF;
        RETURN NEW;
      END $$""")
    op.execute("CREATE TRIGGER http_mcp_dispatch_guard BEFORE INSERT OR UPDATE OR DELETE ON public.http_mcp_dispatches FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_http_mcp_dispatch()")
    op.execute("CREATE TRIGGER http_mcp_dispatch_truncate_guard BEFORE TRUNCATE ON public.http_mcp_dispatches FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_guard_http_mcp_dispatch()")
    op.execute("ALTER TABLE public.http_mcp_dispatches ENABLE ALWAYS TRIGGER http_mcp_dispatch_guard")
    op.execute("ALTER TABLE public.http_mcp_dispatches ENABLE ALWAYS TRIGGER http_mcp_dispatch_truncate_guard")


def downgrade() -> None:
    op.execute("LOCK TABLE public.http_mcp_dispatches IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.http_mcp_dispatches) THEN RAISE EXCEPTION 'MCP_DISPATCH_HISTORY_MUST_BE_PRESERVED'; END IF; END $$")
    op.drop_table("http_mcp_dispatches")
    op.execute("DROP FUNCTION public.supportops_guard_http_mcp_dispatch()")
