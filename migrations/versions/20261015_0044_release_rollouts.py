"""固定范围灰度、追加审计和新会话选版；不回填历史、不自动启用。"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0044"
down_revision: str | None = "20261015_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table("agent_release_rollouts",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="灰度ID，也是主体分组种子。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="灰度所属租户。"),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id"), nullable=False, comment="灰度所属Agent。"),
        sa.Column("baseline_version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id", name="fk_rollout_baseline"), nullable=False, comment="不可变活动基线。"),
        sa.Column("candidate_version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id", name="fk_rollout_candidate"), nullable=False, comment="不可变候选版本。"),
        sa.Column("subject_ids", postgresql.JSONB(), nullable=False, comment="固定主体UUID集合；空表示当前获准使用Agent的主体。"),
        sa.Column("policy", postgresql.JSONB(), nullable=False, comment="不可变有界观测策略；不包含人工填写通过结果。"),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment="策略摘要，需服务器批准配置匹配。"),
        sa.Column("definition_digest", sa.String(64), nullable=False, comment="固定版本、主体和策略定义摘要。"),
        sa.Column("status", sa.String(16), nullable=False, comment="draft/active/paused/stopped；停止不可复活。"),
        sa.Column("candidate_bps", sa.Integer(), nullable=False, comment="万分比；初始及步进最多1000，最高9000。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="控制面单调版本，阻止陈旧操作。"),
        sa.Column("epoch", sa.Integer(), nullable=False, comment="启动/恢复/扩大时新增的观测轮次。"),
        sa.Column("epoch_started_at", sa.DateTime(timezone=True), nullable=True, comment="当前观测轮次起点。"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True, comment="最近Worker复检时间；过期关闭候选流量。"),
        sa.Column("reason_code", sa.String(64), nullable=True, comment="固定暂停/停止原因码。"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", name="fk_rollout_creator"), nullable=False, comment="草稿创建管理员。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="草稿创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="最近控制或系统暂停时间。"),
        sa.CheckConstraint("status IN ('draft', 'active', 'paused', 'stopped')", name="ck_release_rollout_status"),
        sa.CheckConstraint("candidate_bps BETWEEN 1 AND 9000 AND revision >= 1 AND epoch >= 0", name="ck_release_rollout_bounds"),
        sa.CheckConstraint("baseline_version_id <> candidate_version_id", name="ck_release_rollout_versions"),
        sa.CheckConstraint("status NOT IN ('active', 'paused') OR (epoch >= 1 AND epoch_started_at IS NOT NULL AND last_checked_at IS NOT NULL)", name="ck_release_rollout_started"),
        comment="固定范围灰度控制面；缺少适用实测不能启动，不重写旧会话。")
    op.create_index("uq_release_rollout_live", "agent_release_rollouts", ["tenant_id", "agent_id"], unique=True,
        postgresql_where=sa.text("status IN ('draft', 'active', 'paused')"))
    op.create_index("ix_release_rollout_scan", "agent_release_rollouts", ["status", "last_checked_at", "id"])
    op.create_table("agent_release_rollout_events",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="追加事件ID。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="事件租户。"),
        sa.Column("rollout_id", sa.Uuid(), sa.ForeignKey("agent_release_rollouts.id"), nullable=False, comment="关联固定灰度。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="变更后控制面版本。"),
        sa.Column("actor_kind", sa.String(8), nullable=False, comment="实际操作者类型user/system。"),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True, comment="人工主体；系统巡检为空。"),
        sa.Column("event_type", sa.String(32), nullable=False, comment="创建/启动/恢复/扩大/暂停/停止/激活关闭。"),
        sa.Column("status", sa.String(16), nullable=False, comment="事件后的灰度状态。"),
        sa.Column("candidate_bps", sa.Integer(), nullable=False, comment="事件后的候选比例。"),
        sa.Column("epoch", sa.Integer(), nullable=False, comment="事件后的观测轮次。"),
        sa.Column("reason_code", sa.String(64), nullable=True, comment="固定控制原因码。"),
        sa.Column("policy_digest", sa.String(64), nullable=False, comment="固定策略摘要。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="服务器追加事实时间。"),
        sa.UniqueConstraint("rollout_id", "revision", name="uq_rollout_event_revision"),
        sa.CheckConstraint("(actor_kind = 'system' AND actor_user_id IS NULL) OR (actor_kind = 'user' AND actor_user_id IS NOT NULL)", name="ck_rollout_event_actor"),
        comment="追加式灰度控制事件；系统暂停不冒充人工决定。")
    op.create_index("ix_rollout_event_history", "agent_release_rollout_events", ["tenant_id", "rollout_id", "created_at", "id"])
    op.add_column("conversations", sa.Column("release_rollout_id", sa.Uuid(), nullable=True, comment="创建时的固定灰度；历史不回填。"))
    op.add_column("conversations", sa.Column("release_rollout_epoch", sa.Integer(), nullable=True, comment="创建时观测轮次。"))
    op.add_column("conversations", sa.Column("release_selection", sa.String(16), nullable=True, comment="固定baseline/candidate分组。"))
    op.add_column("conversations", sa.Column("release_selection_bucket", sa.Integer(), nullable=True, comment="稳定主体哈希桶0至9999，不授予权限。"))
    op.create_foreign_key("fk_conversation_release_rollout", "conversations", "agent_release_rollouts", ["release_rollout_id"], ["id"])
    op.create_index("ix_conversations_release_rollout_id", "conversations", ["release_rollout_id"])
    op.create_check_constraint("ck_conversation_release_selection", "conversations",
        "(release_rollout_id IS NULL AND release_rollout_epoch IS NULL AND release_selection IS NULL AND release_selection_bucket IS NULL) OR "
        "(release_rollout_id IS NOT NULL AND release_rollout_epoch IS NOT NULL AND release_selection IS NOT NULL AND release_selection_bucket IS NOT NULL AND release_rollout_epoch >= 1 AND release_selection IN ('baseline', 'candidate') AND release_selection_bucket BETWEEN 0 AND 9999)")
    op.execute("""CREATE FUNCTION supportops_guard_rollout_event() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'release rollout events are append only'; END; $$""")
    op.execute("""CREATE TRIGGER release_rollout_event_append_only BEFORE UPDATE OR DELETE ON agent_release_rollout_events
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_rollout_event()""")
    op.execute("""CREATE FUNCTION supportops_guard_rollout_definition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'release rollout history cannot be deleted'; END IF;
        IF ROW(NEW.id, NEW.tenant_id, NEW.agent_id, NEW.baseline_version_id, NEW.candidate_version_id,
            NEW.subject_ids, NEW.policy, NEW.policy_digest, NEW.definition_digest, NEW.created_by, NEW.created_at)
            IS DISTINCT FROM ROW(OLD.id, OLD.tenant_id, OLD.agent_id, OLD.baseline_version_id, OLD.candidate_version_id,
            OLD.subject_ids, OLD.policy, OLD.policy_digest, OLD.definition_digest, OLD.created_by, OLD.created_at) THEN
            RAISE EXCEPTION 'release rollout definition is immutable'; END IF;
        IF OLD.status = 'stopped' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'stopped release rollout is immutable'; END IF;
        IF NEW.revision < OLD.revision OR NEW.epoch < OLD.epoch THEN
            RAISE EXCEPTION 'release rollout revision and epoch cannot decrease'; END IF;
        RETURN NEW; END; $$""")
    op.execute("""CREATE TRIGGER release_rollout_definition_immutable BEFORE UPDATE OR DELETE ON agent_release_rollouts
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_rollout_definition()""")
    op.execute("""CREATE FUNCTION supportops_guard_conversation_release() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF ROW(NEW.agent_version_id, NEW.release_rollout_id, NEW.release_rollout_epoch, NEW.release_selection, NEW.release_selection_bucket)
            IS DISTINCT FROM ROW(OLD.agent_version_id, OLD.release_rollout_id, OLD.release_rollout_epoch, OLD.release_selection, OLD.release_selection_bucket) THEN
            RAISE EXCEPTION 'conversation release selection is immutable'; END IF; RETURN NEW; END; $$""")
    op.execute("""CREATE TRIGGER conversation_release_immutable BEFORE UPDATE ON conversations
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_conversation_release()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_release_rollouts) OR
        EXISTS (SELECT 1 FROM agent_release_rollout_events) OR EXISTS (SELECT 1 FROM conversations WHERE release_rollout_id IS NOT NULL) THEN
        RAISE EXCEPTION 'release rollout history must be preserved before downgrade'; END IF; END $$""")
    for trigger, table, function in (
        ("conversation_release_immutable", "conversations", "supportops_guard_conversation_release"),
        ("release_rollout_definition_immutable", "agent_release_rollouts", "supportops_guard_rollout_definition"),
        ("release_rollout_event_append_only", "agent_release_rollout_events", "supportops_guard_rollout_event"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_constraint("ck_conversation_release_selection", "conversations", type_="check")
    op.drop_index("ix_conversations_release_rollout_id", table_name="conversations")
    op.drop_constraint("fk_conversation_release_rollout", "conversations", type_="foreignkey")
    for column in ("release_selection_bucket", "release_selection", "release_rollout_epoch", "release_rollout_id"):
        op.drop_column("conversations", column)
    op.drop_table("agent_release_rollout_events")
    op.drop_table("agent_release_rollouts")
