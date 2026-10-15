"""固定凭据 provider、历史提示和永久撤销；不虚构旧解封/撤销事实。"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0037"
down_revision: str | None = "20261013_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_credential_revisions", sa.Column("provider", sa.String(64), nullable=False, server_default="unknown", comment="历史 revision 实际密封 provider。"))
    op.add_column("model_credential_revisions", sa.Column("masked_hint", sa.String(32), nullable=False, server_default="••••", comment="历史脱敏提示；未知不补造。"))
    op.add_column("model_credential_revisions", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, comment="永久撤销时间。"))
    op.add_column("model_credential_revisions", sa.Column("revoked_by", sa.Uuid(), nullable=True, comment="撤销管理员。"))
    op.add_column("model_credential_revisions", sa.Column("revocation_reason", sa.String(500), nullable=True, comment="脱敏撤销说明。"))
    op.create_foreign_key("fk_credential_revision_revoked_by", "model_credential_revisions", "users", ["revoked_by"], ["id"])
    # 既有实现只支持本地密封；只确认已知格式，不把未知 provider 解释成可用。
    op.execute("""UPDATE model_credential_revisions r SET provider = 'local_envelope_v1'
        FROM model_credentials c WHERE r.credential_id = c.id AND r.tenant_id = c.tenant_id
        AND c.provider = 'local_envelope_v1'""")
    op.execute("""UPDATE model_credential_revisions r SET masked_hint = c.masked_hint
        FROM model_credentials c WHERE r.credential_id = c.id AND r.tenant_id = c.tenant_id AND r.revision = c.revision""")
    op.alter_column("model_credential_revisions", "provider", server_default=None)
    op.alter_column("model_credential_revisions", "masked_hint", server_default=None)


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM model_credential_revisions WHERE revoked_at IS NOT NULL OR provider <> 'local_envelope_v1') THEN
            RAISE EXCEPTION 'revoked or unsupported credential revisions prevent unsafe downgrade';
        END IF;
    END $$""")
    op.drop_constraint("fk_credential_revision_revoked_by", "model_credential_revisions", type_="foreignkey")
    for column in ("revocation_reason", "revoked_by", "revoked_at", "masked_hint", "provider"):
        op.drop_column("model_credential_revisions", column)
