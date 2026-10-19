"""当前连接状态投影；旧来源、自然过期或缺失观察不能成为健康许可。"""

from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.models import ToolConnectionEvent, ToolDiscoveryRevision, utc_now
from supportops_core.tool_connection_contracts import ConnectionView
from supportops_core.tool_discovery_services import ToolDiscoveryConflict


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC)


async def latest_connection(session: AsyncSession, source: ToolDiscoveryRevision) -> ToolConnectionEvent | None:
    row = await session.scalar(select(ToolConnectionEvent).where(ToolConnectionEvent.tenant_id == source.tenant_id,
        ToolConnectionEvent.source_key == source.source_key).order_by(ToolConnectionEvent.revision.desc()).limit(1)
        .execution_options(populate_existing=True))
    if row is not None and (row.revision != row.parent_revision + 1 or row.parent_revision < 0
        or row.circuit_state not in {"closed", "open", "half_open"} or row.health_state not in {"unknown", "healthy", "unhealthy"}
        or not 0 <= row.failure_count <= 3
        or (row.health_state == "healthy") != (row.checked_at is not None and row.valid_until is not None)
        or row.health_state == "healthy" and row.circuit_state != "closed"
        or row.health_state != "healthy" and (row.checked_at is not None or row.valid_until is not None)):
        raise ToolDiscoveryConflict("连接状态完整性无法确认")
    return row


def connection_view(source: ToolDiscoveryRevision, event: ToolConnectionEvent | None, *,
    available: bool, reason: str | None,
) -> ConnectionView:
    matches = event is not None and event.source_record_id == source.id and event.policy_digest == source.policy_digest
    circuit = event.circuit_state if event else "closed"
    health = event.health_state if event and matches else "unknown"
    # 旧范围的半开探测不能解除已有熔断。
    if not matches and circuit == "half_open":
        circuit = "open"
    if health == "healthy" and event is not None and (event.valid_until is None or aware(event.valid_until) <= utc_now()):
        health = "stale"
    return ConnectionView(source_key=source.source_key, source_record_id=source.id,
        revision=event.revision if event else 0,
        circuit_state=cast(Literal["closed", "open", "half_open"], circuit),
        health_state=cast(Literal["unknown", "healthy", "unhealthy", "stale"], health),
        failure_count=event.failure_count if event and matches else 0,
        checked_at=event.checked_at if event and matches else None,
        valid_until=event.valid_until if event and matches else None,
        retry_after=event.retry_after if event else None, latest_probe_id=event.probe_job_id if event else None,
        probe_available=available, unavailable_reason=reason)
