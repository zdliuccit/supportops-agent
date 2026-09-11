"""保存模型连通性测试返回的安全文本摘要。

Revision ID: 20260908_0008
Revises: 20260907_0007
Create Date: 2026-09-08 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260908_0008"
down_revision: str | None = "20260907_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """为测试记录增加模型返回文本，限制长度避免响应内容无限增长。"""
    op.add_column(
        "model_endpoint_test_runs",
        sa.Column(
            "response_content",
            sa.String(length=4000),
            nullable=False,
            server_default="",
            comment="模型对固定测试问题返回的截断文本，不包含请求凭据。",
        ),
    )
    op.alter_column("model_endpoint_test_runs", "response_content", server_default=None)
    op.alter_column(
        "model_endpoint_test_runs",
        "request_host",
        existing_type=sa.String(length=500),
        existing_nullable=False,
        comment="允许展示的目标主机名，不含凭据和路径参数。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "response_headers_ms",
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment="从发送请求到收到响应头的耗时。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "first_content_ms",
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment="从发送请求到收到首个响应内容的耗时。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "milestones",
        existing_type=postgresql.JSON(),
        type_=postgresql.JSONB(),
        existing_nullable=False,
        postgresql_using="milestones::jsonb",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "error_code",
        existing_type=sa.String(length=100),
        existing_nullable=True,
        comment="稳定且不含供应商秘密的失败代码。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "error_message",
        existing_type=sa.String(length=500),
        existing_nullable=True,
        comment="截断并脱敏后的失败说明。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "correlation_id",
        existing_type=sa.String(length=100),
        existing_nullable=False,
        comment="串联请求日志和审计事件的关联标识。",
    )


def downgrade() -> None:
    """移除模型测试响应文本字段。"""
    op.alter_column(
        "model_endpoint_test_runs",
        "correlation_id",
        existing_type=sa.String(length=100),
        existing_nullable=False,
        comment="日志和审计关联标识。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "error_message",
        existing_type=sa.String(length=500),
        existing_nullable=True,
        comment="截断脱敏失败说明。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "error_code",
        existing_type=sa.String(length=100),
        existing_nullable=True,
        comment="稳定脱敏失败代码。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "milestones",
        existing_type=postgresql.JSONB(),
        type_=postgresql.JSON(),
        existing_nullable=False,
        postgresql_using="milestones::json",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "first_content_ms",
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment="收到首个响应内容的耗时。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "response_headers_ms",
        existing_type=sa.Integer(),
        existing_nullable=True,
        comment="收到响应头的耗时。",
    )
    op.alter_column(
        "model_endpoint_test_runs",
        "request_host",
        existing_type=sa.String(length=500),
        existing_nullable=False,
        comment="允许展示的目标主机名。",
    )
    op.drop_column("model_endpoint_test_runs", "response_content")
