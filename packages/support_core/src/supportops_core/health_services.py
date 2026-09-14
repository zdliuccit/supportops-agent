"""Agent Runtime 健康快照计算；API 与 Worker 共用同一套数据口径。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import agent_config_digest, parse_agent_config
from supportops_core.enums import AgentStatus, RunStatus
from supportops_core.models import (
    Agent,
    AgentDraft,
    AgentRun,
    AgentRuntimeHealth,
    AgentVersion,
    RuntimeServiceLease,
)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _p95(values: list[int]) -> int | None:
    if len(values) < 3:
        return None
    values.sort()
    return values[max(0, ceil(len(values) * 0.95) - 1)]


async def refresh_agent_runtime_health(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    now: datetime | None = None,
    window: timedelta = timedelta(hours=24),
) -> list[AgentRuntimeHealth]:
    """按固定窗口和最小样本量刷新租户内 Agent 健康快照。"""
    observed_at = now or datetime.now(UTC)
    window_start = observed_at - window
    agents = list(
        (
            await session.scalars(
                select(Agent).where(Agent.tenant_id == tenant_id).order_by(Agent.id)
            )
        ).all()
    )
    leases = list(
        (
            await session.scalars(
                select(RuntimeServiceLease).where(RuntimeServiceLease.service_status == "healthy")
            )
        ).all()
    )
    service_available = any(
        (lease.expires_at is not None and (_aware(lease.expires_at) or observed_at) > observed_at)
        for lease in leases
    )
    snapshots: list[AgentRuntimeHealth] = []
    for agent in agents:
        runs = list(
            (
                await session.scalars(
                    select(AgentRun)
                    .where(
                        AgentRun.tenant_id == tenant_id,
                        AgentRun.agent_id == agent.id,
                        AgentRun.created_at >= window_start,
                    )
                    .order_by(AgentRun.created_at.desc())
                )
            ).all()
        )
        active_runs = [run for run in runs if run.status in (RunStatus.QUEUED, RunStatus.RUNNING)]
        terminal_runs = [
            run for run in runs if run.status in (RunStatus.COMPLETED, RunStatus.FAILED)
        ]
        failures = [run for run in terminal_runs if run.status == RunStatus.FAILED]
        error_rate = (
            round(len(failures) / len(terminal_runs) * 100, 2) if len(terminal_runs) >= 3 else None
        )
        p95_latency = _p95(
            [
                run.end_to_end_latency_ms
                for run in terminal_runs
                if run.end_to_end_latency_ms is not None
            ]
        )
        latest = runs[0] if runs else None
        latest_success = next((run for run in runs if run.status == RunStatus.COMPLETED), None)
        latest_failure = next((run for run in runs if run.status == RunStatus.FAILED), None)

        pending_publish = False
        if agent.active_version_id:
            draft = await session.get(AgentDraft, agent.id)
            active_version = await session.get(AgentVersion, agent.active_version_id)
            if draft is not None and active_version is not None:
                try:
                    pending_publish = (
                        agent_config_digest(parse_agent_config(draft.config))
                        != active_version.config_digest
                    )
                except Exception:
                    pending_publish = True

        if agent.status != AgentStatus.ACTIVE:
            health_status, reason = "unknown", "Agent 当前未启用"
        elif not service_available:
            health_status, reason = "unknown", "执行服务不可用或租约已过期"
        elif active_runs:
            health_status, reason = "healthy", "存在排队或运行中的请求"
        elif not terminal_runs:
            health_status, reason = "no_recent_activity", "统计窗口内暂无运行记录"
        elif len(terminal_runs) < 3:
            health_status, reason = "unknown", "统计窗口内有效样本不足，暂不判定异常"
        elif (
            latest
            and (_aware(latest.finished_at or latest.created_at) or observed_at) < window_start
        ):
            health_status, reason = "no_recent_activity", "统计窗口内没有新的运行"
        elif error_rate is not None and error_rate >= 50:
            health_status, reason = "degraded", "统计窗口内失败率较高，请检查错误记录"
        else:
            health_status, reason = "healthy", "统计窗口内运行正常"

        snapshot = await session.scalar(
            select(AgentRuntimeHealth).where(
                AgentRuntimeHealth.tenant_id == tenant_id,
                AgentRuntimeHealth.agent_id == agent.id,
            )
        )
        if snapshot is None:
            snapshot = AgentRuntimeHealth(tenant_id=tenant_id, agent_id=agent.id)
            session.add(snapshot)
        snapshot.health_status = health_status
        snapshot.health_reason = reason
        snapshot.last_run_at = latest.finished_at or latest.created_at if latest else None
        snapshot.last_success_at = latest_success.finished_at if latest_success else None
        snapshot.last_failure_at = latest_failure.finished_at if latest_failure else None
        snapshot.active_run_count = len(active_runs)
        snapshot.error_rate = error_rate
        snapshot.p95_latency_ms = p95_latency
        snapshot.pending_publish = pending_publish
        snapshot.window_start = window_start
        snapshot.window_end = observed_at
        snapshot.computed_at = observed_at
        snapshot.observed_at = observed_at
        snapshots.append(snapshot)
    await session.flush()
    return snapshots


async def refresh_all_agent_runtime_health(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    window: timedelta = timedelta(hours=24),
) -> int:
    """刷新当前数据库中所有租户的健康快照，供 Worker 周期任务调用。"""
    tenant_ids = list((await session.scalars(select(Agent.tenant_id).distinct())).all())
    for tenant_id in tenant_ids:
        await refresh_agent_runtime_health(session, tenant_id=tenant_id, now=now, window=window)
    return len(tenant_ids)
