"""Agent 运行分析与系统级 Dashboard 管理接口。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
import structlog
from sqlalchemy import and_, case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.agent_config import agent_config_digest, parse_agent_config
from supportops_core.agent_services import require_platform_admin
from supportops_core.auth import IdentityContext
from supportops_core.enums import AgentStatus, RunStatus
from supportops_core.dashboard_query_services import DashboardFilters, filter_dashboard_runs, filter_dashboard_agents, agent_health_expression, dashboard_rankings, dashboard_run_metrics
from supportops_core.health_services import agent_activity
from supportops_core.model_budget_services import model_budget_summary
from supportops_core.runtime_errors import runtime_error_message
from supportops_core.models import (
    Agent,
    AgentDraft,
    AgentErrorEvent,
    AgentRun,
    AgentRunObservation,
    AgentRuntimeHealth,
    AgentVersion,
    Conversation,
    ModelEndpointVersion,
    OrganizationUnit,
    RuntimeServiceLease,
    User,
    utc_now,
    BusinessAction,
    RunEvent,
)

from supportops_api.dependencies import current_identity, database_session
from supportops_api.dashboard_filters import dashboard_filters, dashboard_window
from supportops_api.pagination import PaginationParams, pagination_metadata, pagination_params
from supportops_api.schemas import (
    AdminDashboardErrorEventDetail,
    AdminDashboardErrorEventItem,
    AdminDashboardErrorEventListResponse,
    AdminDashboardErrorItem,
    AdminDashboardErrorListResponse,
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
    ActionAuditListResponse,
    ActionAuditSummary,
)

router = APIRouter(tags=["admin-dashboard"])


async def admin_identity(identity: IdentityContext = Depends(current_identity)) -> IdentityContext:
    require_platform_admin(identity)
    return identity


@router.get("/v1/admin/dashboard/runs/{run_id}/actions", response_model=ActionAuditListResponse)
async def read_action_audit(run_id: UUID, pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity), session: AsyncSession = Depends(database_session),
) -> ActionAuditListResponse:
    tenant_id = identity.principal.tenant_id
    run = await session.scalar(select(AgentRun.id).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id))
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    linked = exists(select(RunEvent.id).where(RunEvent.tenant_id == tenant_id, RunEvent.run_id == run_id,
        RunEvent.event_type == "action.proposed", RunEvent.data["logical_key"].as_string() == BusinessAction.logical_key))
    filters = (BusinessAction.tenant_id == tenant_id, linked)
    total = int(await session.scalar(select(func.count()).select_from(BusinessAction).where(*filters)) or 0)
    items = (await session.scalars(select(BusinessAction).where(*filters).order_by(BusinessAction.created_at.desc(), BusinessAction.id.desc())
        .offset(pagination.offset).limit(pagination.page_size))).all()
    metadata = pagination_metadata(total, pagination)
    return ActionAuditListResponse(items=[ActionAuditSummary.model_validate(item) for item in items], total=metadata.total,
        page=metadata.page, page_size=metadata.page_size, pages=metadata.pages)


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
    agent_version_id: UUID | None = None,
    model_endpoint_version_id: UUID | None = None,
    organization_unit_id: UUID | None = None,
    scope: DashboardFilters | None = None,
) -> Any:
    filters = scope or DashboardFilters(agent_id=agent_id, user_id=user_id, status=status_filter,
        agent_version_id=agent_version_id, model_endpoint_version_id=model_endpoint_version_id,
        organization_unit_id=organization_unit_id)
    return filter_dashboard_runs(query, tenant_id=tenant_id, start=start, end=end, filters=filters)


async def _summary(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: UUID | None,
    days: int,
    scope: DashboardFilters,
) -> AdminDashboardSummaryResponse:
    start, end = dashboard_window(days, scope)
    previous_start = start - (end - start)
    previous_end = start

    async def count_runs(left: datetime, right: datetime) -> int:
        query = _run_filters(
            select(func.count()).select_from(AgentRun),
            tenant_id=tenant_id,
            agent_id=agent_id,
            start=left,
            end=right,
            status_filter=None,
            scope=scope,
        )
        return int(await session.scalar(query) or 0)

    async def count_conversations(left: datetime, right: datetime) -> int:
        query = (
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.created_at >= left,
                Conversation.created_at < right,
            )
        )
        if agent_id is not None:
            query = query.where(Conversation.agent_id == agent_id)
        if scope.has_run_dimensions or scope.lifecycle is not None or scope.health is not None:
            matching = filter_dashboard_runs(select(AgentRun.conversation_id), tenant_id=tenant_id,
                start=left, end=right, filters=scope)
            query = query.where(Conversation.id.in_(matching))
        return int(await session.scalar(query.execution_options(include_deleted=True)) or 0)

    async def distinct_active_users(left: datetime, right: datetime) -> int:
        query = (
            select(func.count(func.distinct(Conversation.user_id)))
            .select_from(AgentRun)
            .join(Conversation, Conversation.id == AgentRun.conversation_id)
        )
        query = _run_filters(
            query, tenant_id=tenant_id, agent_id=agent_id, start=left, end=right, status_filter=None, scope=scope
        )
        return int(await session.scalar(query) or 0)

    totals = await dashboard_run_metrics(session, tenant_id=tenant_id, filters=scope, start=start, end=end)
    completed, failed, cancelled, terminal = totals.completed, totals.failed, totals.cancelled, totals.terminal
    if scope.has_run_dimensions or scope.lifecycle is not None or scope.health is not None:
        eligible = filter_dashboard_runs(select(AgentRun.conversation_id), tenant_id=tenant_id,
            start=start, end=end, filters=scope)
        total_users = await session.scalar(select(func.count(func.distinct(Conversation.user_id)))
            .where(Conversation.tenant_id == tenant_id, Conversation.id.in_(eligible)).execution_options(include_deleted=True))
    elif agent_id is None:
        total_users = await session.scalar(
            select(func.count()).select_from(User).where(User.tenant_id == tenant_id)
        )
    else:
        total_users = await session.scalar(
            select(func.count(func.distinct(Conversation.user_id)))
            .select_from(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.agent_id == agent_id,
            )
        )
    current_active = await distinct_active_users(start, end)
    active_5m = await distinct_active_users(max(start, end - timedelta(minutes=5)), end)
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
                filter_dashboard_agents(select(Agent.status, func.count()).where(*agent_condition),
                    tenant_id=tenant_id, filters=scope, start=start, end=end).group_by(Agent.status)
            )
        ).all()
    )
    running_query = _run_filters(
        select(func.count(func.distinct(AgentRun.agent_id))).select_from(AgentRun),
        tenant_id=tenant_id,
        agent_id=agent_id,
        start=start,
        end=end,
        status_filter=None,
        scope=scope,
    ).where(AgentRun.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)))
    running_agents = int(await session.scalar(running_query) or 0)
    health_expression = agent_health_expression(scope)
    health_counts = dict((str(health), int(count)) for health, count in (await session.execute(
        filter_dashboard_agents(select(health_expression, func.count()).select_from(Agent)
            .outerjoin(AgentRuntimeHealth, and_(AgentRuntimeHealth.agent_id == Agent.id, AgentRuntimeHealth.tenant_id == tenant_id)),
            tenant_id=tenant_id, filters=scope, start=start, end=end).group_by(health_expression)
    )).all())
    now = scope.observed_at or utc_now()
    service_rows = list((await session.scalars(select(RuntimeServiceLease).where(
        RuntimeServiceLease.expires_at > now, RuntimeServiceLease.heartbeat_at <= now + timedelta(seconds=5),
        RuntimeServiceLease.service_status.in_(["healthy", "degraded"])))).all())
    observed_instances = int(await session.scalar(select(func.count()).select_from(RuntimeServiceLease)) or 0)
    available_services = len(service_rows)
    service_status = ("degraded" if any(row.service_status == "degraded" for row in service_rows) else "healthy") if service_rows else ("unavailable" if observed_instances else "unknown")
    # 同一 Redis List 的多个 Worker 看到同一 LLEN，不能相加；旧无队列名或未知观测不补造。
    queue_samples = []
    for lease in service_rows:
        metadata = lease.metadata_payload
        if scope.queue_name is None or metadata.get("queue_name") != scope.queue_name or metadata.get("queue_depth_available") is not True:
            continue
        raw_time = metadata.get("queue_depth_observed_at")
        try:
            observed = datetime.fromisoformat(raw_time) if isinstance(raw_time, str) else None
        except ValueError:
            observed = None
        if observed is None or observed.tzinfo is None or lease.queue_depth < 0:
            continue
        if observed > now+timedelta(seconds=5) or (now-observed).total_seconds() > scope.stale_seconds:
            continue
        queue_samples.append((observed, lease.instance_id, lease.queue_depth))
    queue_depth = max(queue_samples)[2] if queue_samples else None

    def measured(value: int | float | None, *, source: str, samples: int, missing: int = 0) -> DashboardMetric:
        return DashboardMetric(value=value, available=value is not None, source=source, sample_count=samples, missing_count=missing)

    return AdminDashboardSummaryResponse(
        window_start=start,
        window_end=end,
        metrics={
            "total_users": _metric(int(total_users or 0)),
            "active_users": _metric(current_active, previous_active),
            "active_users_5m": _metric(active_5m),
            "conversations": _metric(await count_conversations(start, end), previous_conversations),
            "agent_runs": _metric(totals.runs, previous_runs),
            "success_rate": _metric(round(completed / terminal * 100, 2) if terminal else None),
            "error_count": _metric(failed),
            "input_tokens": measured(totals.input_tokens, source="run_usage_v2", samples=totals.runs-totals.input_missing, missing=totals.input_missing),
            "output_tokens": measured(totals.output_tokens, source="run_usage_v2", samples=totals.runs-totals.output_missing, missing=totals.output_missing),
            "total_cost_microusd": measured(totals.cost_microusd, source="run_estimated_cost_v2", samples=totals.runs-totals.cost_missing, missing=totals.cost_missing),
            "p50_latency_ms": measured(totals.p50_latency_ms, source="terminal_nearest_rank_v2", samples=totals.latency_samples, missing=terminal-totals.latency_samples),
            "p95_latency_ms": measured(totals.p95_latency_ms, source="terminal_nearest_rank_v2", samples=totals.latency_samples, missing=terminal-totals.latency_samples),
            "p99_latency_ms": measured(totals.p99_latency_ms, source="terminal_nearest_rank_v2", samples=totals.latency_samples, missing=terminal-totals.latency_samples),
            "queue_depth": measured(queue_depth, source="shared_queue_latest_sample_v2", samples=1 if queue_samples else 0),
        },
        status_counts={
            "health_healthy": health_counts.get("healthy", 0),
            "health_degraded": health_counts.get("degraded", 0),
            "health_unknown": health_counts.get("unknown", 0),
            "health_no_recent_activity": health_counts.get("no_recent_activity", 0),
            "total": sum(agent_counts.values()),
            "running": running_agents,
            "draft": agent_counts.get(AgentStatus.DRAFT.value, 0),
            "active": agent_counts.get(AgentStatus.ACTIVE.value, 0),
            "disabled": agent_counts.get(AgentStatus.DISABLED.value, 0),
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
        },
        service={
            "status": service_status,
            "instances": len(service_rows),
            "observed_instances": observed_instances,
            "available_instances": available_services,
        },
    )


@router.get("/v1/admin/dashboard/summary", response_model=AdminDashboardSummaryResponse)
@router.get("/v1/admin/system/dashboard/summary", response_model=AdminDashboardSummaryResponse)
async def read_dashboard_summary(
    days: int = Query(default=7, ge=1, le=90),
    agent_id: UUID | None = None,
    scope: DashboardFilters = Depends(dashboard_filters),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardSummaryResponse:
    return await _summary(
        session, tenant_id=identity.principal.tenant_id, agent_id=agent_id, days=days, scope=scope
    )


@router.get("/v1/admin/dashboard/timeseries", response_model=AdminDashboardTimeseriesResponse)
@router.get(
    "/v1/admin/system/dashboard/timeseries", response_model=AdminDashboardTimeseriesResponse
)
async def read_dashboard_timeseries(
    days: int = Query(default=7, ge=1, le=90),
    agent_id: UUID | None = None,
    scope: DashboardFilters = Depends(dashboard_filters),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardTimeseriesResponse:
    start, end = dashboard_window(days, scope)
    interval = "hour" if days <= 7 else "day"
    step = timedelta(hours=1 if interval == "hour" else 24)
    items: list[DashboardTimeseriesPoint] = []
    cursor = (
        start.replace(minute=0, second=0, microsecond=0)
        if interval == "hour"
        else start.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    while cursor < end:
        bucket_end = min(cursor + step, end)
        totals = await dashboard_run_metrics(session, tenant_id=identity.principal.tenant_id,
            filters=scope, start=max(cursor, start), end=bucket_end)
        completed, failed, terminal = totals.completed, totals.failed, totals.terminal
        items.append(
            DashboardTimeseriesPoint(
                bucket_start=max(cursor, start),
                bucket_end=bucket_end,
                values={
                    "agent_runs": totals.runs,
                    "completed": completed,
                    "failed": failed,
                    "error_count": failed,
                    "error_rate": round(failed / terminal * 100, 2) if terminal else None,
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "cost_microusd": totals.cost_microusd,
                    "active_users": totals.active_users,
                    "p50_latency_ms": totals.p50_latency_ms,
                    "p95_latency_ms": totals.p95_latency_ms,
                    "p99_latency_ms": totals.p99_latency_ms,
                },
            )
        )
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
        id=run.id,
        agent_id=run.agent_id,
        agent_name=name,
        conversation_id=run.conversation_id,
        user_name=user_name,
        organization_unit_id=organization_unit_id,
        department_name=department_name,
        status=run.status,
        error_code=run.error_code,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        total_cost_microusd=run.total_cost_microusd,
        end_to_end_latency_ms=run.end_to_end_latency_ms,
        correlation_id=run.correlation_id,
    )


@router.get("/v1/admin/dashboard/runs", response_model=AdminDashboardRunListResponse)
async def read_dashboard_runs(
    days: int = Query(default=7, ge=1, le=90),
    agent_id: UUID | None = None,
    user_id: UUID | None = None,
    agent_version_id: UUID | None = None,
    model_endpoint_version_id: UUID | None = None,
    organization_unit_id: UUID | None = None,
    status_filter: RunStatus | None = Query(default=None, alias="status"),
    scope: DashboardFilters = Depends(dashboard_filters),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardRunListResponse:
    start, end = dashboard_window(days, scope)
    base = _run_filters(
        select(
            AgentRun,
            Agent.name,
            User.display_name,
            User.organization_unit_id,
            OrganizationUnit.name,
        )
        .join(Agent, Agent.id == AgentRun.agent_id)
        .join(
            Conversation,
            and_(
                Conversation.id == AgentRun.conversation_id,
                Conversation.tenant_id == identity.principal.tenant_id,
            ),
        )
        .join(
            User,
            and_(User.id == Conversation.user_id, User.tenant_id == identity.principal.tenant_id),
        )
        .join(
            OrganizationUnit,
            and_(
                OrganizationUnit.id == User.organization_unit_id,
                OrganizationUnit.tenant_id == identity.principal.tenant_id,
            ),
            isouter=True,
        ),
        tenant_id=identity.principal.tenant_id,
        agent_id=agent_id,
        start=start,
        end=end,
        status_filter=status_filter,
        user_id=user_id,
        agent_version_id=agent_version_id,
        model_endpoint_version_id=model_endpoint_version_id,
        organization_unit_id=organization_unit_id,
        scope=scope,
    )
    total_query = _run_filters(
        select(func.count()).select_from(AgentRun),
        tenant_id=identity.principal.tenant_id,
        agent_id=agent_id,
        start=start,
        end=end,
        status_filter=status_filter,
        user_id=user_id,
        agent_version_id=agent_version_id,
        model_endpoint_version_id=model_endpoint_version_id,
        organization_unit_id=organization_unit_id,
        scope=scope,
    )
    total = int(await session.scalar(total_query) or 0)
    rows = (
        await session.execute(
            base.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()
    metadata = pagination_metadata(total, pagination)
    return AdminDashboardRunListResponse(
        items=[
            _run_item(run, name, user_name, organization_unit_id, department_name)
            for run, name, user_name, organization_unit_id, department_name in rows
        ],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.get("/v1/admin/dashboard/errors", response_model=AdminDashboardErrorListResponse)
async def read_dashboard_errors(
    days: int = Query(default=7, ge=1, le=90),
    agent_id: UUID | None = None,
    scope: DashboardFilters = Depends(dashboard_filters),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardErrorListResponse:
    start, end = dashboard_window(days, scope)
    query = _run_filters(
        select(AgentRun).where(AgentRun.error_code.is_not(None)),
        tenant_id=identity.principal.tenant_id,
        agent_id=agent_id,
        start=start,
        end=end,
        status_filter=None,
        scope=scope,
    )
    runs = list((await session.scalars(query)).all())
    grouped: dict[str, list[AgentRun]] = {}
    for run in runs:
        grouped.setdefault(run.error_code or "UNKNOWN", []).append(run)
    conversation_ids = {run.conversation_id for run in runs}
    user_rows = (
        (
            await session.execute(
                select(Conversation.id, Conversation.user_id)
                .where(
                    Conversation.tenant_id == identity.principal.tenant_id,
                    Conversation.id.in_(conversation_ids),
                )
                .execution_options(include_deleted=True)
            )
        ).all()
        if conversation_ids
        else []
    )
    users_by_conversation: dict[UUID, UUID] = {row[0]: row[1] for row in user_rows}
    items = [
        AdminDashboardErrorItem(
            error_code=code,
            count=len(rows),
            affected_users=len(
                {
                    users_by_conversation.get(run.conversation_id)
                    for run in rows
                    if users_by_conversation.get(run.conversation_id) is not None
                }
            ),
            agent_count=len({run.agent_id for run in rows}),
            last_seen_at=max((run.finished_at or run.created_at for run in rows), default=None),
        )
        for code, rows in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)
    ]
    return AdminDashboardErrorListResponse(items=items)


def _error_event_query(tenant_id: UUID) -> Any:
    return (
        select(AgentErrorEvent, Agent, User, AgentVersion, ModelEndpointVersion, AgentRun)
        .join(Agent, Agent.id == AgentErrorEvent.agent_id, isouter=True)
        .join(User, User.id == AgentErrorEvent.user_id, isouter=True)
        .join(AgentVersion, AgentVersion.id == AgentErrorEvent.agent_version_id, isouter=True)
        .join(
            ModelEndpointVersion,
            ModelEndpointVersion.id == AgentErrorEvent.model_endpoint_version_id,
            isouter=True,
        )
        .join(AgentRun, AgentRun.id == AgentErrorEvent.run_id, isouter=True)
        .where(AgentErrorEvent.tenant_id == tenant_id)
        .execution_options(include_deleted=True)
    )


def _error_event_item(row: Any) -> AdminDashboardErrorEventItem:
    event, agent, user, version, model, run = row
    return AdminDashboardErrorEventItem(
        id=event.id,
        occurred_at=event.occurred_at,
        severity=event.severity,
        error_code=event.error_code,
        reason=event.reason,
        stage=event.stage,
        resolution_status=event.resolution_status,
        agent_id=event.agent_id,
        agent_name=agent.name if agent else None,
        user_id=event.user_id,
        user_name=user.display_name if user else None,
        conversation_id=event.conversation_id,
        run_id=event.run_id,
        correlation_id=run.correlation_id if run else None,
        agent_version_number=version.version_number if version else None,
        model_name=model.remote_model_name if model else None,
        retry_count=event.retry_count,
        latency_ms=event.latency_ms,
    )


@router.get("/v1/admin/analytics/errors", response_model=AdminDashboardErrorEventListResponse)
async def read_error_events(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    start: datetime | None = None,
    end: datetime | None = None,
    severity: str | None = None,
    error_code: str | None = None,
    agent_id: UUID | None = None,
    user_id: UUID | None = None,
    status: str | None = None,
    run_status: RunStatus | None = None,
    agent_version_id: UUID | None = None,
    model_endpoint_version_id: UUID | None = None,
    organization_unit_id: UUID | None = None,
    lifecycle: AgentStatus | None = None,
    health: Literal["healthy", "degraded", "unknown", "no_recent_activity"] | None = None,
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardErrorEventListResponse:
    scope = dashboard_filters(request, agent_id=agent_id, user_id=user_id,
        organization_unit_id=organization_unit_id, agent_version_id=agent_version_id,
        model_endpoint_version_id=model_endpoint_version_id, status=run_status, lifecycle=lifecycle,
        health=health, start=start, end=end)
    range_start, range_end = dashboard_window(days, scope)
    conditions = [
        AgentErrorEvent.occurred_at >= range_start,
        AgentErrorEvent.occurred_at < range_end,
    ]
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
    if scope.has_run_dimensions or lifecycle is not None or health is not None:
        eligible_runs = filter_dashboard_runs(select(AgentRun.id), tenant_id=tenant_id,
            start=range_start, end=range_end, filters=scope)
        conditions.append(AgentErrorEvent.run_id.in_(eligible_runs))
    total = int(
        await session.scalar(
            select(func.count())
            .select_from(AgentErrorEvent)
            .where(AgentErrorEvent.tenant_id == tenant_id, *conditions)
        )
        or 0
    )
    rows = list(
        (
            await session.execute(
                _error_event_query(tenant_id)
                .where(*conditions)
                .order_by(AgentErrorEvent.occurred_at.desc(), AgentErrorEvent.id.desc())
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        ).all()
    )
    metadata = pagination_metadata(total, pagination)
    return AdminDashboardErrorEventListResponse(
        items=[_error_event_item(row) for row in rows],
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.get("/v1/admin/analytics/errors/{event_id}", response_model=AdminDashboardErrorEventDetail)
async def read_error_event_detail(
    event_id: UUID,
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardErrorEventDetail:
    tenant_id = identity.principal.tenant_id
    row = (
        await session.execute(_error_event_query(tenant_id).where(AgentErrorEvent.id == event_id))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="错误记录不存在或已移除")
    event = row[0]
    item = _error_event_item(row)
    observations = list(
        (
            await session.scalars(
                select(AgentRunObservation)
                .where(
                    AgentRunObservation.run_id == event.run_id,
                    AgentRunObservation.tenant_id == tenant_id,
                )
                .order_by(AgentRunObservation.started_at, AgentRunObservation.id)
            )
        ).all()
    )
    return AdminDashboardErrorEventDetail(
        **item.model_dump(),
        metadata=event.metadata_payload,
        observations=[
            AdminDashboardTraceObservation(
                id=obs.id,
                kind=obs.kind,
                name=obs.name,
                status=obs.status,
                started_at=obs.started_at,
                first_output_at=obs.first_output_at,
                finished_at=obs.finished_at,
                duration_ms=obs.duration_ms,
                input_tokens=obs.input_tokens,
                output_tokens=obs.output_tokens,
                total_cost_microusd=obs.total_cost_microusd,
                error_code=obs.error_code,
                metadata=obs.metadata_payload,
            )
            for obs in observations
        ],
    )


@router.get("/v1/admin/dashboard/runs/{run_id}/trace", response_model=AdminDashboardTraceResponse)
async def read_dashboard_trace(
    run_id: UUID,
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> AdminDashboardTraceResponse:
    from supportops_core.business_trace_services import project_business_trace
    row = await session.execute(
        select(AgentRun, Agent.name)
        .join(Agent, Agent.id == AgentRun.agent_id)
        .where(AgentRun.id == run_id, AgentRun.tenant_id == identity.principal.tenant_id)
    )
    found = row.first()
    if found is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    run, name = found
    observations = list(
        (
            await session.scalars(
                select(AgentRunObservation)
                .where(
                    AgentRunObservation.run_id == run_id,
                    AgentRunObservation.tenant_id == identity.principal.tenant_id,
                )
                .order_by(AgentRunObservation.started_at, AgentRunObservation.id)
            )
        ).all()
    )
    try:
        async with session.begin_nested():
            business_trace = await project_business_trace(session, run=run)
    except Exception:
        # 阶段投影失败保留其他观测；日志只记稳定资源标识，不记上下文正文。
        structlog.get_logger(__name__).warning("business_trace_projection_failed", run_id=str(run_id))
        business_trace = None
    return AdminDashboardTraceResponse(
        run=_run_item(run, name),
        business_trace=business_trace,
        budget=await model_budget_summary(session, tenant_id=identity.principal.tenant_id, run_id=run_id),
        observations=[
            AdminDashboardTraceObservation(
                id=item.id,
                kind=item.kind,
                name=item.name,
                status=item.status,
                started_at=item.started_at,
                first_output_at=item.first_output_at,
                finished_at=item.finished_at,
                duration_ms=item.duration_ms,
                input_tokens=item.input_tokens,
                output_tokens=item.output_tokens,
                total_cost_microusd=item.total_cost_microusd,
                error_code=item.error_code,
                metadata=item.metadata_payload,
                attempt_id=item.attempt_id,
                span_id=item.span_id,
                parent_observation_id=item.parent_observation_id,
                error_message=runtime_error_message(item.error_code),
            )
            for item in observations
        ],
    )


@router.get(
    "/v1/admin/system/dashboard/agents/status", response_model=SystemAgentStatusListResponse
)
async def read_system_agent_status(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    scope: DashboardFilters = Depends(dashboard_filters),
    pagination: PaginationParams = Depends(pagination_params),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> SystemAgentStatusListResponse:
    tenant_id = identity.principal.tenant_id
    # GET 只读协调任务的快照，不能通过读取制造新的成功观测时间。
    observed_now = scope.observed_at or utc_now()
    stale_seconds = request.app.state.settings.health_snapshot_stale_seconds
    start, end = dashboard_window(days, scope)
    total = int(
        await session.scalar(
            filter_dashboard_agents(select(func.count()).select_from(Agent),
                tenant_id=tenant_id, filters=scope, start=start, end=end)
        )
        or 0
    )
    agents = list(
        (
            await session.scalars(
                filter_dashboard_agents(select(Agent), tenant_id=tenant_id, filters=scope, start=start, end=end)
                .order_by(Agent.name, Agent.id)
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        ).all()
    )
    health_rows = (
        list(
            (
                await session.scalars(
                    select(AgentRuntimeHealth).where(
                        AgentRuntimeHealth.tenant_id == tenant_id,
                        AgentRuntimeHealth.agent_id.in_([agent.id for agent in agents]),
                    )
                )
            ).all()
        )
        if agents
        else []
    )
    health_by_agent = {row.agent_id: row for row in health_rows}
    items: list[SystemAgentStatusItem] = []
    for agent in agents:
        health = health_by_agent.get(agent.id)
        activity = await agent_activity(session, tenant_id=tenant_id, agent_id=agent.id)
        active_count = activity.active_count
        snapshot_times = [health.observed_at, health.computed_at] if health else []
        snapshot_times = [value.replace(tzinfo=UTC) if value.tzinfo is None else value for value in snapshot_times if value is not None]
        health_stale = len(snapshot_times) != 2 or any(
            (observed_now - value).total_seconds() > stale_seconds or (value - observed_now).total_seconds() > 5
            for value in snapshot_times
        )
        recent_runs = list(
            (
                await session.execute(
                    select(AgentRun.id, AgentRun.status, AgentRun.created_at, AgentRun.finished_at)
                    .where(AgentRun.tenant_id == tenant_id, AgentRun.agent_id == agent.id)
                    .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
                    .limit(20)
                )
            ).all()
        )
        latest = recent_runs[0] if recent_runs else None
        # 缺少读模型时不以最近 20 条推断健康或误报单次失败。
        derived_error_rate = None
        derived_health, derived_reason = "unknown", "健康快照尚未生成，请检查协调任务"
        if health is not None and health_stale:
            derived_reason = "健康快照已过期或时钟异常，当前健康未知，请检查协调任务"
        display_health = health.health_status if health and not health_stale else derived_health
        display_reason = health.health_reason if health and not health_stale else derived_reason
        if agent.status != AgentStatus.ACTIVE:
            display_health, display_reason = "unknown", "Agent 当前未启用"
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
            else:
                pending_publish = True
        items.append(
            SystemAgentStatusItem(
                id=agent.id,
                name=agent.name,
                lifecycle_status=agent.status.value,
                execution_status=activity.execution_status,
                health_status=display_health,
                health_reason=display_reason,
                last_run_at=health.last_run_at
                if health and health.last_run_at
                else (latest.finished_at or latest.created_at if latest else None),
                last_success_at=health.last_success_at
                if health and health.last_success_at
                else next(
                    (run.finished_at for run in recent_runs if run.status == RunStatus.COMPLETED),
                    None,
                ),
                last_failure_at=health.last_failure_at
                if health and health.last_failure_at
                else next(
                    (run.finished_at for run in recent_runs if run.status == RunStatus.FAILED), None
                ),
                active_run_count=active_count,
                error_rate=health.error_rate
                if health and not health_stale and health.error_rate is not None
                else derived_error_rate,
                p95_latency_ms=health.p95_latency_ms
                if health and not health_stale and health.p95_latency_ms is not None
                else None,
                pending_publish=pending_publish,
                observed_at=health.observed_at if health else None,
                computed_at=health.computed_at if health else None,
                health_stale=health_stale,
            )
        )
    metadata = pagination_metadata(total, pagination)
    return SystemAgentStatusListResponse(
        items=items,
        total=metadata.total,
        page=metadata.page,
        page_size=metadata.page_size,
        pages=metadata.pages,
    )


@router.get("/v1/admin/system/dashboard/rankings", response_model=SystemAgentRankingsResponse)
async def read_system_rankings(
    days: int = Query(default=7, ge=1, le=90),
    scope: DashboardFilters = Depends(dashboard_filters),
    identity: IdentityContext = Depends(admin_identity),
    session: AsyncSession = Depends(database_session),
) -> SystemAgentRankingsResponse:
    start, end = dashboard_window(days, scope)
    rankings = await dashboard_rankings(session, tenant_id=identity.principal.tenant_id, filters=scope, start=start, end=end)
    return SystemAgentRankingsResponse(rankings={key: [SystemAgentRankingItem(
        agent_id=item.agent_id, agent_name=item.agent_name, value=item.value) for item in items]
        for key, items in rankings.items()})
