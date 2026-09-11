"""将模型端点改造成可启停的多模型连接并记录逐模型测试。

Revision ID: 20260907_0007
Revises: 20260904_0006
Create Date: 2026-09-07 16:00:00
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_0007"
down_revision: str | None = "20260904_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """迁移既有单模型端点，并建立稳定模型和测试历史表。"""
    op.add_column(
        "model_endpoints",
        sa.Column(
            "provider_preset",
            sa.String(length=64),
            nullable=True,
            comment="创建时选择的供应商预设标识；自定义配置为空。",
        ),
    )
    op.add_column(
        "model_endpoints",
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment="当前模型连接是否允许 Agent 新绑定和使用。",
        ),
    )
    op.execute("UPDATE model_endpoints SET is_enabled = (status = 'active')")
    op.create_index("ix_model_endpoints_is_enabled", "model_endpoints", ["is_enabled"])
    op.drop_index("ix_model_endpoints_status", table_name="model_endpoints")
    op.drop_column("model_endpoints", "status")
    op.alter_column("model_endpoints", "is_enabled", server_default=None)

    op.create_table(
        "model_endpoint_models",
        sa.Column("id", sa.Uuid(), nullable=False, comment="稳定模型唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="稳定模型所属租户。"),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False, comment="稳定模型所属连接端点。"),
        sa.Column(
            "upstream_model_id",
            sa.String(length=300),
            nullable=False,
            comment="发送给供应商的真实模型 ID。",
        ),
        sa.Column(
            "display_name",
            sa.String(length=300),
            nullable=False,
            server_default="",
            comment="管理端和 Agent 选择器中的模型显示名称。",
        ),
        sa.Column(
            "badge",
            sa.String(length=16),
            nullable=False,
            server_default="",
            comment="用于区分同名模型的短后缀或 Emoji。",
        ),
        sa.Column(
            "current_version_id",
            sa.Uuid(),
            nullable=True,
            comment="该模型当前使用的不可变调用配置版本。",
        ),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="从当前配置移除的时间；为空表示仍可选择。",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="稳定模型创建时间。",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="稳定模型更新时间。",
        ),
        sa.ForeignKeyConstraint(["endpoint_id"], ["model_endpoints.id"]),
        sa.ForeignKeyConstraint(
            ["current_version_id"],
            ["model_endpoint_versions.id"],
            name="fk_model_endpoint_models_current_version_id",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint_id", "upstream_model_id"),
        comment="模型端点中的稳定模型，保存展示信息并指向当前不可变调用版本。",
    )
    op.create_index(
        "ix_model_endpoint_models_endpoint_id", "model_endpoint_models", ["endpoint_id"]
    )
    op.create_index("ix_model_endpoint_models_tenant_id", "model_endpoint_models", ["tenant_id"])

    op.add_column(
        "model_endpoint_versions",
        sa.Column(
            "endpoint_model_id",
            sa.Uuid(),
            nullable=True,
            comment="所属稳定模型；历史单模型版本迁移时允许为空。",
        ),
    )
    op.create_index(
        "ix_model_endpoint_versions_endpoint_model_id",
        "model_endpoint_versions",
        ["endpoint_model_id"],
    )
    op.create_foreign_key(
        "fk_model_endpoint_versions_endpoint_model_id",
        "model_endpoint_versions",
        "model_endpoint_models",
        ["endpoint_model_id"],
        ["id"],
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT e.id AS endpoint_id, e.tenant_id, e.active_version_id, "
            "v.remote_model_name FROM model_endpoints e "
            "JOIN model_endpoint_versions v ON v.id = e.active_version_id"
        )
    ).mappings()
    for row in rows:
        model_id = uuid4()
        connection.execute(
            sa.text(
                "INSERT INTO model_endpoint_models "
                "(id, tenant_id, endpoint_id, upstream_model_id, display_name, badge, "
                "current_version_id, archived_at, created_at, updated_at) "
                "VALUES (:id, :tenant_id, :endpoint_id, :upstream_model_id, '', '', "
                ":current_version_id, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": model_id,
                "tenant_id": row["tenant_id"],
                "endpoint_id": row["endpoint_id"],
                "upstream_model_id": row["remote_model_name"],
                "current_version_id": row["active_version_id"],
            },
        )
        connection.execute(
            sa.text(
                "UPDATE model_endpoint_versions SET endpoint_model_id = :model_id "
                "WHERE endpoint_id = :endpoint_id AND remote_model_name = :upstream_model_id"
            ),
            {
                "model_id": model_id,
                "endpoint_id": row["endpoint_id"],
                "upstream_model_id": row["remote_model_name"],
            },
        )

    op.create_table(
        "model_endpoint_test_runs",
        sa.Column("id", sa.Uuid(), nullable=False, comment="测试运行唯一标识。"),
        sa.Column("tenant_id", sa.Uuid(), nullable=False, comment="测试运行所属租户。"),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False, comment="被测试的模型连接端点。"),
        sa.Column("endpoint_model_id", sa.Uuid(), nullable=False, comment="被测试的稳定模型。"),
        sa.Column(
            "model_version_id", sa.Uuid(), nullable=False, comment="本次测试固定的模型配置版本。"
        ),
        sa.Column(
            "credential_revision",
            sa.Integer(),
            nullable=False,
            comment="本次测试使用的凭据修订号。",
        ),
        sa.Column(
            "config_digest",
            sa.String(length=64),
            nullable=False,
            comment="模型配置和凭据 revision 的稳定摘要。",
        ),
        sa.Column("status", sa.String(length=32), nullable=False, comment="测试执行状态。"),
        sa.Column(
            "stage", sa.String(length=32), nullable=False, comment="页面当前展示的测试阶段。"
        ),
        sa.Column(
            "request_host", sa.String(length=500), nullable=False, comment="允许展示的目标主机名。"
        ),
        sa.Column(
            "request_path",
            sa.String(length=500),
            nullable=False,
            comment="允许展示的协议请求路径。",
        ),
        sa.Column(
            "provider_status", sa.Integer(), nullable=True, comment="供应商返回的 HTTP 状态码。"
        ),
        sa.Column("response_headers_ms", sa.Integer(), nullable=True, comment="收到响应头的耗时。"),
        sa.Column(
            "first_content_ms", sa.Integer(), nullable=True, comment="收到首个响应内容的耗时。"
        ),
        sa.Column("total_ms", sa.Integer(), nullable=True, comment="完整测试总耗时。"),
        sa.Column("milestones", sa.JSON(), nullable=False, comment="各真实测试阶段的安全摘要。"),
        sa.Column("error_code", sa.String(length=100), nullable=True, comment="稳定脱敏失败代码。"),
        sa.Column(
            "error_message", sa.String(length=500), nullable=True, comment="截断脱敏失败说明。"
        ),
        sa.Column(
            "correlation_id", sa.String(length=100), nullable=False, comment="日志和审计关联标识。"
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False, comment="发起测试的平台管理员。"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True, comment="测试实际开始时间。"
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="测试进入终态的时间。",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, comment="测试记录创建时间。"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["endpoint_id"], ["model_endpoints.id"]),
        sa.ForeignKeyConstraint(["endpoint_model_id"], ["model_endpoint_models.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_endpoint_versions.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="逐模型连通性测试历史、阶段耗时和脱敏诊断结果。",
    )
    op.create_index(
        "ix_model_endpoint_test_runs_model_created",
        "model_endpoint_test_runs",
        ["endpoint_model_id", "created_at"],
    )
    for column in (
        "tenant_id",
        "endpoint_id",
        "endpoint_model_id",
        "model_version_id",
        "status",
        "correlation_id",
    ):
        op.create_index(
            f"ix_model_endpoint_test_runs_{column}", "model_endpoint_test_runs", [column]
        )


def downgrade() -> None:
    """恢复旧单模型状态字段；多模型和测试历史会被移除。"""
    for column in (
        "correlation_id",
        "status",
        "model_version_id",
        "endpoint_model_id",
        "endpoint_id",
        "tenant_id",
    ):
        op.drop_index(
            f"ix_model_endpoint_test_runs_{column}", table_name="model_endpoint_test_runs"
        )
    op.drop_index(
        "ix_model_endpoint_test_runs_model_created", table_name="model_endpoint_test_runs"
    )
    op.drop_table("model_endpoint_test_runs")
    op.drop_constraint(
        "fk_model_endpoint_versions_endpoint_model_id",
        "model_endpoint_versions",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_model_endpoint_versions_endpoint_model_id", table_name="model_endpoint_versions"
    )
    op.drop_column("model_endpoint_versions", "endpoint_model_id")
    op.drop_index("ix_model_endpoint_models_tenant_id", table_name="model_endpoint_models")
    op.drop_index("ix_model_endpoint_models_endpoint_id", table_name="model_endpoint_models")
    op.drop_table("model_endpoint_models")
    op.add_column(
        "model_endpoints",
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
    )
    op.execute(
        "UPDATE model_endpoints SET status = CASE WHEN is_enabled THEN 'active' ELSE 'disabled' END"
    )
    op.create_index("ix_model_endpoints_status", "model_endpoints", ["status"])
    op.drop_index("ix_model_endpoints_is_enabled", table_name="model_endpoints")
    op.drop_column("model_endpoints", "is_enabled")
    op.drop_column("model_endpoints", "provider_preset")
