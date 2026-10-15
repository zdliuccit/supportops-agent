"""Agent Runtime 健康快照计算；API 与 Worker 共用同一套数据口径。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from math import ceil
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_config import TOOL_REGISTRY, agent_config_digest, parse_agent_config, pinned_tool_version
from supportops_core.config import Settings
from supportops_core.credential_services import assert_credential_revision_active, CredentialAccessDenied
from supportops_core.models import ModelCredential
from supportops_core.diagnostic_mock_services import MOCK_DIAGNOSTIC_TOOLS
from supportops_core.enums import AgentStatus, ApprovalPolicy, RunStatus
from supportops_core.tool_policy import legacy_risk_number
from supportops_core.models import (
    Agent,
    AgentDraft,
    AgentRun,
    AgentRuntimeHealth,
    AgentVersion,
    Conversation,
    ModelEndpoint,
    ModelEndpointVersion,
    RunAttempt,
    RuntimeServiceLease,
    ToolCatalogEntry,
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


@dataclass(frozen=True, slots=True)
class AgentActivity:
    queued: int
    running: int
    cancelling: int
    needs_attention: int
    waiting_approval: int = 0
    waiting_action: int = 0

    @property
    def active_count(self) -> int:
        return self.queued + self.running + self.cancelling

    @property
    def execution_status(self) -> str:
        if self.needs_attention:
            return "needs_attention"
        if self.running:
            return "running"
        if self.cancelling:
            return "cancelling"
        if self.waiting_approval:
            return "waiting_approval"
        if self.waiting_action:
            return "waiting_action"
        return "queued" if self.queued else "idle"


async def agent_activity(session: AsyncSession, *, tenant_id: UUID, agent_id: UUID,
                         now: datetime | None = None) -> AgentActivity:
    """活跃请求不受历史窗口截断；只把有效当前许可当作正在执行。"""
    observed_at = now or datetime.now(UTC)
    queued = and_(AgentRun.status == RunStatus.QUEUED,
        or_(AgentRun.recovery_reason.is_(None), AgentRun.recovery_reason.not_in((
            "RUN_NOTIFICATION_EXHAUSTED", "ACTION_RECONCILIATION_REQUIRED", "LEGACY_EXECUTOR_CONFIRMATION_REQUIRED"))))
    valid = and_(RunAttempt.status == "running", RunAttempt.lease_expires_at > observed_at,
        RunAttempt.tenant_id == tenant_id, RunAttempt.run_id == AgentRun.id,
        Conversation.tenant_id == tenant_id,
        RunAttempt.generation == AgentRun.execution_generation,
        Conversation.active_attempt_id == RunAttempt.id, Conversation.active_run_id == AgentRun.id)
    running, cancelling = and_(AgentRun.status == RunStatus.RUNNING, valid), and_(AgentRun.status == RunStatus.CANCELLING, valid)
    row = (await session.execute(select(func.count().filter(queued), func.count().filter(running),
        func.count().filter(cancelling), func.count().filter(AgentRun.status == RunStatus.WAITING_APPROVAL),
        func.count().filter(AgentRun.status == RunStatus.WAITING_ACTION), func.count())
        .select_from(AgentRun).outerjoin(RunAttempt, RunAttempt.id == AgentRun.current_attempt_id)
        .outerjoin(Conversation, Conversation.id == AgentRun.conversation_id)
        .where(AgentRun.tenant_id == tenant_id, AgentRun.agent_id == agent_id,
            AgentRun.status.in_((RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION)))
        .execution_options(include_deleted=True))).one()
    return AgentActivity(int(row[0]), int(row[1]), int(row[2]), max(0, int(row[5])-sum(int(row[index]) for index in range(5))), int(row[3]), int(row[4]))


async def _version_issue(session: AsyncSession, *, tenant_id: UUID, version: AgentVersion | None,
                         settings: Settings | None) -> str | None:
    if version is None or version.tenant_id != tenant_id:
        return "活动版本不可用"
    try:
        config = parse_agent_config(version.config)
    except ValueError:
        return "活动版本配置无法解析"
    model = await session.scalar(select(ModelEndpointVersion).join(ModelEndpoint,
        ModelEndpoint.id == ModelEndpointVersion.endpoint_id).where(
        ModelEndpointVersion.id == version.model_endpoint_version_id, ModelEndpointVersion.tenant_id == tenant_id,
        ModelEndpoint.tenant_id == tenant_id, ModelEndpoint.is_enabled.is_(True)))
    if model is None:
        return "固定模型端点停用或不可用"
    credential = await session.scalar(select(ModelCredential).where(ModelCredential.id == model.credential_id,
        ModelCredential.tenant_id == tenant_id))
    if credential is None:
        return "固定模型凭据不存在"
    try:
        await assert_credential_revision_active(session, tenant_id=tenant_id, credential_id=credential.id, revision=credential.revision)
    except CredentialAccessDenied as exc:
        return str(exc)
    tools = [binding.tool_id for binding in config.tools if binding.enabled]
    catalog = {entry.tool_id: entry for entry in (await session.scalars(select(ToolCatalogEntry).where(
        ToolCatalogEntry.tenant_id == tenant_id, ToolCatalogEntry.tool_id.in_(tools)))).all()} if tools else {}
    for tool_id in tools:
        definition, entry = TOOL_REGISTRY.get(tool_id), catalog.get(tool_id)
        if definition is None or pinned_tool_version(version.resolved_tool_versions, tool_id) != definition.version:
            return "固定工具实现版本不可用，需显式重新发布"
        if entry is None or not entry.is_enabled or entry.is_revoked:
            return "绑定工具目录未就绪、停用或紧急撤销"
        binding = next(item for item in config.tools if item.tool_id == tool_id)
        if not definition.action_proposal_only and (binding.approval_policy == ApprovalPolicy.REQUIRED or max(
            definition.minimum_risk_number, legacy_risk_number(definition.risk_level), legacy_risk_number(entry.risk_level)) >= 3):
            return "绑定工具当前需要有效审批凭证，执行门禁将拒绝未批准调用"
        if settings is not None and tool_id in MOCK_DIAGNOSTIC_TOOLS and (not settings.diagnostic_mock_enabled or settings.environment == "production"):
            return "活动版本绑定的隔离 Mock 已关闭或禁止用于 production"
        if settings is not None and definition.action_proposal_only and (not settings.business_action_mock_enabled or settings.environment == "production"):
            return "活动版本绑定的隔离动作 Mock 已关闭或禁止用于 production"
    return None


async def refresh_agent_runtime_health(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    now: datetime | None = None,
    window: timedelta = timedelta(hours=24),
    settings: Settings | None = None,
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
                select(RuntimeServiceLease).where(RuntimeServiceLease.service_status.in_(("healthy", "degraded")))
            )
        ).all()
    )
    service_available = any(
        (lease.expires_at is not None and (_aware(lease.expires_at) or observed_at) > observed_at)
        for lease in leases
    )
    service_degraded = service_available and not any(lease.service_status == "healthy"
        and (_aware(lease.expires_at) or observed_at) > observed_at for lease in leases)
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
        activity = await agent_activity(session, tenant_id=tenant_id, agent_id=agent.id, now=observed_at)
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
        active_version = None
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
        version_issue = await _version_issue(session, tenant_id=tenant_id, version=active_version, settings=settings) if agent.status == AgentStatus.ACTIVE else None

        if agent.status != AgentStatus.ACTIVE:
            health_status, reason = "unknown", "Agent 当前未启用"
        elif version_issue:
            health_status, reason = "degraded", version_issue
        elif not service_available:
            health_status, reason = "unknown", "执行服务不可用或租约已过期"
        elif activity.needs_attention:
            health_status, reason = "degraded", "存在过期/未证明有效的执行或通知死信，需要恢复、确认旧执行者或对账"
        elif service_degraded:
            health_status, reason = "degraded", "共享执行服务仍存活，但正在排空或维护异常"
        elif activity.active_count:
            health_status, reason = "healthy", f"排队 {activity.queued}，有效执行 {activity.running}，取消中 {activity.cancelling}"
        elif activity.waiting_approval or activity.waiting_action:
            health_status, reason = "healthy", f"等待审批 {activity.waiting_approval}，等待动作处理 {activity.waiting_action}；模型执行已释放"
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
        snapshot.active_run_count = activity.active_count
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
    settings: Settings | None = None,
) -> int:
    """刷新当前数据库中所有租户的健康快照，供 Worker 周期任务调用。"""
    tenant_ids = list((await session.scalars(select(Agent.tenant_id).distinct())).all())
    for tenant_id in tenant_ids:
        await refresh_agent_runtime_health(session, tenant_id=tenant_id, now=now, window=window, settings=settings)
    return len(tenant_ids)
