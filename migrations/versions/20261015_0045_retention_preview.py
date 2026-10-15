"""保留草稿、本人一次影响预览与未来最小删除事实；不执行清理或回填。"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0045"
down_revision: str | None = "20261015_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("retention_policies",
        sa.Column("id", sa.Uuid(), primary_key=True, comment='策略草稿版本ID。'),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment='策略所属租户。'),
        sa.Column("name", sa.String(100), nullable=False, comment='策略草稿名称。'),
        sa.Column("definition", postgresql.JSONB(), nullable=False, comment='严格类别/周期/待确认法律保留/候选复核人员，不含业务正文。'),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment='固定租户/创建人/策略ID与完整定义的SHA256。'),
        sa.Column("status", sa.String(16), nullable=False, comment='当前只支持draft；草稿不产生批准。'),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment='创建草稿的当前管理员。'),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment='服务器创建时间。'),
        sa.CheckConstraint("status = 'draft'", name="ck_retention_policy_draft"),
        comment='保留规则与候选复核范围的不可变草稿，不授权自动清理或擦除。')
    op.create_index("ix_retention_policy_latest", "retention_policies", ["tenant_id", "created_at", "id"])
    op.create_table("erasure_requests",
        sa.Column("id", sa.Uuid(), primary_key=True, comment='申请ID。'),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment='可信申请租户。'),
        sa.Column("subject_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment='只能为当前登录本人，客户端不能指定其他用户。'),
        sa.Column("idempotency_key", sa.String(200), nullable=False, comment='本人申请幂等键；同键不同范围拒绝。'),
        sa.Column("payload_digest", sa.String(64), nullable=False, comment='类别/固定原因码的请求摘要。'),
        sa.Column("categories", postgresql.JSONB(), nullable=False, comment='固定选择的数据类别，不由模型扩展。'),
        sa.Column("reason_code", sa.String(32), nullable=False, comment='固定申请原因码，不保存自由文本个人信息。'),
        sa.Column("policy_id", sa.Uuid(), sa.ForeignKey("retention_policies.id"), nullable=True, comment='创建时最新草稿；为空表示未配置，不改绑后续策略。'),
        sa.Column("policy_digest", sa.String(64), nullable=True, comment='创建时策略摘要，草稿不代表批准。'),
        sa.Column("scope_digest", sa.String(64), nullable=False, comment='租户/本人/类别/原因/策略/截止时间的范围摘要。'),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False, comment='服务器固定的预览创建记录截止时间，不是删除截止许可。'),
        sa.Column("status", sa.String(16), nullable=False, comment='requested/previewed/withdrawn；没有approved或erased状态。'),
        sa.Column("revision", sa.Integer(), nullable=False, comment='预览/撤回操作的并发版本。'),
        sa.Column("preview_report", postgresql.JSONB(), nullable=True, comment='只含聚合计数/未知和阻塞原因，无正文/资源ID/凭据。'),
        sa.Column("preview_digest", sa.String(64), nullable=True, comment='固定元数据预览SHA256，读取复检。'),
        sa.Column("previewed_at", sa.DateTime(timezone=True), nullable=True, comment='成功生成一次预览的服务器时间。'),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True, comment='本人明确撤回时间，保留原申请和预览。'),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment='申请创建时间。'),
        sa.UniqueConstraint("tenant_id", "subject_user_id", "idempotency_key", name="uq_erasure_request_key"),
        sa.CheckConstraint("status IN ('requested', 'previewed', 'withdrawn') AND revision >= 1", name="ck_erasure_request_state"),
        sa.CheckConstraint("(policy_id IS NULL AND policy_digest IS NULL) OR (policy_id IS NOT NULL AND policy_digest IS NOT NULL)", name="ck_erasure_policy_pair"),
        sa.CheckConstraint("(preview_report IS NULL AND preview_digest IS NULL AND previewed_at IS NULL) OR (preview_report IS NOT NULL AND preview_digest IS NOT NULL AND previewed_at IS NOT NULL)", name="ck_erasure_preview_pair"),
        sa.CheckConstraint("(status = 'withdrawn' AND withdrawn_at IS NOT NULL) OR (status <> 'withdrawn' AND withdrawn_at IS NULL)", name="ck_erasure_withdrawal_pair"),
        sa.CheckConstraint("(status <> 'requested' OR preview_report IS NULL) AND (status <> 'previewed' OR preview_report IS NOT NULL)", name="ck_erasure_preview_state"),
        sa.CheckConstraint("reason_code IN ('privacy_request', 'account_closure', 'data_correction')", name="ck_erasure_reason"),
        comment='本人擦除意向、固定范围和不可覆盖预览；不批准、隔离或删除业务数据。')
    op.create_index("ix_erasure_request_owner", "erasure_requests", ["tenant_id", "subject_user_id", "created_at", "id"])
    op.create_table("deletion_tombstones",
        sa.Column("id", sa.Uuid(), primary_key=True, comment='最小删除/隔离事实ID。'),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment='原事实租户ID；保留独立证明，不依赖被恢复的租户记录。'),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False, comment='原主体ID，不含邮箱/姓名，不依赖被擦除身份外键。'),
        sa.Column("erasure_request_id", sa.Uuid(), nullable=False, comment='原获批申请ID，未来维护者须独立验证，当前不生成。'),
        sa.Column("category", sa.String(32), nullable=False, comment='实际维护的数据类别。'),
        sa.Column("resource_key_digest", sa.String(64), nullable=False, comment='稳定资源定位摘要；不保存原始正文或秘密。'),
        sa.Column("scope_digest", sa.String(64), nullable=False, comment='实际批准维护范围的摘要。'),
        sa.Column("action", sa.String(16), nullable=False, comment='实际已隔离或已擦除；不会由预览填入。'),
        sa.Column("evidence_digest", sa.String(64), nullable=False, comment='独立维护者实际结果证明摘要。'),
        sa.Column("maintenance_identity", sa.String(100), nullable=False, comment='未来独立维护执行身份标识，不保存令牌。'),
        sa.Column("record_digest", sa.String(64), nullable=False, comment='完整最小事实的规范化摘要。'),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, comment='实际维护后追加时间；当前不回填。'),
        sa.UniqueConstraint("tenant_id", "erasure_request_id", "category", "resource_key_digest", "action", name="uq_deletion_tombstone_fact"),
        sa.CheckConstraint("action IN ('quarantined', 'erased')", name="ck_deletion_tombstone_action"),
        comment='实际维护后的追加式最小事实，主体/请求不设业务外键以避免未来擦除破坏证明；当前不会生成记录。')
    op.create_index("ix_deletion_tombstone_subject", "deletion_tombstones", ["tenant_id", "subject_user_id", "recorded_at", "id"])
    op.execute("""CREATE FUNCTION supportops_guard_retention_fact() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'retention draft and deletion facts are append only'; END; $$""")
    op.execute("""CREATE TRIGGER retention_policy_immutable BEFORE UPDATE OR DELETE ON retention_policies
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_retention_fact()""")
    op.execute("""CREATE TRIGGER deletion_tombstone_immutable BEFORE UPDATE OR DELETE ON deletion_tombstones
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_retention_fact()""")
    op.execute("""CREATE FUNCTION supportops_guard_erasure_request() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'erasure request history cannot be deleted'; END IF;
        IF ROW(NEW.id, NEW.tenant_id, NEW.subject_user_id, NEW.idempotency_key, NEW.payload_digest, NEW.categories,
            NEW.reason_code, NEW.policy_id, NEW.policy_digest, NEW.scope_digest, NEW.cutoff_at, NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id, OLD.tenant_id, OLD.subject_user_id, OLD.idempotency_key, OLD.payload_digest,
            OLD.categories, OLD.reason_code, OLD.policy_id, OLD.policy_digest, OLD.scope_digest, OLD.cutoff_at, OLD.created_at)
            THEN RAISE EXCEPTION 'erasure request scope is immutable'; END IF;
        IF OLD.preview_report IS NOT NULL AND ROW(NEW.preview_report, NEW.preview_digest, NEW.previewed_at)
            IS DISTINCT FROM ROW(OLD.preview_report, OLD.preview_digest, OLD.previewed_at)
            THEN RAISE EXCEPTION 'erasure preview cannot be overwritten'; END IF;
        IF OLD.preview_report IS NULL AND NEW.preview_report IS NOT NULL AND NEW.status <> 'previewed' THEN
            RAISE EXCEPTION 'preview can only be recorded in previewed transition'; END IF;
        IF OLD.status = 'withdrawn' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'withdrawn erasure request cannot be changed'; END IF;
        IF NEW IS DISTINCT FROM OLD AND (NEW.revision <> OLD.revision + 1 OR
            NOT ((OLD.status = 'requested' AND NEW.status IN ('previewed', 'withdrawn')) OR
                 (OLD.status = 'previewed' AND NEW.status = 'withdrawn'))) THEN
            RAISE EXCEPTION 'invalid erasure request transition'; END IF;
        RETURN NEW; END; $$""")
    op.execute("""CREATE TRIGGER erasure_request_immutable_scope BEFORE UPDATE OR DELETE ON erasure_requests
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_erasure_request()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM retention_policies) OR EXISTS (SELECT 1 FROM erasure_requests)
        OR EXISTS (SELECT 1 FROM deletion_tombstones) THEN
        RAISE EXCEPTION 'retention and erasure facts must be preserved before downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER erasure_request_immutable_scope ON erasure_requests")
    op.execute("DROP FUNCTION supportops_guard_erasure_request()")
    op.execute("DROP TRIGGER deletion_tombstone_immutable ON deletion_tombstones")
    op.execute("DROP TRIGGER retention_policy_immutable ON retention_policies")
    op.execute("DROP FUNCTION supportops_guard_retention_fact()")
    op.drop_table("deletion_tombstones")
    op.drop_table("erasure_requests")
    op.drop_table("retention_policies")
