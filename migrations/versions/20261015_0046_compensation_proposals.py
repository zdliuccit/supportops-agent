"""追加式隔离Mock补偿提案；无批准、执行和原动作状态回写。"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0046"
down_revision: str | None = "20261015_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("action_compensation_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="补偿提案ID，与原逻辑动作分离。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="当前可信租户。"),
        sa.Column("subject_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="原动作主体，只有本人提出和读取。"),
        sa.Column("action_id", sa.Uuid(), sa.ForeignKey("business_actions.id"), nullable=False, comment="保留的原业务动作。"),
        sa.Column("source_receipt_id", sa.Uuid(), sa.ForeignKey("action_receipts.id"), nullable=False, comment="当前generation独立复核/冲突回执，不接受人工猜测结果。"),
        sa.Column("scope", postgresql.JSONB(), nullable=False, comment="冻结原动作/回执/目标版本/当前授权上下文和关闭意向，无正文或秘密。"),
        sa.Column("scope_digest", sa.String(64), nullable=False, comment="固定补偿范围SHA256，同一范围幂等复用。"),
        sa.Column("minimum_risk", sa.String(2), nullable=False, comment="最低R3，原动作R4时保持R4，不能降为原R2。"),
        sa.Column("reason_code", sa.String(32), nullable=False, comment="复核冲突或本人明确请求关闭，不保存自由文本。"),
        sa.Column("status", sa.String(16), nullable=False, comment="仅proposed，不能标为approved/queued/completed。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="服务器保存提案时间，不是执行时间。"),
        sa.UniqueConstraint("tenant_id", "action_id", "scope_digest", name="uq_compensation_proposal_scope"),
        sa.CheckConstraint("status = 'proposed' AND minimum_risk IN ('R3', 'R4')", name="ck_compensation_proposal_state"),
        sa.CheckConstraint("reason_code IN ('verification_conflict', 'user_requested_closure')", name="ck_compensation_proposal_reason"),
        comment="隔离Mock关闭工单的不可变补偿提案，不继承原动作批准且没有执行入口。")
    op.create_index("ix_compensation_proposal_owner", "action_compensation_proposals",
        ["tenant_id", "subject_user_id", "action_id", "created_at", "id"])
    op.execute("""CREATE FUNCTION supportops_guard_compensation_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'compensation proposals are immutable historical intents'; END; $$""")
    op.execute("""CREATE TRIGGER compensation_proposal_immutable BEFORE UPDATE OR DELETE ON action_compensation_proposals
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_compensation_proposal()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM action_compensation_proposals) THEN
        RAISE EXCEPTION 'compensation history must be preserved before downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER compensation_proposal_immutable ON action_compensation_proposals")
    op.execute("DROP FUNCTION supportops_guard_compensation_proposal()")
    op.drop_table("action_compensation_proposals")
