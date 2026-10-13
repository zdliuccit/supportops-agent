"""工具许可账本衍生追踪与次数；不依赖 SDK 是否触发工具 callback。"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.models import AgentRun, AgentRunObservation, ToolInvocation


async def count_tool_invocations(session: AsyncSession, *, tenant_id: UUID, run_id: UUID, attempt_id: UUID | None = None) -> int:
    """统计已消费的调用许可；拒绝不消费，失败/未知不能假装未调用。"""
    filters = [ToolInvocation.tenant_id == tenant_id, ToolInvocation.run_id == run_id, ToolInvocation.status != "denied"]
    if attempt_id is not None:
        filters.append(ToolInvocation.attempt_id == attempt_id)
    return int(await session.scalar(select(func.count()).select_from(ToolInvocation).where(*filters)) or 0)


async def record_tool_observation(session_factory: async_sessionmaker[AsyncSession], *, invocation_id: UUID, tenant_id: UUID) -> None:
    """独立事务记录无正文观测；调用方必须隔离观测失败与业务回执。"""
    async with session_factory() as session, session.begin():
        invocation = await session.scalar(select(ToolInvocation).where(
            ToolInvocation.id == invocation_id, ToolInvocation.tenant_id == tenant_id,
        ))
        if invocation is None or invocation.status == "running":
            return
        span_id = f"tool:{invocation.attempt_id}:{invocation.id}"
        if await session.scalar(select(AgentRunObservation.id).where(
            AgentRunObservation.tenant_id == tenant_id, AgentRunObservation.run_id == invocation.run_id,
            AgentRunObservation.span_id == span_id,
        )) is not None:
            return
        run = await session.scalar(select(AgentRun).where(AgentRun.id == invocation.run_id, AgentRun.tenant_id == tenant_id))
        if run is None:
            return
        started_at = invocation.created_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        finished_at = invocation.finished_at or datetime.now(UTC)
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=UTC)
        session.add(AgentRunObservation(
            tenant_id=tenant_id, run_id=run.id, attempt_id=invocation.attempt_id, trace_id=run.correlation_id,
            span_id=span_id, kind="retrieval" if invocation.tool_id == "search_knowledge" else "tool",
            name="tool.policy_denied" if invocation.status == "denied" else invocation.tool_id,
            status="completed" if invocation.status == "succeeded" else "failed" if invocation.status == "denied" else invocation.status,
            started_at=started_at, finished_at=finished_at, duration_ms=max(0, int((finished_at-started_at).total_seconds()*1000)),
            tool_id=invocation.tool_id, model_version_id=run.model_endpoint_version_id, error_code=invocation.error_code,
            metadata_payload={"source": "tool_gateway_v1", "invocation_id": str(invocation.id),
                "implementation_version": invocation.implementation_version, "catalog_version": invocation.catalog_version,
                "risk": invocation.risk_level, "invocation_status": invocation.status},
        ))
