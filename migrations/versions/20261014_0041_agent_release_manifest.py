"""服务器生成不可变发布清单；旧版本不回填当前源码或虚构评测。"""
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "20261014_0041"
down_revision: str | None = "20261014_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_versions", sa.Column("release_manifest", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        nullable=True, comment="发布时服务器生成的版本/源码/策略摘要；无秘密或正文，旧版本为空。"))
    op.add_column("agent_versions", sa.Column("release_manifest_digest", sa.String(64), nullable=True,
        comment="发布清单 SHA-256；不等于评测通过或执行授权。"))
    op.create_check_constraint("ck_agent_versions_manifest_pair", "agent_versions",
        "(release_manifest IS NULL AND release_manifest_digest IS NULL) OR (release_manifest IS NOT NULL AND release_manifest_digest IS NOT NULL)")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_versions WHERE release_manifest IS NOT NULL) THEN
        RAISE EXCEPTION 'release manifest history must be preserved before downgrade'; END IF; END $$""")
    op.drop_constraint("ck_agent_versions_manifest_pair", "agent_versions", type_="check")
    op.drop_column("agent_versions", "release_manifest_digest")
    op.drop_column("agent_versions", "release_manifest")
