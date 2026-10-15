"""Dashboard 共用查询范围：历史调用维度与当前 Agent 状态明确区分。"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID
from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.enums import AgentStatus, RunStatus
from supportops_core.models import Agent, AgentRun, AgentRuntimeHealth, Conversation, ToolInvocation, User

Rows = TypeVar("Rows", bound=tuple[object, ...])


@dataclass(frozen=True)
class DashboardFilters:
    agent_id: UUID | None = None
    user_id: UUID | None = None
    organization_unit_id: UUID | None = None
    agent_version_id: UUID | None = None
    model_endpoint_version_id: UUID | None = None
    status: RunStatus | None = None
    lifecycle: AgentStatus | None = None
    health: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    observed_at: datetime | None = None
    stale_seconds: float = 90
    queue_name: str | None = None

    @property
    def has_run_dimensions(self) -> bool:
        return any(value is not None for value in (self.user_id, self.organization_unit_id,
            self.agent_version_id, self.model_endpoint_version_id, self.status))


def agent_health_expression(filters: DashboardFilters) -> ColumnElement[str]:
    """当前健康表达式由筛选、全量分布和状态页共用相同时间规则。"""
    now = filters.observed_at or datetime.now(UTC)
    invalid = or_(AgentRuntimeHealth.observed_at.is_(None), AgentRuntimeHealth.computed_at.is_(None),
        AgentRuntimeHealth.observed_at < now-timedelta(seconds=filters.stale_seconds),
        AgentRuntimeHealth.computed_at < now-timedelta(seconds=filters.stale_seconds),
        AgentRuntimeHealth.observed_at > now+timedelta(seconds=5), AgentRuntimeHealth.computed_at > now+timedelta(seconds=5))
    return case((or_(Agent.status != AgentStatus.ACTIVE, invalid), "unknown"),
        else_=func.coalesce(AgentRuntimeHealth.health_status, "unknown"))


def agent_scope_query(*, tenant_id: UUID, filters: DashboardFilters) -> Select[tuple[UUID]]:
    """当前生命周期/快照状态，缺失与 stale 的 SQL 口径和状态响应一致。"""
    query = select(Agent.id).outerjoin(AgentRuntimeHealth, and_(AgentRuntimeHealth.agent_id == Agent.id,
        AgentRuntimeHealth.tenant_id == tenant_id)).where(Agent.tenant_id == tenant_id, Agent.deleted_at.is_(None))
    if filters.agent_id is not None:
        query = query.where(Agent.id == filters.agent_id)
    if filters.lifecycle is not None:
        query = query.where(Agent.status == filters.lifecycle)
    if filters.health is not None:
        query = query.where(agent_health_expression(filters) == filters.health)
    return query.correlate(None)


def filter_dashboard_runs(query: Select[Rows], *, tenant_id: UUID, start: datetime, end: datetime,
    filters: DashboardFilters,
) -> Select[Rows]:
    conditions = [AgentRun.tenant_id == tenant_id, AgentRun.created_at >= start, AgentRun.created_at < end]
    # 与列表的关联行保持相同租户边界；历史软删除由 include_deleted 统一处理。
    valid_agents = select(Agent.id).where(Agent.tenant_id == tenant_id).correlate(None)
    valid_conversations = select(Conversation.id).join(User, and_(User.id == Conversation.user_id, User.tenant_id == tenant_id))
    valid_conversations = valid_conversations.where(Conversation.tenant_id == tenant_id).correlate(None)
    conditions.extend([AgentRun.agent_id.in_(valid_agents), AgentRun.conversation_id.in_(valid_conversations)])
    if filters.agent_id is not None:
        conditions.append(AgentRun.agent_id == filters.agent_id)
    if filters.status is not None:
        conditions.append(AgentRun.status == filters.status)
    if filters.agent_version_id is not None:
        conditions.append(AgentRun.agent_version_id == filters.agent_version_id)
    if filters.model_endpoint_version_id is not None:
        conditions.append(AgentRun.model_endpoint_version_id == filters.model_endpoint_version_id)
    if filters.user_id is not None or filters.organization_unit_id is not None:
        users = select(Conversation.id).join(User, and_(User.id == Conversation.user_id, User.tenant_id == tenant_id))
        users = users.where(Conversation.tenant_id == tenant_id)
        if filters.user_id is not None:
            users = users.where(Conversation.user_id == filters.user_id)
        if filters.organization_unit_id is not None:
            users = users.where(User.organization_unit_id == filters.organization_unit_id)
        conditions.append(AgentRun.conversation_id.in_(users.correlate(None)))
    if filters.lifecycle is not None or filters.health is not None:
        conditions.append(AgentRun.agent_id.in_(agent_scope_query(tenant_id=tenant_id, filters=filters)))
    # 管理分析保留历史会话；过滤和 count/paging 共用同一 SQL 范围。
    return query.where(*conditions).execution_options(include_deleted=True)


def filter_dashboard_agents(query: Select[Rows], *, tenant_id: UUID, filters: DashboardFilters,
    start: datetime, end: datetime,
) -> Select[Rows]:
    query = query.where(Agent.id.in_(agent_scope_query(tenant_id=tenant_id, filters=filters)))
    if filters.has_run_dimensions:
        runs = filter_dashboard_runs(select(AgentRun.agent_id), tenant_id=tenant_id, start=start, end=end, filters=filters)
        query = query.where(Agent.id.in_(runs))
    return query


@dataclass(frozen=True)
class DashboardRankingItem:
    agent_id: UUID
    agent_name: str
    value: int | float | None


@dataclass(frozen=True)
class DashboardRunMetrics:
    runs: int
    completed: int
    failed: int
    cancelled: int
    active_users: int
    input_tokens: int | None
    output_tokens: int | None
    cost_microusd: int | None
    input_missing: int
    output_missing: int
    cost_missing: int
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    p99_latency_ms: int | None
    latency_samples: int
    terminal: int


async def dashboard_run_metrics(session: AsyncSession, *, tenant_id: UUID, filters: DashboardFilters,
    start: datetime, end: datetime,
) -> DashboardRunMetrics:
    """调用摘要仅取 SQL 聚合；空集合为零，缺失 usage 不能用已知子集冒充总量。"""
    count = func.count(AgentRun.id)
    query = select(count.label("runs"),
        func.sum(case((AgentRun.status == RunStatus.COMPLETED, 1), else_=0)).label("completed"),
        func.sum(case((AgentRun.status == RunStatus.FAILED, 1), else_=0)).label("failed"),
        func.sum(case((AgentRun.status == RunStatus.CANCELLED, 1), else_=0)).label("cancelled"),
        func.count(func.distinct(Conversation.user_id)).label("users"),
        func.count(AgentRun.input_tokens).label("input_known"), func.sum(AgentRun.input_tokens).label("input_sum"),
        func.count(AgentRun.output_tokens).label("output_known"), func.sum(AgentRun.output_tokens).label("output_sum"),
        func.count(AgentRun.total_cost_microusd).label("cost_known"), func.sum(AgentRun.total_cost_microusd).label("cost_sum"))
    query = query.join(Conversation, and_(Conversation.id == AgentRun.conversation_id, Conversation.tenant_id == tenant_id))
    row = (await session.execute(filter_dashboard_runs(query, tenant_id=tenant_id, start=start, end=end, filters=filters))).one()
    runs = int(row.runs)
    input_missing, output_missing, cost_missing = runs-int(row.input_known), runs-int(row.output_known), runs-int(row.cost_known)
    timings = filter_dashboard_runs(select(AgentRun.end_to_end_latency_ms.label("latency"),
        func.row_number().over(order_by=(AgentRun.end_to_end_latency_ms, AgentRun.id)).label("position"),
        func.count().over().label("samples")), tenant_id=tenant_id, start=start, end=end, filters=filters)
    timings = timings.where(AgentRun.status.in_([RunStatus.COMPLETED, RunStatus.FAILED]), AgentRun.end_to_end_latency_ms >= 0).subquery()
    # 仅三个 nearest-rank 位置返回 API，不在进程中加载所有调用或正文。
    percentile_rows = (await session.execute(select(timings.c.latency, timings.c.position, timings.c.samples)
        .where(timings.c.position.in_([func.ceil(timings.c.samples * fraction) for fraction in (0.5, 0.95, 0.99)]))
        .execution_options(include_deleted=True))).all()
    samples = int(percentile_rows[0].samples) if percentile_rows else 0
    positions = {int(item.position): int(item.latency) for item in percentile_rows}
    from math import ceil
    percentiles = [positions.get(ceil(samples * fraction)) if samples >= 3 else None for fraction in (0.5, 0.95, 0.99)]
    completed, failed = int(row.completed or 0), int(row.failed or 0)
    return DashboardRunMetrics(runs, completed, failed, int(row.cancelled or 0), int(row.users),
        None if input_missing else int(row.input_sum or 0), None if output_missing else int(row.output_sum or 0),
        None if cost_missing else int(row.cost_sum or 0), input_missing, output_missing, cost_missing,
        *percentiles, samples, completed+failed)


async def dashboard_rankings(session: AsyncSession, *, tenant_id: UUID, filters: DashboardFilters,
    start: datetime, end: datetime,
) -> dict[str, list[DashboardRankingItem]]:
    """在 SQL 中聚合，不加载问题/回答/参数；未知不填零，平局按 UUID 排序。"""
    run_count = func.count(AgentRun.id)
    tokens = case((and_(func.count(AgentRun.input_tokens) == run_count,
        func.count(AgentRun.output_tokens) == run_count), func.sum(AgentRun.input_tokens + AgentRun.output_tokens)), else_=None)
    cost = case((func.count(AgentRun.total_cost_microusd) == run_count, func.sum(AgentRun.total_cost_microusd)), else_=None)
    query = select(AgentRun.agent_id, Agent.name.label("agent_name"), run_count.label("agent_runs"),
        tokens.label("tokens"), cost.label("cost"),
        func.sum(case((AgentRun.status == RunStatus.FAILED, 1), else_=0)).label("errors"),
        func.sum(case((AgentRun.status.in_([RunStatus.COMPLETED, RunStatus.FAILED]), 1), else_=0)).label("terminal"),
        func.count(func.distinct(Conversation.user_id)).label("active_users"),
        func.count(AgentRun.tool_call_count).label("tool_known"), func.sum(AgentRun.tool_call_count).label("tool_claimed"))
    query = query.join(Agent, and_(Agent.id == AgentRun.agent_id, Agent.tenant_id == tenant_id))
    query = query.join(Conversation, and_(Conversation.id == AgentRun.conversation_id, Conversation.tenant_id == tenant_id))
    rows = (await session.execute(filter_dashboard_runs(query, tenant_id=tenant_id, start=start, end=end,
        filters=filters).group_by(AgentRun.agent_id, Agent.name))).all()

    timings = filter_dashboard_runs(select(AgentRun.agent_id, AgentRun.end_to_end_latency_ms.label("latency"),
        func.row_number().over(partition_by=AgentRun.agent_id, order_by=(AgentRun.end_to_end_latency_ms, AgentRun.id)).label("position"),
        func.count().over(partition_by=AgentRun.agent_id).label("samples")), tenant_id=tenant_id, start=start, end=end, filters=filters)
    timings = timings.where(AgentRun.status.in_([RunStatus.COMPLETED, RunStatus.FAILED]), AgentRun.end_to_end_latency_ms >= 0).subquery()
    p95_rows = (await session.execute(select(timings.c.agent_id, timings.c.latency).where(
        timings.c.samples >= 3, timings.c.position == func.ceil(timings.c.samples * 0.95)).execution_options(include_deleted=True))).all()
    p95 = {row.agent_id: int(row.latency) for row in p95_rows}
    tools = select(AgentRun.agent_id, func.count(ToolInvocation.id).label("invocations")).join(ToolInvocation,
        and_(ToolInvocation.run_id == AgentRun.id, ToolInvocation.tenant_id == tenant_id))
    tools = filter_dashboard_runs(tools, tenant_id=tenant_id, start=start, end=end, filters=filters)
    tool_rows = (await session.execute(tools.where(ToolInvocation.status != "denied").group_by(AgentRun.agent_id))).all()
    tool_counts = {row.agent_id: int(row.invocations) for row in tool_rows}
    metrics: dict[str, list[DashboardRankingItem]] = {key: [] for key in (
        "agent_runs", "active_users", "failure_rate", "tokens", "cost_microusd", "errors", "p95_latency_ms", "tool_calls")}
    for row in rows:
        observed_tools = tool_counts.get(row.agent_id, 0)
        known_tools = row.tool_known == row.agent_runs and int(row.tool_claimed or 0) == observed_tools
        values = {"agent_runs": int(row.agent_runs), "active_users": int(row.active_users), "errors": int(row.errors or 0),
            "failure_rate": round(int(row.errors or 0) / int(row.terminal) * 100, 2) if row.terminal else None,
            "tokens": int(row.tokens) if row.tokens is not None else None,
            "cost_microusd": int(row.cost) if row.cost is not None else None,
            "p95_latency_ms": p95.get(row.agent_id), "tool_calls": observed_tools if known_tools else None}
        for key, value in values.items():
            metrics[key].append(DashboardRankingItem(row.agent_id, row.agent_name, value))
    return {key: sorted(items, key=lambda item: (item.value is None, -(item.value or 0), str(item.agent_id)))[:10]
        for key, items in metrics.items()}
