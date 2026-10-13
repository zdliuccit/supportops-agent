"""执行尝试、会话执行许可与固定输入序号；旧运行不伪造尝试。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0019"
down_revision: str | None = "20261012_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_attempts",
        sa.Column("id", sa.Uuid(), nullable=False, comment="执行尝试唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="执行尝试所属租户。"),
        sa.Column("run_id", sa.Uuid(), nullable=False, comment="关联业务 Run。"),
        sa.Column("generation", sa.Integer(), nullable=False, comment="本 Run 单调递增的 fencing generation。"),
        sa.Column("owner_id", sa.String(100), nullable=False, comment="领取该尝试的 Worker 启动实例标识。"),
        sa.Column("status", sa.String(20), nullable=False, comment="running/completed/failed/cancelled/expired/quarantined。"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, comment="当前执行者最后成功续租时间。"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False, comment="超时后失效，旧执行者禁止提交。"),
        sa.Column("checkpoint_thread_id", sa.String(300), nullable=False, comment="该尝试独占的 checkpoint thread。"),
        sa.Column("checkpoint_id", sa.String(100), nullable=True, comment="成功产品提交对应的 checkpoint ID。"),
        sa.Column("replay_policy", sa.String(20), nullable=False, comment="只有已证明只读的尝试允许自动重放。"),
        sa.Column("side_effect_state", sa.String(20), nullable=False, comment="副作用事实：none/unknown/confirmed；未知必须人工对账。"),
        sa.Column("error_code", sa.String(100), nullable=True, comment="尝试失败/过期/隔离的稳定错误码。"),
        sa.Column("recovery_reason", sa.String(100), nullable=True, comment="创建此恢复尝试的原因。"),
        sa.Column("metrics", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False, comment="本尝试脱敏计量，未知 usage 不记为零。"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, comment="该尝试开始时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="该尝试结束或隔离时间。"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.UniqueConstraint("run_id", "generation", name="uq_run_attempt_generation"),
        sa.UniqueConstraint("checkpoint_thread_id", name="uq_run_attempt_checkpoint_thread"),
        sa.CheckConstraint("generation >= 1", name="ck_run_attempt_generation"),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed', 'cancelled', 'expired', 'quarantined')", name="ck_run_attempt_status"),
        sa.CheckConstraint("replay_policy IN ('read_only', 'manual')", name="ck_run_attempt_replay"),
        sa.CheckConstraint("side_effect_state IN ('none', 'unknown', 'confirmed')", name="ck_run_attempt_side_effect"),
        comment="独立执行尝试和 fencing 租约；未提交 checkpoint 与业务事实隔离。",
    )
    op.create_index("ix_run_attempts_expiry", "run_attempts", ["status", "lease_expires_at"])
    op.create_index("ix_run_attempts_tenant_run", "run_attempts", ["tenant_id", "run_id", "generation"])
    fields = {
        "conversations": [
            sa.Column("next_input_sequence", sa.Integer(), nullable=False, server_default="1", comment="下次用户输入的会话内序号，在会话行锁内递增。"),
            sa.Column("active_run_id", sa.Uuid(), nullable=True, comment="当前持有会话执行许可的 Run。"),
            sa.Column("active_attempt_id", sa.Uuid(), nullable=True, comment="当前会话执行许可对应的尝试。"),
            sa.Column("checkpoint_thread_id", sa.String(300), nullable=True, comment="最近产品事务成功提交的隔离 checkpoint thread。"),
            sa.Column("checkpoint_id", sa.String(100), nullable=True, comment="最近有效 checkpoint 的精确提交指针。"),
            sa.Column("checkpoint_input_sequence", sa.Integer(), nullable=True, comment="有效 checkpoint 对应的已完成输入序号。"),
        ],
        "messages": [sa.Column("turn_sequence", sa.Integer(), nullable=True, comment="用户输入及对应助手回答共用的轮次序号；无法关联的旧消息为空。")],
        "agent_runs": [
            sa.Column("input_sequence", sa.Integer(), nullable=True, comment="本轮固定输入边界，不读取后续排队输入。"),
            sa.Column("execution_generation", sa.Integer(), nullable=False, server_default="0", comment="递增的执行 fencing generation；旧数据不伪造尝试。"),
            sa.Column("current_attempt_id", sa.Uuid(), nullable=True, comment="当前获准执行的尝试。"),
            sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0", comment="执行恢复次数，独立于模型重试次数；人工恢复不绕过上限。"),
            sa.Column("recovery_reason", sa.String(100), nullable=True, comment="最近恢复或人工诊断的稳定原因码。"),
        ],
        "run_events": [sa.Column("attempt_id", sa.Uuid(), nullable=True, comment="产生执行事件的尝试；控制面和旧事件为空。")],
        "agent_run_observations": [sa.Column("attempt_id", sa.Uuid(), nullable=True, comment="产生该观测的执行尝试；旧观测不伪造关联。")],
    }
    for table, columns in fields.items():
        for column in columns:
            op.add_column(table, column)
    for name, source, field, target in (
        ("fk_conversation_active_run", "conversations", "active_run_id", "agent_runs"),
        ("fk_conversation_active_attempt", "conversations", "active_attempt_id", "run_attempts"),
        ("fk_agent_run_current_attempt", "agent_runs", "current_attempt_id", "run_attempts"),
        ("fk_run_events_attempt", "run_events", "attempt_id", "run_attempts"),
        ("fk_run_observations_attempt", "agent_run_observations", "attempt_id", "run_attempts"),
    ):
        op.create_foreign_key(name, source, target, [field], ["id"])

    # 只新增确定顺序和关联，不修改旧正文、事件或执行次数。
    op.execute(sa.text("""
        WITH inputs AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY tenant_id, conversation_id ORDER BY created_at, id
            ) AS sequence FROM messages WHERE LOWER(role) = 'user'
        ) UPDATE messages AS m SET turn_sequence = inputs.sequence FROM inputs WHERE m.id = inputs.id
    """))
    op.execute(sa.text("""
        UPDATE agent_runs AS r SET input_sequence = m.turn_sequence
        FROM messages AS m WHERE r.input_message_id = m.id AND r.tenant_id = m.tenant_id
    """))
    op.execute(sa.text("""
        UPDATE messages AS m SET turn_sequence = r.input_sequence FROM agent_runs AS r
        WHERE LOWER(m.role) = 'assistant' AND m.tenant_id = r.tenant_id
        AND m.conversation_id = r.conversation_id
        AND m.idempotency_key = 'run:' || CAST(r.id AS TEXT) || ':assistant'
    """))
    op.execute(sa.text("""
        UPDATE conversations AS c SET next_input_sequence = COALESCE((
            SELECT MAX(m.turn_sequence) + 1 FROM messages AS m
            WHERE m.tenant_id = c.tenant_id AND m.conversation_id = c.id AND LOWER(m.role) = 'user'
        ), 1)
    """))
    op.execute(sa.text("""
        UPDATE agent_runs SET recovery_reason = 'LEGACY_EXECUTOR_CONFIRMATION_REQUIRED'
        WHERE LOWER(status) IN ('running', 'cancelling')
    """))
    op.create_unique_constraint("uq_messages_turn_role", "messages", ["tenant_id", "conversation_id", "turn_sequence", "role"])
    op.create_unique_constraint("uq_agent_runs_input_sequence", "agent_runs", ["tenant_id", "conversation_id", "input_sequence"])


def downgrade() -> None:
    op.drop_constraint("uq_agent_runs_input_sequence", "agent_runs", type_="unique")
    op.drop_constraint("uq_messages_turn_role", "messages", type_="unique")
    for table, fields in (
        ("conversations", ("active_attempt_id", "active_run_id", "checkpoint_id", "checkpoint_thread_id", "checkpoint_input_sequence", "next_input_sequence")),
        ("run_events", ("attempt_id",)), ("agent_run_observations", ("attempt_id",)),
        ("agent_runs", ("current_attempt_id", "execution_generation", "input_sequence", "recovery_count", "recovery_reason")),
    ):
        for field in fields:
            op.drop_column(table, field)
    op.drop_column("messages", "turn_sequence")
    op.drop_index("ix_run_attempts_tenant_run", table_name="run_attempts")
    op.drop_index("ix_run_attempts_expiry", table_name="run_attempts")
    op.drop_table("run_attempts")
