"""增加 Run 通知 Outbox，并为已有 queued Run 建立恢复通知。"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0018"
down_revision: str | None = "20261010_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_notification_outbox",
        sa.Column("id", sa.Uuid(), nullable=False, comment="通知记录唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="通知所属租户。"),
        sa.Column("run_id", sa.Uuid(), nullable=False, comment="唯一关联业务 Run。"),
        sa.Column("event_key", sa.String(100), nullable=False, comment="稳定通知键，重复输入不创建新键。"),
        sa.Column("status", sa.String(20), nullable=False, comment="pending/delivered/dead_letter/obsolete。"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, comment="累计投递尝试数，人工重试不清零。"),
        sa.Column("cycle_attempt_count", sa.Integer(), nullable=False, comment="当前获准投递周期的尝试数。"),
        sa.Column("retry_count", sa.Integer(), nullable=False, comment="管理员显式重新投递周期数。"),
        sa.Column("revision", sa.Integer(), nullable=False, comment="通知状态乐观并发版本。"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False, comment="本次有限投递窗口开始时间。"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, comment="下一次允许投递或核对的时间。"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True, comment="最近一次 Redis 确认时间；不是业务执行成功。"),
        sa.Column("last_error_code", sa.String(100), nullable=True, comment="稳定投递错误码，不保存连接串或原始异常。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="通知创建时间。"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, comment="最近状态更新时间。"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.UniqueConstraint("run_id", name="uq_run_notification_outbox_run"),
        sa.UniqueConstraint("event_key", name="uq_run_notification_outbox_event"),
        sa.CheckConstraint("status IN ('pending', 'delivered', 'dead_letter', 'obsolete')", name="ck_run_notification_outbox_status"),
        sa.CheckConstraint("attempt_count >= 0 AND cycle_attempt_count >= 0 AND retry_count >= 0", name="ck_run_notification_outbox_attempts"),
        comment="持久化 Run 通知投递、退避、死信与授权重试事实；不保存消息正文。",
    )
    op.create_index("ix_run_notification_outbox_tenant_id", "run_notification_outbox", ["tenant_id"])
    op.create_index("ix_run_notification_outbox_due", "run_notification_outbox", ["status", "next_attempt_at", "id"])

    # 旧 queued Run 从迁移时刻获得有限投递窗口；不伪造旧尝试次数。
    now = datetime.now(UTC)
    runs = sa.table("agent_runs", sa.column("id", sa.Uuid()), sa.column("tenant_id", sa.Uuid()),
                    sa.column("status", sa.String()), sa.column("created_at", sa.DateTime(timezone=True)))
    outbox = sa.table("run_notification_outbox", *[
        sa.column(name) for name in ("id", "tenant_id", "run_id", "event_key", "status",
        "attempt_count", "cycle_attempt_count", "retry_count", "revision", "window_started_at",
        "next_attempt_at", "created_at", "updated_at")
    ])
    op.execute(outbox.insert().from_select(list(outbox.c.keys()), sa.select(
        runs.c.id, runs.c.tenant_id, runs.c.id,
        sa.literal("run:") + sa.cast(runs.c.id, sa.String()) + sa.literal(":queued"),
        sa.literal("pending"), sa.literal(0), sa.literal(0), sa.literal(0), sa.literal(1),
        sa.literal(now), sa.literal(now), runs.c.created_at, sa.literal(now),
    ).where(sa.func.lower(runs.c.status) == "queued")))


def downgrade() -> None:
    op.drop_index("ix_run_notification_outbox_due", table_name="run_notification_outbox")
    op.drop_index("ix_run_notification_outbox_tenant_id", table_name="run_notification_outbox")
    op.drop_table("run_notification_outbox")
