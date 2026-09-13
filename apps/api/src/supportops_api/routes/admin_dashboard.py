"""Agent 运行分析与系统级 Dashboard 管理接口。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.auth import IdentityContext
from supportops_core.agent_config import agent_config_digest, parse_agent_config
from supportops_core.enums import AgentStatus, RunStatus
from supportops_core.models import (
    Agent,
    AgentErrorEvent,
    AgentDraft,
    AgentRun,
    AgentRunObservation,
    AgentVersion,
    ModelEndpointVersion,
    AgentRuntimeHealth,
    Conversation,
    OrganizationUnit,
    RuntimeServiceLease,
    User,
    utc_now,
)

from supportops_api.dependencies import current_identity, database_session
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    AdminDashboardErrorItem,
    AdminDashboardErrorListResponse,
    AdminDashboardErrorEventDetail,
    AdminDashboardErrorEventItem,
    AdminDashboardErrorEventListResponse,
    AdminDashboardRunItem,
    AdminDashboardRunListResponse,
    AdminDashboardSummaryResponse,
    AdminDashboardTimeseriesResponse,
    AdminDashboardTraceObservation,
    AdminDashboardTraceResponse,
    DashboardMetric,
    DashboardTimeseriesPoint,
    SystemAgentRankingItem,
    SystemAgentRankingsResponse,
    SystemAgentStatusItem,
    SystemAgentStatusListResponse,
)
from supportops_core.agent_services import require_platform_admin

router = APIRouter(tags=["admin-dashboard"])


async def admin_identity(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


def _window(days: int) -> tuple[datetime, datetime]:
    end = utc_now()
    return end - timedelta(days=days), end


def _metric(value: int | float | None, previous: int | float | None = None) -> DashboardMetric:
    if value is None:
        return DashboardMetric(value=None, previous_value=previous, available=False)
    change = None
    if previous not in (None, 0):
        change = round((float(value) - float(previous)) / float(previous) * 100, 2)
    return DashboardMetric(value=value, previous_value=previous, change_percent=change)


def _run_filters(
    query: Any,
    *,
    tenant_id: UUID,
    agent_id: UUID | None,
    start: datetime,
    end: datetime,
    status_filter: RunStatus | None,
    user_id: UUID | None = None,
) -> Any:
    conditions = [
        AgentRun.tenant_id == tenant_id,
        AgentRun.created_at >= start,
        AgentRun.created_at < end,
    ]
    if agent_id is not None:
        conditions.append(AgentRun.agent_id == agent_id)
    if status_filter is not None:
        conditions.append(AgentRun.status == status_filter)
    if user_id is not None:
        conditions.append(
            AgentRun.conversation_id.in_(
                select(Conversation.id).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.user_id == user_id,
                )
            )
        )
    return query.where(*conditions)


async def _summary(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: UUID | None,
    days: int,
) -> AdminDashboardSummaryResponse:
    start, end = _window(days)
    previous_start = start - (end - start)
    previous_end = start
    async def count_runs(left: datetime, right: datetime) -> int:
        query = _run_filters(select(func.count()).select_from(AgentRun), tenant_id=tenant_id, agent_id=agent_id, start=left, end=right, status_filter=None)
        return int(await session.scalar(query) or 0)

    async def count_conversations(left: datetime, right: datetime) -> int:
        query = select(func.count()).select_from(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.created_at >= left,
            Conversation.created_at < right,
        )
        if agent_id is not None:
            query = query.where(Conversation.agent_id == agent_id)
        return int(await session.scalar(query) or 0)

    async def distinct_active_users(left: datetime, right: datetime) -> int:
        query = select(func.count(func.distinct(Conversation.user_id))).select_from(
            AgentRun
        ).join(Conversation, Conversation.id == AgentRun.conversation_id)
        query = _run_filters(query, tenant_id=tenant_id, agent_id=agent_id, start=left, end=right, status_filter=None)
        return int(await session.scalar(query) or 0)

    run_query = _run_filters(select(AgentRun), tenant_id=tenant_id, agent_id=agent_id, start=start, end=end, status_filter=None)
    runs = list((await session.scalars(run_query)).all())
    completed = sum(run.status == RunStatus.COMPLETED for run in runs)
    failed = sum(run.status == RunStatus.FAILED for run in runs)
    cancelled = sum(run.status == RunStatus.CANCELLED for run in runs)
    terminal = completed + failed
    tokens_in = [run.input_tokens for run in runs if run.input_tokens is not None]
    tokens_out = [run.output_tokens for run in runs if run.output_tokens is not None]
    costs = [run.total_cost_microusd for run in runs if run.total_cost_microusd is not None]
    latencies = sorted(run.end_to_end_latency_ms for run in runs if run.end_to_end_latency_ms is not None)
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else None
    total_users = await session.scalar(select(func.count()).select_from(User).where(User.tenant_id == tenant_id))
    current_active = await distinct_active_users(start, end)
    active_5m = await distinct_active_users(end - timedelta(minutes=5), end)
    previous_runs = await count_runs(previous_start, previous_end)
    previous_conversations = await count_conversations(previous_start, previous_end)
    previous_active = await distinct_active_users(previous_start, previous_end)
    agent_condition = [Agent.tenant_id == tenant_id]
    if agent_id is not None:
        agent_condition.append(Agent.id == agent_id)
    agent_counts = dict(
        (str(status.value), int(count))
        for status, count in (
            await session.execute(
                select(Agent.status, func.count()).where(*agent_condition).group_by(Agent.status)
            )
        ).all()
    )
    running_query = _run_filters(
        select(func.count(func.distinct(AgentRun.agent_id))).select_from(AgentRun),
        tenant_id=tenant_id, agent_id=agent_id, start=start, end=end, status_filter=None,
    ).where(AgentRun.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)))
    running_agents = int(await session.scalar(running_query) or 0)
    service_rows = list((await session.scalars(select(RuntimeServiceLease))).all())
    now = utc_now()
    available_services = sum(row.expires_at >= now for row in service_rows)
    service_status = "healthy" if service_rows and available_services else ("unavailable" if service_rows else "unknown")
    return AdminDashboardSummaryResponse(
        window_start=start,
        window_end=end,
        metrics={
            "total_users": _metric(int(total_users or 0)),
            "active_users": _metric(current_active, previous_active),
            "active_users_5m": _metric(active_5m),
            "conversations": _metric(await count_conversations(start, end), previous_conversations),
            "agent_runs": _metric(len(runs), previous_runs),
            "success_rate": _metric(round(completed / terminal * 100, 2) if terminal else None),
            "error_count": _metric(failed),
            "input_tokens": _metric(sum(tokens_in) if tokens_in else None),
            "output_tokens": _metric(sum(tokens_out) if tokens_out else None),
            "total_cost_microusd": _metric(sum(costs) if costs else None),
            "p95_latency_ms": _metric(p95),
            "queue_depth": _metric(sum(row.queue_depth for row in service_rows)),
        },
        status_counts={
            "total": sum(agent_counts.values()),
            "running": running_agents,
            "draft": agent_counts.get(AgentStatus.DRAFT.value, 0),
            "active": agent_counts.get(AgentStatus.ACTIVE.value, 0),
            "disabled": agent_counts.get(AgentStatus.DISABLED.value, 0),
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
        },
        service={"status": service_status, "instances": len(service_rows), "available_instances": available_services},
    )


@router.get("/v1/admin/dashboard/summary", response_model=AdminDashboardSummaryResponse)
@router.get("/v1/admin/system/dashboard/summary", response_model=AdminDashboardSummaryResponse)
async def read_dashboard_summary(
    days: int = Query(default=7, ge=1, le=90),
    agent_id: UUID | None = None,
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardSummaryResponse:
    return await _summary(session, tenant_id=identity.principal.tenant_id, agent_id=agent_id, days=days)


@router.get("/v1/admin/dashboard/timeseries", response_model=AdminDashboardTimeseriesResponse)
@router.get("/v1/admin/system/dashboard/timeseries", response_model=AdminDashboardTimeseriesResponse)
async def read_dashboard_timeseries(
    days: int = Query(default=7, ge=1, le=90),
    agent_id: UUID | None = None,
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardTimeseriesResponse:
    start, end = _window(days)
    interval = "hour" if days <= 7 else "day"
    step = timedelta(hours=1 if interval == "hour" else 24)
    items: list[DashboardTimeseriesPoint] = []
    cursor = start.replace(minute=0, second=0, microsecond=0) if interval == "hour" else start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cursor < end:
        bucket_end = min(cursor + step, end)
        query = _run_filters(select(AgentRun), tenant_id=identity.principal.tenant_id, agent_id=agent_id, start=cursor, end=bucket_end, status_filter=None)
        runs = list((await session.scalars(query)).all())
        completed = sum(run.status == RunStatus.COMPLETED for run in runs)
        failed = sum(run.status == RunStatus.FAILED for run in runs)
        terminal = completed + failed
        items.append(DashboardTimeseriesPoint(bucket_start=cursor, values={
            "agent_runs": len(runs), "completed": completed, "failed": failed,
            "error_count": failed, "error_rate": round(failed / terminal * 100, 2) if terminal else None,
            "input_tokens": sum(run.input_tokens for run in runs if run.input_tokens is not None) or None,
            "output_tokens": sum(run.output_tokens for run in runs if run.output_tokens is not None) or None,
            "cost_microusd": sum(run.total_cost_microusd for run in runs if run.total_cost_microusd is not None) or None,
        }))
        cursor = bucket_end
    return AdminDashboardTimeseriesResponse(interval=interval, items=items)


def _run_item(
    run: AgentRun,
    name: str,
    user_name: str | None = None,
    organization_unit_id: UUID | None = None,
    department_name: str | None = None,
) -> AdminDashboardRunItem:
    return AdminDashboardRunItem(
        id=run.id, agent_id=run.agent_id, agent_name=name, conversation_id=run.conversation_id,
        user_name=user_name, organization_unit_id=organization_unit_id, department_name=department_name,
        status=run.status, error_code=run.error_code, created_at=run.created_at,
        started_at=run.started_at, finished_at=run.finished_at, input_tokens=run.input_tokens,
        output_tokens=run.output_tokens, total_cost_microusd=run.total_cost_microusd,
        end_to_end_latency_ms=run.end_to_end_latency_ms, correlation_id=run.correlation_id,
    )


@router.get("/v1/admin/dashboard/runs", response_model=AdminDashboardRunListResponse)
async def read_dashboard_runs(
    days: int = Query(default=7, ge=1, le=90),
    agent_id: UUID | None = None,
    user_id: UUID | None = None,
    status_filter: RunStatus | None = Query(default=None, alias="status"),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardRunListResponse:
    start, end = _window(days)
    base = _run_filters(
        select(AgentRun, Agent.name, User.display_name, User.organization_unit_id, OrganizationUnit.name)
        .join(Agent, Agent.id == AgentRun.agent_id)
        .join(Conversation, and_(Conversation.id == AgentRun.conversation_id, Conversation.tenant_id == identity.principal.tenant_id))
        .join(User, and_(User.id == Conversation.user_id, User.tenant_id == identity.principal.tenant_id))
        .join(OrganizationUnit, and_(OrganizationUnit.id == User.organization_unit_id, OrganizationUnit.tenant_id == identity.principal.tenant_id), isouter=True),
        tenant_id=identity.principal.tenant_id, agent_id=agent_id, start=start, end=end, status_filter=status_filter,
        user_id=user_id,
    )
    total_query = _run_filters(select(func.count()).select_from(AgentRun), tenant_id=identity.principal.tenant_id, agent_id=agent_id, start=start, end=end, status_filter=status_filter, user_id=user_id)
    total = int(await session.scalar(total_query) or 0)
    rows = (await session.execute(base.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return AdminDashboardRunListResponse(items=[_run_item(run, name, user_name, organization_unit_id, department_name) for run, name, user_name, organization_unit_id, department_name in rows], total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/v1/admin/dashboard/errors", response_model=AdminDashboardErrorListResponse)
async def read_dashboard_errors(
    days: int = Query(default=7, ge=1, le=90),
    agent_id: UUID | None = None,
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardErrorListResponse:
    start, end = _window(days)
    query = _run_filters(select(AgentRun).where(AgentRun.error_code.is_not(None)), tenant_id=identity.principal.tenant_id, agent_id=agent_id, start=start, end=end, status_filter=None)
    runs = list((await session.scalars(query)).all())
    grouped: dict[str, list[AgentRun]] = {}
    for run in runs:
        grouped.setdefault(run.error_code or "UNKNOWN", []).append(run)
    conversation_ids = {run.conversation_id for run in runs}
    users_by_conversation = dict(
        (
            await session.execute(
                select(Conversation.id, Conversation.user_id).where(
                    Conversation.tenant_id == identity.principal.tenant_id,
                    Conversation.id.in_(conversation_ids),
                )
            )
        ).all()
    ) if conversation_ids else {}
    items = [AdminDashboardErrorItem(error_code=code, count=len(rows), affected_users=len({users_by_conversation.get(run.conversation_id) for run in rows if users_by_conversation.get(run.conversation_id) is not None}), agent_count=len({run.agent_id for run in rows}), last_seen_at=max((run.finished_at or run.created_at for run in rows), default=None)) for code, rows in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)]
    return AdminDashboardErrorListResponse(items=items)


def _error_event_query(tenant_id: UUID) -> Any:
    return select(AgentErrorEvent, Agent, User, AgentVersion, ModelEndpointVersion, AgentRun).join(Agent, Agent.id == AgentErrorEvent.agent_id, isouter=True).join(User, User.id == AgentErrorEvent.user_id, isouter=True).join(AgentVersion, AgentVersion.id == AgentErrorEvent.agent_version_id, isouter=True).join(ModelEndpointVersion, ModelEndpointVersion.id == AgentErrorEvent.model_endpoint_version_id, isouter=True).join(AgentRun, AgentRun.id == AgentErrorEvent.run_id, isouter=True).where(AgentErrorEvent.tenant_id == tenant_id)


def _error_event_item(row: Any) -> AdminDashboardErrorEventItem:
    event, agent, user, version, model, run = row
    return AdminDashboardErrorEventItem(id=event.id, occurred_at=event.occurred_at, severity=event.severity, error_code=event.error_code, reason=event.reason, stage=event.stage, resolution_status=event.resolution_status, agent_id=event.agent_id, agent_name=agent.name if agent else None, user_id=event.user_id, user_name=user.display_name if user else None, conversation_id=event.conversation_id, run_id=event.run_id, correlation_id=run.correlation_id if run else None, agent_version_number=version.version_number if version else None, model_name=model.remote_model_name if model else None, retry_count=event.retry_count, latency_ms=event.latency_ms)


@router.get("/v1/admin/analytics/errors", response_model=AdminDashboardErrorEventListResponse)
async def read_error_events(
    days: int = Query(default=7, ge=1, le=90),
    start: datetime | None = None,
    end: datetime | None = None,
    severity: str | None = None,
    error_code: str | None = None,
    agent_id: UUID | None = None,
    user_id: UUID | None = None,
    status: str | None = None,
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardErrorEventListResponse:
    default_start, default_end = _window(days)
    range_start, range_end = start or default_start, end or default_end
    conditions = [AgentErrorEvent.occurred_at >= range_start, AgentErrorEvent.occurred_at < range_end]
    if severity:
        conditions.append(AgentErrorEvent.severity == severity)
    if error_code:
        conditions.append(AgentErrorEvent.error_code == error_code)
    if agent_id:
        conditions.append(AgentErrorEvent.agent_id == agent_id)
    if user_id:
        conditions.append(AgentErrorEvent.user_id == user_id)
    if status:
        conditions.append(AgentErrorEvent.resolution_status == status)
    tenant_id = identity.principal.tenant_id
    total = int(await session.scalar(select(func.count()).select_from(AgentErrorEvent).where(AgentErrorEvent.tenant_id == tenant_id, *conditions)) or 0)
    rows = list((await session.execute(_error_event_query(tenant_id).where(*conditions).order_by(AgentErrorEvent.occurred_at.desc(), AgentErrorEvent.id.desc()).offset(pagination.offset).limit(pagination.page_size))).all())
    metadata = pagination_metadata(total, pagination)
    return AdminDashboardErrorEventListResponse(items=[_error_event_item(row) for row in rows], total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/v1/admin/analytics/errors/{event_id}", response_model=AdminDashboardErrorEventDetail)
async def read_error_event_detail(
    event_id: UUID,
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardErrorEventDetail:
    tenant_id = identity.principal.tenant_id
    row = (await session.execute(_error_event_query(tenant_id).where(AgentErrorEvent.id == event_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="错误记录不存在或已移除")
    event = row[0]
    item = _error_event_item(row)
    observations = list((await session.scalars(select(AgentRunObservation).where(AgentRunObservation.run_id == event.run_id, AgentRunObservation.tenant_id == tenant_id).order_by(AgentRunObservation.started_at, AgentRunObservation.id))).all())
    return AdminDashboardErrorEventDetail(**item.model_dump(), metadata=event.metadata_payload, observations=[AdminDashboardTraceObservation(id=obs.id, kind=obs.kind, name=obs.name, status=obs.status, started_at=obs.started_at, finished_at=obs.finished_at, duration_ms=obs.duration_ms, input_tokens=obs.input_tokens, output_tokens=obs.output_tokens, total_cost_microusd=obs.total_cost_microusd, error_code=obs.error_code, metadata=obs.metadata_payload) for obs in observations])


@router.get("/v1/admin/dashboard/runs/{run_id}/trace", response_model=AdminDashboardTraceResponse)
async def read_dashboard_trace(
    run_id: UUID,
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardTraceResponse:
    row = await session.execute(select(AgentRun, Agent.name).join(Agent, Agent.id == AgentRun.agent_id).where(AgentRun.id == run_id, AgentRun.tenant_id == identity.principal.tenant_id))
    found = row.first()
    if found is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    run, name = found
    observations = list((await session.scalars(select(AgentRunObservation).where(AgentRunObservation.run_id == run_id, AgentRunObservation.tenant_id == identity.principal.tenant_id).order_by(AgentRunObservation.started_at, AgentRunObservation.id))).all())
    return AdminDashboardTraceResponse(run=_run_item(run, name), observations=[AdminDashboardTraceObservation(id=item.id, kind=item.kind, name=item.name, status=item.status, started_at=item.started_at, finished_at=item.finished_at, duration_ms=item.duration_ms, input_tokens=item.input_tokens, output_tokens=item.output_tokens, total_cost_microusd=item.total_cost_microusd, error_code=item.error_code, metadata=item.metadata_payload) for item in observations])


@router.get("/v1/admin/system/dashboard/agents/status", response_model=SystemAgentStatusListResponse)
async def read_system_agent_status(
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> SystemAgentStatusListResponse:
    tenant_id = identity.principal.tenant_id
    total = int(await session.scalar(select(func.count()).select_from(Agent).where(Agent.tenant_id == tenant_id)) or 0)
    agents = list((await session.scalars(select(Agent).where(Agent.tenant_id == tenant_id).order_by(Agent.name, Agent.id).offset(pagination.offset).limit(pagination.page_size))).all())
    health_rows = list((await session.scalars(select(AgentRuntimeHealth).where(AgentRuntimeHealth.tenant_id == tenant_id, AgentRuntimeHealth.agent_id.in_([agent.id for agent in agents])))).all()) if agents else []
    health_by_agent = {row.agent_id: row for row in health_rows}
    items: list[SystemAgentStatusItem] = []
    for agent in agents:
        health = health_by_agent.get(agent.id)
        active_count = int(await session.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.tenant_id == tenant_id, AgentRun.agent_id == agent.id, AgentRun.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)))) or 0)
        recent_runs = list((await session.scalars(select(AgentRun).where(AgentRun.tenant_id == tenant_id, AgentRun.agent_id == agent.id).order_by(AgentRun.created_at.desc()).limit(20))).all())
        latest = recent_runs[0] if recent_runs else None
        terminal_runs = [run for run in recent_runs if run.status in (RunStatus.COMPLETED, RunStatus.FAILED)]
        failure_count = sum(run.status == RunStatus.FAILED for run in terminal_runs)
        derived_error_rate = round(failure_count / len(terminal_runs) * 100, 2) if terminal_runs else None
        derived_health = "unknown"
        derived_reason = "暂无运行记录"
        if active_count:
            derived_health, derived_reason = "healthy", "存在排队或运行中的请求"
        elif latest is not None:
            age = utc_now() - (latest.finished_at or latest.created_at)
            if age > timedelta(hours=24):
                derived_health, derived_reason = "no_recent_activity", "超过 24 小时没有新的运行"
            elif derived_error_rate is not None and len(terminal_runs) >= 3 and derived_error_rate >= 50:
                derived_health, derived_reason = "degraded", "近期失败率较高，请检查错误记录"
            elif latest.status == RunStatus.FAILED:
                derived_health, derived_reason = "degraded", f"最近一次运行失败：{latest.error_code or 'UNKNOWN'}"
            else:
                derived_health, derived_reason = "healthy", "近期运行正常"
        pending_publish = False
        if agent.active_version_id:
            draft = await session.get(AgentDraft, agent.id)
            active_version = await session.get(AgentVersion, agent.active_version_id)
            if draft is not None and active_version is not None:
                try:
                    pending_publish = agent_config_digest(parse_agent_config(draft.config)) != active_version.config_digest
                except Exception:
                    pending_publish = True
        items.append(SystemAgentStatusItem(id=agent.id, name=agent.name, lifecycle_status=agent.status.value, execution_status="running" if active_count else "idle", health_status=health.health_status if health else derived_health, health_reason=health.health_reason if health else derived_reason, last_run_at=health.last_run_at if health and health.last_run_at else (latest.finished_at or latest.created_at if latest else None), last_success_at=health.last_success_at if health and health.last_success_at else next((run.finished_at for run in recent_runs if run.status == RunStatus.COMPLETED), None), last_failure_at=health.last_failure_at if health and health.last_failure_at else next((run.finished_at for run in recent_runs if run.status == RunStatus.FAILED), None), active_run_count=active_count, error_rate=health.error_rate if health and health.error_rate is not None else derived_error_rate, p95_latency_ms=health.p95_latency_ms if health and health.p95_latency_ms is not None else None, pending_publish=(health.pending_publish if health else False) or pending_publish, observed_at=health.observed_at if health else utc_now()))
    metadata = pagination_metadata(total, pagination)
    return SystemAgentStatusListResponse(items=items, total=metadata.total, page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


@router.get("/v1/admin/system/dashboard/rankings", response_model=SystemAgentRankingsResponse)
async def read_system_rankings(
    days: int = Query(default=7, ge=1, le=90),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> SystemAgentRankingsResponse:
    start, end = _window(days)
    rows = list((await session.execute(select(AgentRun.agent_id, Agent.name, func.count(AgentRun.id)).join(Agent, Agent.id == AgentRun.agent_id).where(AgentRun.tenant_id == identity.principal.tenant_id, AgentRun.created_at >= start, AgentRun.created_at < end).group_by(AgentRun.agent_id, Agent.name).order_by(func.count(AgentRun.id).desc()).limit(10))).all())
    return SystemAgentRankingsResponse(rankings={"agent_runs": [SystemAgentRankingItem(agent_id=agent_id, agent_name=name, value=count) for agent_id, name, count in rows]})
