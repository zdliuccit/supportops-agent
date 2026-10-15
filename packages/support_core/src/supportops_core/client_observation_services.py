"""客户端正文观测只写遥测，不决定产品状态或覆盖供应商计时。"""

from datetime import UTC
from uuid import UUID
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.auth import IdentityContext
from supportops_core.models import AgentRunObservation, RunEvent, utc_now
from supportops_core.services import ResourceNotFoundError, get_run_for_user


async def record_client_first_text(session: AsyncSession, *, run_id: UUID, identity: IdentityContext,
    event_sequence: int, elapsed_ms: int,
) -> None:
    # Run 锁串行化每个 Run 的首条观测；重复浏览器上报不重计或改写历史测量。
    run = await get_run_for_user(session, run_id=run_id, tenant_id=identity.principal.tenant_id,
        user_id=identity.user.id, lock=True)
    event = await session.scalar(select(RunEvent).where(RunEvent.run_id == run.id,
        RunEvent.tenant_id == run.tenant_id, RunEvent.sequence == event_sequence,
        RunEvent.event_type.in_(["text.delta", "text.replace"])))
    text = event.data.get("delta" if event.event_type == "text.delta" else "content") if event is not None else None
    if not isinstance(text, str) or not text:
        raise ResourceNotFoundError("正文事件不存在或不可观测")
    span_id = f"client:first_text:{run.id}"
    if await session.scalar(select(AgentRunObservation.id).where(AgentRunObservation.run_id == run.id,
        AgentRunObservation.tenant_id == run.tenant_id, AgentRunObservation.span_id == span_id)) is not None:
        return
    parent_id = await session.scalar(select(AgentRunObservation.id).where(AgentRunObservation.run_id == run.id,
        AgentRunObservation.tenant_id == run.tenant_id, AgentRunObservation.kind == "agent",
        AgentRunObservation.attempt_id == event.attempt_id).order_by(AgentRunObservation.started_at, AgentRunObservation.id).limit(1))
    received_at = utc_now()
    created_at = run.created_at.replace(tzinfo=UTC) if run.created_at.tzinfo is None else run.created_at
    committed_at = event.created_at.replace(tzinfo=UTC) if event.created_at.tzinfo is None else event.created_at
    first_event = await session.scalar(select(RunEvent).where(RunEvent.run_id == run.id,
        RunEvent.tenant_id == run.tenant_id, or_(
            (RunEvent.event_type == "text.delta") & (RunEvent.data["delta"].as_string() != ""),
            (RunEvent.event_type == "text.replace") & (RunEvent.data["content"].as_string() != "")))
        .order_by(RunEvent.sequence).limit(1))
    # 客户端是未信任的性能来源；其单调时钟不能与服务器时间直接相减。
    session.add(AgentRunObservation(tenant_id=run.tenant_id, run_id=run.id, attempt_id=event.attempt_id,
        parent_observation_id=parent_id, trace_id=run.correlation_id, span_id=span_id, kind="client",
        name="client.first_text_commit", status="completed", started_at=received_at, finished_at=received_at,
        metadata_payload={"source": "browser_performance_v1", "trusted": False,
            "boundary": "local_submit_to_react_commit", "client_elapsed_ms": elapsed_ms,
            "received_at": received_at.isoformat(), "event_sequence": event.sequence,
            "server_event_created_elapsed_ms": max(0, int((committed_at-created_at).total_seconds()*1000)),
            "is_first_server_text_event": first_event is not None and first_event.sequence == event.sequence,
            "server_boundary": "run_created_to_observed_event_timestamp", "convention_version": "first_text_v1"}))


async def record_api_first_committed_text(session: AsyncSession, *, run_id: UUID,
    identity: IdentityContext, event_sequence: int,
) -> None:
    """独立读事务已看到正文，即提交可见的观测上界；不冒充数据库精确 commit 时间。"""
    run = await get_run_for_user(session, run_id=run_id, tenant_id=identity.principal.tenant_id,
        user_id=identity.user.id, lock=True)
    span_id = f"api:first_text:{run.id}"
    if await session.scalar(select(AgentRunObservation.id).where(AgentRunObservation.run_id == run.id,
        AgentRunObservation.tenant_id == run.tenant_id, AgentRunObservation.span_id == span_id)) is not None:
        return
    event = await session.scalar(select(RunEvent).where(RunEvent.run_id == run.id,
        RunEvent.tenant_id == run.tenant_id, RunEvent.sequence == event_sequence,
        RunEvent.event_type.in_(["text.delta", "text.replace"])))
    text = event.data.get("delta" if event.event_type == "text.delta" else "content") if event is not None else None
    if not isinstance(text, str) or not text:
        return
    observed_at = utc_now()
    created_at = run.created_at.replace(tzinfo=UTC) if run.created_at.tzinfo is None else run.created_at
    parent_id = await session.scalar(select(AgentRunObservation.id).where(AgentRunObservation.run_id == run.id,
        AgentRunObservation.tenant_id == run.tenant_id, AgentRunObservation.kind == "agent",
        AgentRunObservation.attempt_id == event.attempt_id).order_by(AgentRunObservation.started_at, AgentRunObservation.id).limit(1))
    session.add(AgentRunObservation(tenant_id=run.tenant_id, run_id=run.id, attempt_id=event.attempt_id,
        parent_observation_id=parent_id, trace_id=run.correlation_id, span_id=span_id, kind="api",
        name="api.first_committed_text_observed", status="completed", started_at=created_at,
        first_output_at=observed_at, finished_at=observed_at,
        duration_ms=max(0, int((observed_at-created_at).total_seconds()*1000)),
        metadata_payload={"source": "api_database_visibility_v1", "event_sequence": event.sequence,
            "boundary": "run_created_to_first_sse_read_of_committed_text", "measurement": "upper_bound",
            "includes_polling_or_reconnect_delay": True, "convention_version": "first_text_v1"}))
