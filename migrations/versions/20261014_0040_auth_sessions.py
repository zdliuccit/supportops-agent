"""登录会话引用与许可代次；旧 JWT 不虚构会话，升级后重新登录。"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0040"
down_revision: str | None = "20261014_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_generation", sa.Integer(), nullable=False, server_default="0", comment="登录许可代次，安全身份变化递增。"))
    op.create_table("auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="登录会话 UUID，对应 JWT jti。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="固定租户。"),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, comment="固定用户。"),
        sa.Column("auth_generation", sa.Integer(), nullable=False, comment="签发时许可代次。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="创建时间。"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, comment="服务器固定过期时间。"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, comment="永久撤销时间。"),
        sa.Column("revoked_by", sa.Uuid(), sa.ForeignKey("users.id", name="fk_auth_session_revoker"), nullable=True, comment="实际撤销人员。"),
        sa.Column("revocation_code", sa.String(32), nullable=True, comment="固定撤销原因。"),
        sa.CheckConstraint("auth_generation >= 0", name="ck_auth_sessions_generation"),
        comment="短期 Bearer 登录会话；不保存令牌、密码、IP 或完整设备标识。")
    op.create_index("ix_auth_sessions_owner_created", "auth_sessions", ["tenant_id", "user_id", "created_at"])


def downgrade() -> None:
    # 降级不能让已撤销令牌再次成为有效身份，也不能删除会话历史。
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM auth_sessions) OR EXISTS (SELECT 1 FROM users WHERE auth_generation <> 0) THEN
            RAISE EXCEPTION 'auth session revocation history must be preserved before downgrade';
        END IF;
    END $$""")
    op.drop_table("auth_sessions")
    op.drop_column("users", "auth_generation")
