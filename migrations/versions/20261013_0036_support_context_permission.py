"""Mock 排查上下文的独立人员授权，不覆盖知识 ACL。"""
from collections.abc import Sequence
from alembic import op

revision: str = "20261013_0036"
down_revision: str | None = "20261013_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_mock_resource_permission", "mock_resource_grants", type_="check")
    op.create_check_constraint("ck_mock_resource_permission", "mock_resource_grants",
        "permission IN ('approve', 'ticket_handle', 'support_context_read')")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM mock_resource_grants WHERE permission = 'support_context_read') THEN
            RAISE EXCEPTION 'support context grants must be handled before downgrade';
        END IF;
    END $$""")
    op.drop_constraint("ck_mock_resource_permission", "mock_resource_grants", type_="check")
    op.create_check_constraint("ck_mock_resource_permission", "mock_resource_grants", "permission IN ('approve', 'ticket_handle')")
