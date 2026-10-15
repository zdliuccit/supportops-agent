"""统一筛选契约，显式时间使用带时区的左闭右开区间。"""
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID
from fastapi import HTTPException, Request
from supportops_core.dashboard_query_services import DashboardFilters
from supportops_core.enums import AgentStatus, RunStatus


def dashboard_filters(request: Request, agent_id: UUID | None = None, user_id: UUID | None = None,
    organization_unit_id: UUID | None = None, agent_version_id: UUID | None = None,
    model_endpoint_version_id: UUID | None = None, status: RunStatus | None = None,
    lifecycle: AgentStatus | None = None,
    health: Literal["healthy", "degraded", "unknown", "no_recent_activity"] | None = None,
    start: datetime | None = None, end: datetime | None = None,
) -> DashboardFilters:
    if (start is None) != (end is None):
        raise HTTPException(422, "start 和 end 必须同时提供")
    if start is not None and end is not None:
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(422, "时间范围必须包含时区")
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if start >= end or end-start > timedelta(days=90):
            raise HTTPException(422, "时间范围须递增且不超过 90 天")
    return DashboardFilters(agent_id=agent_id, user_id=user_id, organization_unit_id=organization_unit_id,
        agent_version_id=agent_version_id, model_endpoint_version_id=model_endpoint_version_id,
        status=status, lifecycle=lifecycle, health=health, start=start, end=end,
        observed_at=datetime.now(UTC), stale_seconds=request.app.state.settings.health_snapshot_stale_seconds,
        queue_name=request.app.state.settings.redis_queue_name)


def dashboard_window(days: int, filters: DashboardFilters) -> tuple[datetime, datetime]:
    end = filters.end or filters.observed_at or datetime.now(UTC)
    return filters.start or end-timedelta(days=days), end
