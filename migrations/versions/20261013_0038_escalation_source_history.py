"""升级来源变化追加历史，不把早期空包当作恢复后的完整排查。"""
import hashlib
import json
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0038"
down_revision: str | None = "20261013_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticket_escalation_packages", sa.Column("source_digest", sa.String(64), nullable=True, comment="除捕获时间外的来源摘要。"))
    table = sa.table("ticket_escalation_packages", sa.column("id", sa.Uuid()), sa.column("payload", sa.JSON()), sa.column("source_digest", sa.String(64)))
    connection = op.get_bind()
    cursor = None
    while True:
        statement = sa.select(table.c.id, table.c.payload).order_by(table.c.id).limit(200)
        if cursor is not None:
            statement = statement.where(table.c.id > cursor)
        rows = connection.execute(statement).all()
        if not rows:
            break
        for row in rows:
            data = json.loads(row.payload) if isinstance(row.payload, str) else row.payload
            source = {key: value for key, value in data.items() if key != "captured_at"}
            digest = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            connection.execute(sa.update(table).where(table.c.id == row.id).values(source_digest=digest))
        cursor = rows[-1].id
    op.alter_column("ticket_escalation_packages", "source_digest", nullable=False)
    op.drop_constraint("uq_ticket_escalation_source", "ticket_escalation_packages", type_="unique")
    op.create_unique_constraint("uq_ticket_escalation_source", "ticket_escalation_packages", ["action_id", "run_id", "source_digest"])


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM ticket_escalation_packages GROUP BY action_id, run_id HAVING count(*) > 1) THEN
            RAISE EXCEPTION 'multiple escalation source facts prevent unsafe downgrade';
        END IF;
    END $$""")
    op.drop_constraint("uq_ticket_escalation_source", "ticket_escalation_packages", type_="unique")
    op.create_unique_constraint("uq_ticket_escalation_source", "ticket_escalation_packages", ["action_id", "run_id"])
    op.drop_column("ticket_escalation_packages", "source_digest")
