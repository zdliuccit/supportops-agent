"""精简部门模型并移除冗余公司根节点。

Revision ID: 20260904_0006
Revises: 20260904_0005
Create Date: 2026-09-04 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0006"
down_revision: str | None = "20260904_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """保留真实部门层级和归属，移除公司根节点与不再使用的业务字段。"""
    # 公司资料已经由 tenants 独立维护；旧公司根节点不再作为部门返回。
    op.execute(
        "UPDATE users SET organization_unit_id = NULL "
        "WHERE organization_unit_id IN "
        "(SELECT id FROM organization_units WHERE unit_type = 'company')"
    )
    op.execute(
        "UPDATE organization_units AS child SET parent_id = NULL "
        "WHERE parent_id IN "
        "(SELECT id FROM organization_units WHERE unit_type = 'company')"
    )
    op.execute("DELETE FROM organization_units WHERE unit_type = 'company'")

    op.drop_constraint(
        "uq_organization_units_tenant_code", "organization_units", type_="unique"
    )
    op.drop_column("organization_units", "code")
    op.drop_column("organization_units", "unit_type")
    op.drop_column("organization_units", "sort_order")
    op.drop_column("organization_units", "status")

    op.create_table_comment(
        "organization_units",
        "企业部门节点；父子关系形成租户内树，删除不得级联用户或子部门。",
        existing_comment="公司组织架构节点；父子关系形成租户内树，删除不得级联用户或子节点。",
    )
    op.alter_column("organization_units", "id", comment="部门唯一标识。")
    op.alter_column("organization_units", "tenant_id", comment="部门所属租户。")
    op.alter_column("organization_units", "parent_id", comment="上级部门；顶级部门为空。")
    op.alter_column("organization_units", "name", comment="部门名称。")
    op.alter_column("organization_units", "created_at", comment="部门创建时间。")
    op.alter_column("organization_units", "updated_at", comment="部门最近更新时间。")
    op.alter_column("users", "organization_unit_id", comment="用户所属部门；未分配部门时为空。")


def downgrade() -> None:
    """恢复旧字段结构；已删除的公司根节点及其用户归属无法自动还原。"""
    op.add_column(
        "organization_units",
        sa.Column("code", sa.String(length=100), nullable=True, comment="租户内唯一组织代码。"),
    )
    op.execute(
        "UPDATE organization_units SET code = "
        "upper(substr(replace(CAST(id AS VARCHAR), '-', ''), 1, 16))"
    )
    op.alter_column("organization_units", "code", nullable=False)
    op.create_unique_constraint(
        "uq_organization_units_tenant_code", "organization_units", ["tenant_id", "code"]
    )
    op.add_column(
        "organization_units",
        sa.Column(
            "unit_type",
            sa.String(length=32),
            server_default="department",
            nullable=False,
            comment="组织类型，例如 company、department 或 team。",
        ),
    )
    op.add_column(
        "organization_units",
        sa.Column(
            "sort_order",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="同级节点显示顺序。",
        ),
    )
    op.add_column(
        "organization_units",
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
            comment="组织单元状态。",
        ),
    )
    op.create_table_comment(
        "organization_units",
        "公司组织架构节点；父子关系形成租户内树，删除不得级联用户或子节点。",
        existing_comment="企业部门节点；父子关系形成租户内树，删除不得级联用户或子部门。",
    )
    op.alter_column("organization_units", "id", comment="组织单元唯一标识。")
    op.alter_column("organization_units", "tenant_id", comment="组织单元所属租户。")
    op.alter_column("organization_units", "parent_id", comment="父组织单元；根组织为空。")
    op.alter_column("organization_units", "name", comment="组织单元显示名称。")
    op.alter_column("organization_units", "created_at", comment="组织单元创建时间。")
    op.alter_column("organization_units", "updated_at", comment="组织单元最近更新时间。")
    op.alter_column(
        "users", "organization_unit_id", comment="用户所属组织单元；历史或未分配用户可为空。"
    )
