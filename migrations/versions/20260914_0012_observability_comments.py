"""补齐观测与健康表的 PostgreSQL 数据字典注释。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260914_0012"
down_revision: str | None = "20260912_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _comment(table: str, column: str, text: str) -> None:
    escaped = text.replace("'", "''")
    op.execute(f'COMMENT ON COLUMN "{table}"."{column}" IS \'{escaped}\'')


def upgrade() -> None:
    for table, text in (
        ("agent_run_observations", "Agent 运行 Trace 明细；默认只保存结构化脱敏元数据。"),
        ("agent_runtime_health", "由 AgentRun 和执行服务事实派生的 Agent 健康快照。"),
        ("runtime_service_leases", "跨租户共享 Worker 实例的心跳、队列和服务状态。"),
    ):
        escaped = text.replace("'", "''")
        op.execute(f"COMMENT ON TABLE \"{table}\" IS '{escaped}'")

    observation_comments = {
        "id": "观测步骤唯一标识。",
        "tenant_id": "观测所属租户。",
        "run_id": "观测所属 AgentRun。",
        "parent_observation_id": "父级观测步骤。",
        "trace_id": "Trace 关联标识。",
        "span_id": "Span 关联标识。",
        "kind": "agent、llm、tool 或 retrieval。",
        "name": "观测步骤名称。",
        "status": "running、completed 或 failed。",
        "started_at": "步骤开始时间。",
        "first_output_at": "首个输出时间。",
        "finished_at": "步骤结束时间。",
        "duration_ms": "步骤耗时。",
        "provider": "模型供应商。",
        "model_version_id": "模型版本。",
        "tool_id": "工具标识。",
        "input_tokens": "该步骤输入 Token。",
        "output_tokens": "该步骤输出 Token。",
        "total_cost_microusd": "该步骤成本，单位微美元。",
        "error_code": "该步骤脱敏错误码。",
        "metadata": "经过白名单限制的结构化 metadata，不保存完整 Prompt 或回答。",
    }
    for column, text in observation_comments.items():
        _comment("agent_run_observations", column, text)

    health_comments = {
        "id": "健康快照唯一标识。",
        "tenant_id": "所属租户。",
        "agent_id": "所属 Agent。",
        "health_status": "healthy、degraded、no_recent_activity 或 unknown。",
        "health_reason": "面向管理员的健康状态原因。",
        "last_run_at": "最近一次运行时间。",
        "last_success_at": "最近一次成功时间。",
        "last_failure_at": "最近一次失败时间。",
        "active_run_count": "当前排队或运行中的 Run 数。",
        "error_rate": "统计窗口内失败率百分比。",
        "p95_latency_ms": "统计窗口内端到端 P95 耗时。",
        "pending_publish": "当前配置是否有待发布变更。",
        "window_start": "健康统计窗口开始。",
        "window_end": "健康统计窗口结束。",
        "computed_at": "健康快照计算时间。",
        "observed_at": "底层事实被观察到的时间。",
    }
    for column, text in health_comments.items():
        _comment("agent_runtime_health", column, text)

    lease_comments = {
        "id": "服务租约唯一标识。",
        "instance_id": "Worker 实例稳定标识。",
        "heartbeat_at": "最近一次 Worker 心跳时间。",
        "expires_at": "超过该时间未续租即视为过期。",
        "queue_depth": "共享执行队列当前积压数。",
        "active_run_count": "该实例当前执行中的 Run 数。",
        "service_status": "healthy、degraded 或 stopped。",
        "metadata": "服务实例的脱敏结构化元数据。",
        "created_at": "租约创建时间。",
        "updated_at": "租约最近更新时间。",
    }
    for column, text in lease_comments.items():
        _comment("runtime_service_leases", column, text)

    run_comments = {
        "input_tokens": "供应商或适配器报告的输入 Token；未知时为空。",
        "output_tokens": "供应商或适配器报告的输出 Token；未知时为空。",
        "cached_input_tokens": "缓存输入 Token 明细；未知时为空。",
        "reasoning_tokens": "推理 Token 明细；未知时为空。",
        "total_cost_microusd": "本次运行成本，单位为微美元；未知时为空。",
        "cost_source": "成本来源：provider、calculated 或 unknown。",
        "model_call_count": "本次运行模型调用次数。",
        "tool_call_count": "本次运行工具调用次数。",
        "retry_count": "本次运行重试次数。",
        "queue_latency_ms": "从入队到 Worker 开始的耗时。",
        "execution_latency_ms": "从 Worker 开始到终态的耗时。",
        "end_to_end_latency_ms": "从入队到终态的端到端耗时。",
        "time_to_first_token_ms": "真实首个输出事件耗时；非流式运行保持为空。",
        "finish_reason": "模型完成原因。",
        "provider_request_id": "供应商请求 ID；不一定由供应商提供。",
    }
    for column, text in run_comments.items():
        _comment("agent_runs", column, text)


def downgrade() -> None:
    # 注释属于数据字典，不影响表结构；回滚时清除本 migration 写入的注释。
    for table in ("agent_run_observations", "agent_runtime_health", "runtime_service_leases"):
        op.execute(f'COMMENT ON TABLE "{table}" IS NULL')
