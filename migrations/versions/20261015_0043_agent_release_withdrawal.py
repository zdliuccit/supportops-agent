"""回滚撤回独立于不可变版本内容；不改旧会话、审批、动作或回执。"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "20261015_0043"
down_revision: str | None = "20261014_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_versions", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True,
        comment="回滚后撤回该版本的新激活/新写动作时间；保留历史与只读对账，不解除。"))
    op.add_column("agent_versions", sa.Column("withdrawn_by", sa.Uuid(), nullable=True,
        comment="执行回滚并撤回该版本的管理员；与撤回时间成对保存。"))
    op.create_foreign_key("fk_agent_version_withdrawal_actor", "agent_versions", "users", ["withdrawn_by"], ["id"])
    op.create_check_constraint("ck_agent_version_withdrawal_pair", "agent_versions",
        "(withdrawn_at IS NULL AND withdrawn_by IS NULL) OR (withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL)")
    op.execute("""CREATE FUNCTION supportops_guard_release_withdrawal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN IF OLD.withdrawn_at IS NOT NULL AND
            (NEW.withdrawn_at IS DISTINCT FROM OLD.withdrawn_at OR NEW.withdrawn_by IS DISTINCT FROM OLD.withdrawn_by) THEN
            RAISE EXCEPTION 'release withdrawal cannot be cleared or overwritten'; END IF; RETURN NEW; END; $$""")
    op.execute("""CREATE TRIGGER agent_release_withdrawal_immutable BEFORE UPDATE ON agent_versions
        FOR EACH ROW EXECUTE FUNCTION supportops_guard_release_withdrawal()""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_versions WHERE withdrawn_at IS NOT NULL) THEN
        RAISE EXCEPTION 'release withdrawal facts must be preserved before downgrade'; END IF; END $$""")
    op.execute("DROP TRIGGER agent_release_withdrawal_immutable ON agent_versions")
    op.execute("DROP FUNCTION supportops_guard_release_withdrawal()")
    op.drop_constraint("ck_agent_version_withdrawal_pair", "agent_versions", type_="check")
    op.drop_constraint("fk_agent_version_withdrawal_actor", "agent_versions", type_="foreignkey")
    op.drop_column("agent_versions", "withdrawn_by")
    op.drop_column("agent_versions", "withdrawn_at")
