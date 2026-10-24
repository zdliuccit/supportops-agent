"""不可变路由策略候选版本与永久撤销；不批准或启用路由。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_0061"
down_revision = "20261020_0060"
branch_labels = None
depends_on = None

_TABLES = ("model_routing_policy_versions", "model_routing_policy_revocations")


def upgrade() -> None:
    op.alter_column("agent_audit_events", "version_id", existing_type=sa.Uuid(), existing_nullable=True,
        comment="相关固定资源版本标识；具体类型以resource_type为准。")
    op.create_table("model_routing_policy_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="原提交固定的版本ID，用于只读查证未知提交。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="可信租户。"),
        sa.Column("policy_id", sa.Uuid(), nullable=False, comment="同租户策略系列ID。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="策略系列连续版本号。"),
        sa.Column("previous_version_id", sa.Uuid(), nullable=True, comment="同租户同策略前版。"),
        sa.Column("previous_policy_digest", sa.String(64), nullable=True, comment="前版完整规范摘要。"),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment="包含服务器身份和时间的完整版本SHA-256。"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, comment="封闭策略版本，无URL、秘密或规则代码。"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="实际保存版本的管理员。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="服务器保存时间。"),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False, comment="原版本期限，不允许原地延期。"),
        sa.UniqueConstraint("tenant_id", "policy_id", "revision", name="uq_routing_policy_revision"),
        sa.UniqueConstraint("tenant_id", "policy_id", "id", name="uq_routing_policy_scope"),
        sa.ForeignKeyConstraint(["tenant_id", "policy_id", "previous_version_id"],
            ["model_routing_policy_versions.tenant_id", "model_routing_policy_versions.policy_id",
                "model_routing_policy_versions.id"], name="fk_routing_policy_previous"),
        sa.CheckConstraint("revision >= 1", name="ck_routing_policy_revision"),
        sa.CheckConstraint("(revision = 1 AND previous_version_id IS NULL AND previous_policy_digest IS NULL) OR "
            "(revision > 1 AND previous_version_id IS NOT NULL AND previous_policy_digest IS NOT NULL "
            "AND previous_version_id <> id)", name="ck_routing_policy_previous"),
        sa.CheckConstraint("valid_until > created_at", name="ck_routing_policy_window"),
        comment="追加式模型路由策略候选版本，无批准或执行开关。")
    op.create_index("ix_routing_policy_tenant_created", "model_routing_policy_versions", ["tenant_id", "created_at", "id"])
    op.create_table("model_routing_policy_revocations",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="撤销事实ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="可信租户。"),
        sa.Column("policy_id", sa.Uuid(), nullable=False, comment="原策略系列ID。"),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False, comment="原精确版本，唯一撤销。"),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment="原策略完整摘要。"),
        sa.Column("record_digest", sa.String(64), nullable=False, comment="原撤销范围/人员/时间/原因的完整摘要。"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, comment="封闭撤销事实，无任意说明正文。"),
        sa.Column("revoked_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="实际撤销管理员。"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False, comment="真实撤销时间，不可改写。"),
        sa.UniqueConstraint("policy_version_id", name="uq_routing_policy_revocation"),
        sa.ForeignKeyConstraint(["tenant_id", "policy_id", "policy_version_id"],
            ["model_routing_policy_versions.tenant_id", "model_routing_policy_versions.policy_id",
                "model_routing_policy_versions.id"], name="fk_routing_policy_revoked_scope"),
        comment="独立追加式策略撤销事实，不撤销或改写历史模型调用。")
    op.create_index("ix_routing_policy_revoked_tenant", "model_routing_policy_revocations", ["tenant_id", "revoked_at", "id"])
    op.execute("CREATE FUNCTION public.supportops_guard_routing_policy_history() RETURNS trigger LANGUAGE plpgsql "
        "SET search_path=pg_catalog AS $routing_guard$ BEGIN "
        "RAISE EXCEPTION 'ROUTING_POLICY_HISTORY_IMMUTABLE'; END; $routing_guard$")
    for table in _TABLES:
        # 表名来自固定源码常量，不接收配置或请求字符串。
        op.execute(f"CREATE TRIGGER routing_policy_history_guard BEFORE UPDATE OR DELETE ON public.{table} "
            "FOR EACH ROW EXECUTE FUNCTION public.supportops_guard_routing_policy_history()")
        op.execute(f"CREATE TRIGGER routing_policy_truncate_guard BEFORE TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.supportops_guard_routing_policy_history()")
        for trigger in ("routing_policy_history_guard", "routing_policy_truncate_guard"):
            op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {trigger}")


def downgrade() -> None:
    op.execute("LOCK TABLE public.model_routing_policy_versions, public.model_routing_policy_revocations "
        "IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM public.model_routing_policy_versions) "
        "OR EXISTS(SELECT 1 FROM public.model_routing_policy_revocations) THEN "
        "RAISE EXCEPTION 'ROUTING_POLICY_HISTORY_MUST_BE_PRESERVED'; END IF; END $$")
    for table in reversed(_TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION public.supportops_guard_routing_policy_history()")
    op.alter_column("agent_audit_events", "version_id", existing_type=sa.Uuid(), existing_nullable=True,
        comment="相关 AgentVersion 或 ModelEndpointVersion 标识。")
