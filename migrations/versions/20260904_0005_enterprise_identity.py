"""重建企业账号身份并增加公司组织架构。

Revision ID: 20260904_0005
Revises: 20260904_0004
Create Date: 2026-09-04 16:00:00

本迁移根据产品决策主动清空既有业务数据，不提供历史数据恢复能力。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_0005"
down_revision: str | None = "20260904_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # 用户已明确要求不保留历史数据；租户是全部业务数据的根外键。
        op.execute("TRUNCATE TABLE tenants CASCADE")
        # LangGraph checkpoint 不属于 tenants 外键树，需要单独重建 Schema。
        op.execute("DROP SCHEMA IF EXISTS supportops_checkpoints CASCADE")
        op.execute("CREATE SCHEMA supportops_checkpoints")
        op.execute(
            "COMMENT ON SCHEMA supportops_checkpoints IS "
            "'LangGraph 持久化 checkpoint、内部消息和工具轨迹的独立 Schema。'"
        )

    op.add_column(
        "tenants",
        sa.Column(
            "slug",
            sa.String(length=100),
            nullable=True,
            comment="公司唯一短名，用于稳定识别租户。",
        ),
    )
    op.add_column(
        "tenants",
        sa.Column("logo_url", sa.String(length=2000), nullable=True, comment="公司 Logo 地址。"),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "contact_email", sa.String(length=320), nullable=True, comment="公司主要联系邮箱。"
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="公司资料最近更新时间。",
        ),
    )
    op.create_unique_constraint("uq_tenants_slug", "tenants", ["slug"])

    op.create_table(
        "organization_units",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="组织单元唯一标识。",
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="组织单元所属租户。",
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="父组织单元；根组织为空。",
        ),
        sa.Column("name", sa.String(length=200), nullable=False, comment="组织单元显示名称。"),
        sa.Column("code", sa.String(length=100), nullable=False, comment="租户内唯一组织代码。"),
        sa.Column(
            "unit_type",
            sa.String(length=32),
            server_default="department",
            nullable=False,
            comment="组织类型，例如 company、department 或 team。",
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="同级节点显示顺序。",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
            comment="组织单元状态。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="组织单元创建时间。",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="组织单元最近更新时间。",
        ),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_org_unit_not_self"),
        sa.ForeignKeyConstraint(["parent_id"], ["organization_units.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_organization_units_tenant_code"),
        sa.UniqueConstraint(
            "tenant_id", "parent_id", "name", name="uq_organization_units_sibling_name"
        ),
        comment="公司组织架构节点；父子关系形成租户内树，删除不得级联用户或子节点。",
    )
    op.create_index("ix_organization_units_tenant_id", "organization_units", ["tenant_id"])
    op.create_index("ix_organization_units_parent_id", "organization_units", ["parent_id"])

    op.drop_constraint("users_tenant_id_external_subject_key", "users", type_="unique")
    op.drop_column("users", "external_subject")
    op.add_column(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False, comment="规范化的小写登录邮箱。"),
    )
    op.add_column(
        "users",
        sa.Column(
            "password_hash",
            sa.Text(),
            nullable=False,
            comment="版本化 scrypt 密码哈希；不保存明文密码。",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "organization_unit_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="用户所属组织单元；历史或未分配用户可为空。",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "job_title",
            sa.String(length=200),
            server_default="",
            nullable=False,
            comment="用户在公司内的职位名称。",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "phone",
            sa.String(length=64),
            server_default="",
            nullable=False,
            comment="用户联系电话。",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="用户资料最近更新时间。",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="最近一次密码登录成功时间。",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="最近一次设置或重置密码时间。",
        ),
    )
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_organization_unit_id", "users", ["organization_unit_id"])
    op.create_foreign_key(
        "fk_users_organization_unit_id",
        "users",
        "organization_units",
        ["organization_unit_id"],
        ["id"],
    )

    if bind.dialect.name == "postgresql":
        op.execute("COMMENT ON TABLE tenants IS '企业公司资料和全平台租户隔离根边界。'")
        op.execute("COMMENT ON TABLE users IS '租户内本地登录用户、组织归属及可信角色。'")
        op.execute("COMMENT ON COLUMN users.created_at IS '系统用户创建时间。'")


def downgrade() -> None:
    op.drop_constraint("fk_users_organization_unit_id", "users", type_="foreignkey")
    op.drop_index("ix_users_organization_unit_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "phone")
    op.drop_column("users", "job_title")
    op.drop_column("users", "organization_unit_id")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "email")
    op.add_column(
        "users", sa.Column("external_subject", sa.String(length=200), nullable=True)
    )
    op.execute("UPDATE users SET external_subject = CAST(id AS VARCHAR)")
    op.alter_column("users", "external_subject", nullable=False)
    op.create_unique_constraint(
        "users_tenant_id_external_subject_key",
        "users",
        ["tenant_id", "external_subject"],
    )
    op.drop_index("ix_organization_units_parent_id", table_name="organization_units")
    op.drop_index("ix_organization_units_tenant_id", table_name="organization_units")
    op.drop_table("organization_units")
    op.drop_constraint("uq_tenants_slug", "tenants", type_="unique")
    op.drop_column("tenants", "updated_at")
    op.drop_column("tenants", "contact_email")
    op.drop_column("tenants", "logo_url")
    op.drop_column("tenants", "slug")
