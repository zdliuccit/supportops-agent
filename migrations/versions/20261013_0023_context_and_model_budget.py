"""上下文边界及逐物理模型调用预算账本。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261013_0023"
down_revision: str | None = "20261013_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_attempts", sa.Column("context_snapshot", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True,
        comment="模型输入历史边界与可信证据 ID；不以旧角色快照授权。"))
    op.create_table(
        "model_call_ledger",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="模型调用许可标识。"),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, comment="模型调用所属租户。"),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("agent_runs.id"), nullable=False, comment="同 Run 恢复共用预算。"),
        sa.Column("attempt_id", sa.Uuid(), sa.ForeignKey("run_attempts.id"), nullable=False, comment="调用执行尝试。"),
        sa.Column("model_endpoint_version_id", sa.Uuid(), sa.ForeignKey("model_endpoint_versions.id"), nullable=False, comment="本次实际使用的固定模型端点版本。"),
        sa.Column("policy_version", sa.String(64), nullable=False, comment="预留和结算策略版本。"),
        sa.Column("call_number", sa.Integer(), nullable=False, comment="整个 Run 内物理调用序号，含重试。"),
        sa.Column("retry_index", sa.Integer(), nullable=False, comment="同逻辑请求内有限依赖重试序号；0 表示首次。"),
        sa.Column("status", sa.String(32), nullable=False, comment="reserved/settled/unknown；未知保留消费。"),
        sa.Column("token_strategy", sa.String(64), nullable=False, comment="完整输入估算策略，不表示精确 tokenizer。"),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False, comment="完整系统/历史/工具/结构输入估算。"),
        sa.Column("reserved_output_tokens", sa.Integer(), nullable=False, comment="请求最大输出预留。"),
        sa.Column("input_tokens", sa.Integer(), nullable=True, comment="本次真实输入 usage。"),
        sa.Column("output_tokens", sa.Integer(), nullable=True, comment="本次真实输出 usage。"),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True, comment="包含在输入中的缓存 Token。"),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True, comment="包含在输出中的推理 Token。"),
        sa.Column("reserved_cost_microusd", sa.BigInteger(), nullable=True, comment="保守 USD 费用预留；未知为 null。"),
        sa.Column("settled_cost_microusd", sa.BigInteger(), nullable=True, comment="按真实总 usage 计算；未知为 null。"),
        sa.Column("error_code", sa.String(100), nullable=True, comment="失败/未知 usage 原因。"),
        sa.Column("omitted_message_count", sa.Integer(), nullable=False, comment="裁去的完整旧消息数。"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, comment="调用许可创建时间。"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="结算/未知确认时间。"),
        sa.UniqueConstraint("run_id", "call_number", name="uq_model_call_number"),
        comment="逐物理模型调用预留、结算和未知 usage 的持久化计量。",
    )
    op.create_index("ix_model_call_tenant_day", "model_call_ledger", ["tenant_id", "created_at"])
    op.create_index("ix_model_call_ledger_run_id", "model_call_ledger", ["run_id"])


def downgrade() -> None:
    op.drop_table("model_call_ledger")
    op.drop_column("run_attempts", "context_snapshot")
