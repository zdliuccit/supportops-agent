"""增加租户可管理的受控工具目录。"""

import json
from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260911_0009"
down_revision: str | None = "20260908_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BUILTIN_TOOLS = (
    (
        "support_ticket_lookup",
        "查询支持工单",
        "根据工单编号读取当前租户内的工单摘要。",
        "support_ticket_lookup",
        ["employee", "agent_user", "platform_admin"],
    ),
    (
        "current_identity_summary",
        "读取当前身份摘要",
        "读取当前请求的租户、用户和角色摘要，不访问外部系统。",
        "current_identity_summary",
        [],
    ),
)


def _stable_id(tenant_id: UUID, tool_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"supportops:{tenant_id}:tool:{tool_id}")


def upgrade() -> None:
    op.create_table(
        "tool_catalog_entries",
        sa.Column("id", sa.Uuid(), nullable=False, comment="工具目录条目 ID。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="工具目录所属租户。"),
        sa.Column("tool_id", sa.String(length=100), nullable=False, comment="稳定工具标识。"),
        sa.Column("name", sa.String(length=200), nullable=False, comment="工具显示名称。"),
        sa.Column("description", sa.Text(), nullable=False, comment="工具能力说明。"),
        sa.Column(
            "implementation_key",
            sa.String(length=100),
            nullable=False,
            comment="服务端实现注册键，不接受客户端代码。",
        ),
        sa.Column(
            "required_roles",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="调用工具所需角色。",
        ),
        sa.Column("risk_level", sa.String(length=16), nullable=False, comment="工具风险级别。"),
        sa.Column("version", sa.Integer(), nullable=False, comment="工具目录元数据版本。"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, comment="是否允许新 Agent 绑定。"),
        sa.Column("created_by", sa.Uuid(), nullable=False, comment="创建人。"),
        sa.Column("updated_by", sa.Uuid(), nullable=False, comment="最近更新人。"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, comment="工具目录创建时间。"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="工具目录最近更新时间。",
        ),
        sa.CheckConstraint("risk_level IN ('low', 'medium', 'high')", name="ck_tool_risk_level"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "tool_id"),
        comment="Agent 可绑定的受控工具目录；不允许上传或执行任意代码。",
    )
    op.create_index(
        op.f("ix_tool_catalog_entries_tenant_id"),
        "tool_catalog_entries",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_catalog_entries_is_enabled"),
        "tool_catalog_entries",
        ["is_enabled"],
        unique=False,
    )

    connection = op.get_bind()
    tenants = connection.execute(sa.text("SELECT id FROM tenants")).scalars().all()
    for tenant_value in tenants:
        tenant_id = UUID(str(tenant_value))
        creator_id = connection.execute(
            sa.text(
                "SELECT id FROM users WHERE tenant_id = :tenant_id "
                "ORDER BY created_at, id LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        ).scalar_one_or_none()
        if creator_id is None:
            continue
        for tool_id, name, description, implementation_key, required_roles in BUILTIN_TOOLS:
            connection.execute(
                sa.text(
                    "INSERT INTO tool_catalog_entries "
                    "(id, tenant_id, tool_id, name, description, implementation_key, "
                    "required_roles, risk_level, version, is_enabled, created_by, updated_by, "
                    "created_at, updated_at) VALUES "
                    "(:id, :tenant_id, :tool_id, :name, :description, :implementation_key, "
                    "CAST(:required_roles AS jsonb), 'low', 1, TRUE, :creator_id, :creator_id, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": _stable_id(tenant_id, tool_id),
                    "tenant_id": tenant_id,
                    "tool_id": tool_id,
                    "name": name,
                    "description": description,
                    "implementation_key": implementation_key,
                    "required_roles": json.dumps(required_roles),
                    "creator_id": creator_id,
                },
            )


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_catalog_entries_is_enabled"), table_name="tool_catalog_entries")
    op.drop_index(op.f("ix_tool_catalog_entries_tenant_id"), table_name="tool_catalog_entries")
    op.drop_table("tool_catalog_entries")
