"""追加发布评测事实；不回填通过结果，不执行任何评测。"""
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "20261014_0042"
down_revision: str | None = "20261014_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("agent_release_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="评测事实唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="评测所属租户。"),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False, comment="评测所属 Agent。"),
        sa.Column("agent_version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id"), nullable=False, comment="被评测的固定 Agent 版本。"),
        sa.Column("execution_id", sa.Uuid(), nullable=False, comment="可信执行器物理执行 ID；重复提交不得覆盖事实。"),
        sa.Column("manifest_digest", sa.String(64), nullable=False, comment="被评测固定发布清单 SHA-256。"),
        sa.Column("scope_digest", sa.String(64), nullable=False, comment="执行时模型/工具/源码/权限/知识/凭据范围摘要。"),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment="服务器批准评测集/负载/执行器/门槛与环境的策略摘要。"),
        sa.Column("report", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False, comment="严格版本化计数和完整性指标；不存答案、工具正文或任意链接。"),
        sa.Column("report_digest", sa.String(64), nullable=False, comment="规范化评测报告 SHA-256；读取时复检。"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, comment="可信执行器实际开始时间。"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, comment="可信执行器实际完成时间。"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, comment="服务器追加事实时间；不由客户端填入。"),
        sa.UniqueConstraint("tenant_id", "execution_id", name="uq_release_evaluation_execution"),
        sa.CheckConstraint("completed_at >= started_at AND recorded_at >= completed_at", name="ck_release_evaluation_times"),
        comment="追加式发布评测事实；固定版本、当前范围、批准评测策略和结果摘要，无业务正文或秘密。")
    op.create_index("ix_release_evaluation_latest", "agent_release_evaluations",
        ["tenant_id", "agent_id", "agent_version_id", "recorded_at", "id"])
    op.execute("""CREATE FUNCTION supportops_guard_release_evaluation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'release evaluation facts are append only'; END; $$""")
    op.execute("""CREATE TRIGGER release_evaluation_append_only BEFORE UPDATE OR DELETE ON agent_release_evaluations
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_release_evaluation()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_release_evaluations) THEN
        RAISE EXCEPTION 'release evaluation history must be preserved before downgrade'; END IF; END $$""")
    op.drop_table("agent_release_evaluations")
    op.execute("DROP FUNCTION supportops_guard_release_evaluation()")
