"""增加 Agent 运行观测、健康快照和执行服务租约。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260911_0010"
down_revision: str | None = "20260911_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name, column in (
        ("input_tokens", sa.BigInteger()),
        ("output_tokens", sa.BigInteger()),
        ("cached_input_tokens", sa.BigInteger()),
        ("reasoning_tokens", sa.BigInteger()),
        ("total_cost_microusd", sa.BigInteger()),
        ("cost_source", sa.String(length=24)),
        ("model_call_count", sa.Integer()),
        ("tool_call_count", sa.Integer()),
        ("retry_count", sa.Integer()),
        ("queue_latency_ms", sa.BigInteger()),
        ("execution_latency_ms", sa.BigInteger()),
        ("end_to_end_latency_ms", sa.BigInteger()),
        ("time_to_first_token_ms", sa.BigInteger()),
        ("finish_reason", sa.String(length=64)),
        ("provider_request_id", sa.String(length=200)),
    ):
        op.add_column("agent_runs", sa.Column(name, column, nullable=True))

    op.create_table(
        "agent_run_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_observation_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.String(length=100), nullable=False),
        sa.Column("span_id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_output_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("model_version_id", sa.Uuid(), nullable=True),
        sa.Column("tool_id", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("total_cost_microusd", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["parent_observation_id"], ["agent_run_observations.id"]),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_endpoint_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_run_observations_tenant_id", "agent_run_observations", ["tenant_id"]
    )
    op.create_index("ix_agent_run_observations_run_id", "agent_run_observations", ["run_id"])
    op.create_index("ix_agent_run_observations_trace_id", "agent_run_observations", ["trace_id"])
    op.create_index("ix_agent_run_observations_span_id", "agent_run_observations", ["span_id"])
    op.create_index(
        "ix_agent_run_observations_tenant_started",
        "agent_run_observations",
        ["tenant_id", "started_at"],
    )
    op.create_index(
        "ix_agent_run_observations_run_started",
        "agent_run_observations",
        ["run_id", "started_at"],
    )
    op.create_index(
        "ix_agent_run_observations_tenant_kind_started",
        "agent_run_observations",
        ["tenant_id", "kind", "started_at"],
    )

    op.create_table(
        "agent_runtime_health",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("health_status", sa.String(length=32), nullable=False),
        sa.Column("health_reason", sa.String(length=500), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_run_count", sa.Integer(), nullable=False),
        sa.Column("error_rate", sa.Float(), nullable=True),
        sa.Column("p95_latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("pending_publish", sa.Boolean(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "agent_id"),
    )
    op.create_index("ix_agent_runtime_health_tenant_id", "agent_runtime_health", ["tenant_id"])
    op.create_index("ix_agent_runtime_health_agent_id", "agent_runtime_health", ["agent_id"])
    op.create_index(
        "ix_agent_runtime_health_tenant_status",
        "agent_runtime_health",
        ["tenant_id", "health_status"],
    )
    op.create_index(
        "ix_agent_runtime_health_tenant_observed",
        "agent_runtime_health",
        ["tenant_id", "observed_at"],
    )

    op.create_table(
        "runtime_service_leases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.String(length=100), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queue_depth", sa.Integer(), nullable=False),
        sa.Column("active_run_count", sa.Integer(), nullable=False),
        sa.Column("service_status", sa.String(length=32), nullable=False),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id"),
    )
    op.create_index(
        "ix_runtime_service_leases_service_status",
        "runtime_service_leases",
        ["service_status"],
    )
    op.create_index(
        "ix_runtime_service_leases_status_expires",
        "runtime_service_leases",
        ["service_status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_service_leases_status_expires", table_name="runtime_service_leases")
    op.drop_index("ix_runtime_service_leases_service_status", table_name="runtime_service_leases")
    op.drop_table("runtime_service_leases")
    op.drop_index("ix_agent_runtime_health_tenant_observed", table_name="agent_runtime_health")
    op.drop_index("ix_agent_runtime_health_tenant_status", table_name="agent_runtime_health")
    op.drop_index("ix_agent_runtime_health_agent_id", table_name="agent_runtime_health")
    op.drop_index("ix_agent_runtime_health_tenant_id", table_name="agent_runtime_health")
    op.drop_table("agent_runtime_health")
    op.drop_index(
        "ix_agent_run_observations_tenant_kind_started", table_name="agent_run_observations"
    )
    op.drop_index("ix_agent_run_observations_run_started", table_name="agent_run_observations")
    op.drop_index("ix_agent_run_observations_tenant_started", table_name="agent_run_observations")
    op.drop_index("ix_agent_run_observations_span_id", table_name="agent_run_observations")
    op.drop_index("ix_agent_run_observations_trace_id", table_name="agent_run_observations")
    op.drop_index("ix_agent_run_observations_run_id", table_name="agent_run_observations")
    op.drop_index("ix_agent_run_observations_tenant_id", table_name="agent_run_observations")
    op.drop_table("agent_run_observations")
    for name, _column in (
        ("provider_request_id", None),
        ("finish_reason", None),
        ("time_to_first_token_ms", None),
        ("end_to_end_latency_ms", None),
        ("execution_latency_ms", None),
        ("queue_latency_ms", None),
        ("retry_count", None),
        ("tool_call_count", None),
        ("model_call_count", None),
        ("cost_source", None),
        ("total_cost_microusd", None),
        ("reasoning_tokens", None),
        ("cached_input_tokens", None),
        ("output_tokens", None),
        ("input_tokens", None),
    ):
        op.drop_column("agent_runs", name)
