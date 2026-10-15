"""固定主体灰度控制、显式扩大和有界系统巡检；所有变更沿Agent锁串行。"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from supportops_core.agent_services import get_agent, ensure_agent_mutable
from supportops_core.enums import AgentStatus
from supportops_core.models import Agent, AgentReleaseRollout, AgentReleaseRolloutEvent, AgentVersion, User, utc_now
from supportops_core.release_gate_services import ReleaseReadiness, assess_release_readiness
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.release_rollout_contracts import (
    ReleaseRolloutCommand, ReleaseRolloutCreate, ReleaseRolloutDetail, ReleaseRolloutEventSummary,
    ReleaseRolloutPolicy, ReleaseRolloutSummary,
)
from supportops_core.release_rollout_observations import aware, observe_release_rollout
from supportops_core.config import Settings
from supportops_core.services import ResourceNotFoundError

LIVE_STATUSES = ("draft", "active", "paused")


class ReleaseRolloutConflict(ValueError):
    code = "AGENT_ROLLOUT_CONFLICT"


def _definition(rollout: AgentReleaseRollout) -> str:
    return manifest_digest({"schema": "release_rollout_definition_v1", "id": str(rollout.id),
        "tenant_id": str(rollout.tenant_id), "agent_id": str(rollout.agent_id),
        "baseline_version_id": str(rollout.baseline_version_id), "candidate_version_id": str(rollout.candidate_version_id),
        "subject_ids": sorted(rollout.subject_ids), "policy_digest": rollout.policy_digest,
        "selection": "sha256_tenant_agent_rollout_subject_v1"})


def validate_rollout_definition(rollout: AgentReleaseRollout) -> ReleaseRolloutPolicy:
    try:
        policy = ReleaseRolloutPolicy.model_validate(rollout.policy)
        if not isinstance(rollout.subject_ids, list) or any(not isinstance(value, str) for value in rollout.subject_ids):
            raise ValueError("invalid subject types")
        if len(rollout.subject_ids) > 200 or len(set(rollout.subject_ids)) != len(rollout.subject_ids):
            raise ValueError("invalid subjects")
        if any(str(UUID(value)) != value for value in rollout.subject_ids):
            raise ValueError("noncanonical subject")
        if manifest_digest(policy.model_dump(mode="json")) != rollout.policy_digest or _definition(rollout) != rollout.definition_digest:
            raise ValueError("definition digest mismatch")
        return policy
    except (ValidationError, ValueError, TypeError) as exc:
        raise ReleaseRolloutConflict("灰度定义或策略摘要不一致，已阻止新增候选流量") from exc


def rollout_summary(row: AgentReleaseRollout) -> ReleaseRolloutSummary:
    return ReleaseRolloutSummary.model_validate({name: getattr(row, name) for name in ReleaseRolloutSummary.model_fields})


def _append_event(session: AsyncSession, row: AgentReleaseRollout, *, event_type: str, actor_user_id: UUID | None) -> None:
    session.add(AgentReleaseRolloutEvent(tenant_id=row.tenant_id, rollout_id=row.id, revision=row.revision,
        actor_kind="user" if actor_user_id else "system", actor_user_id=actor_user_id, event_type=event_type,
        status=row.status, candidate_bps=row.candidate_bps, epoch=row.epoch, reason_code=row.reason_code,
        policy_digest=row.policy_digest))


async def get_release_rollout(session: AsyncSession, *, tenant_id: UUID, agent_id: UUID,
    rollout_id: UUID, lock: bool = False,
) -> AgentReleaseRollout:
    query = select(AgentReleaseRollout).where(AgentReleaseRollout.id == rollout_id,
        AgentReleaseRollout.tenant_id == tenant_id, AgentReleaseRollout.agent_id == agent_id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise ResourceNotFoundError("灰度发布不存在")
    return row


async def _versions(session: AsyncSession, *, rollout: AgentReleaseRollout, lock: bool = False) -> dict[UUID, AgentVersion]:
    query = select(AgentVersion).where(AgentVersion.tenant_id == rollout.tenant_id, AgentVersion.agent_id == rollout.agent_id,
        AgentVersion.id.in_({rollout.baseline_version_id, rollout.candidate_version_id})).order_by(AgentVersion.id)
    if lock:
        query = query.with_for_update(read=True)
    result = {row.id: row for row in (await session.scalars(query.execution_options(populate_existing=True))).all()}
    if len(result) != 2:
        raise ReleaseRolloutConflict("灰度固定版本不可用，不能替换为其他版本")
    return result


async def _readiness(session: AsyncSession, *, rollout: AgentReleaseRollout, settings: Settings,
    lock: bool = False,
) -> tuple[ReleaseReadiness, ReleaseReadiness]:
    versions = await _versions(session, rollout=rollout, lock=lock)
    outcomes = {}
    for version_id in sorted(versions, key=str):
        outcomes[version_id] = await assess_release_readiness(session, version=versions[version_id], settings=settings, lock_references=lock)
    return outcomes[rollout.baseline_version_id], outcomes[rollout.candidate_version_id]


def _approved(rollout: AgentReleaseRollout, settings: Settings) -> bool:
    return settings.release_rollout_policy_digest is not None and rollout.policy_digest == settings.release_rollout_policy_digest


def _supervisor_fresh(row: AgentReleaseRollout, settings: Settings, now: datetime) -> bool:
    return row.last_checked_at is not None and timedelta(0) <= now-aware(row.last_checked_at) <= timedelta(seconds=settings.release_rollout_stale_seconds)


async def create_release_rollout(session: AsyncSession, *, tenant_id: UUID, agent_id: UUID,
    actor_user_id: UUID, payload: ReleaseRolloutCreate,
) -> AgentReleaseRollout:
    from uuid import uuid4
    agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id, lock=True)
    ensure_agent_mutable(agent)
    if agent.status != AgentStatus.ACTIVE or agent.active_version_id is None:
        raise ReleaseRolloutConflict("只有已有活动基线版本的Agent可以保存灰度草稿")
    existing = await session.scalar(select(AgentReleaseRollout.id).where(AgentReleaseRollout.tenant_id == tenant_id,
        AgentReleaseRollout.agent_id == agent_id, AgentReleaseRollout.status.in_(LIVE_STATUSES)))
    if existing is not None:
        raise ReleaseRolloutConflict("该Agent已有未停止的灰度，请先处理已有灰度")
    subjects = sorted(set(payload.subject_ids), key=str)
    if len(subjects) != len(payload.subject_ids):
        raise ReleaseRolloutConflict("灰度主体不能重复")
    if subjects:
        count = await session.scalar(select(func.count()).select_from(User).where(User.tenant_id == tenant_id,
            User.id.in_(subjects), User.status == "active"))
        if count != len(subjects):
            raise ReleaseRolloutConflict("灰度主体须为当前租户有效用户；不会自动授予Agent或业务权限")
    row = AgentReleaseRollout(id=uuid4(), tenant_id=tenant_id, agent_id=agent_id,
        baseline_version_id=agent.active_version_id, candidate_version_id=payload.candidate_version_id,
        subject_ids=[str(value) for value in subjects], policy=payload.policy.model_dump(mode="json"),
        policy_digest=manifest_digest(payload.policy.model_dump(mode="json")), status="draft",
        candidate_bps=payload.initial_candidate_bps, revision=1, epoch=0, created_by=actor_user_id)
    versions = await _versions(session, rollout=row)
    if versions[row.candidate_version_id].version_number <= versions[row.baseline_version_id].version_number:
        raise ReleaseRolloutConflict("灰度候选须比固定活动基线更新，回滚使用明确的版本激活入口")
    if any(version.withdrawn_at is not None for version in versions.values()):
        raise ReleaseRolloutConflict("已撤回版本不能用于新的灰度")
    row.definition_digest = _definition(row)
    session.add(row)
    await session.flush()
    _append_event(session, row, event_type="created", actor_user_id=actor_user_id)
    return row


async def release_rollout_detail(session: AsyncSession, *, rollout: AgentReleaseRollout, settings: Settings) -> ReleaseRolloutDetail:
    policy = validate_rollout_definition(rollout)
    baseline, candidate = await _readiness(session, rollout=rollout, settings=settings)
    observation = await observe_release_rollout(session, rollout=rollout, policy=policy)
    agent = await get_agent(session, agent_id=rollout.agent_id, tenant_id=rollout.tenant_id)
    approved = _approved(rollout, settings)
    current = agent.status == AgentStatus.ACTIVE and agent.active_version_id == rollout.baseline_version_id
    gates = baseline.status == "passed" and candidate.status == "passed"
    fresh = _supervisor_fresh(rollout, settings, observation.observed_at)
    ready = approved and current and gates
    reasons = []
    if not approved:
        reasons.append("策略摘要尚未被服务器配置批准，不能启动或扩大")
    if not current:
        reasons.append("活动基线或Agent生命周期已变化，原灰度不能继续")
    if not gates:
        reasons.append("基线与候选都必须完整通过当前发布门禁，开发未验收也不能启动灰度")
    if rollout.status == "active" and not fresh:
        reasons.append("Worker巡检已过期，当前不分配新候选会话；需排查巡检，若状态已暂停则须显式恢复")
    if rollout.status == "active" and not observation.can_expand:
        reasons.append("本轮观察时长、两组样本、完整计量或退化门槛尚未满足")
    return ReleaseRolloutDetail(rollout=rollout_summary(rollout), subject_ids=rollout.subject_ids,
        policy=policy, policy_approved=approved, supervisor_fresh=fresh, baseline_readiness=baseline,
        candidate_readiness=candidate, observation=observation, start_allowed=rollout.status == "draft" and ready,
        resume_allowed=rollout.status == "paused" and ready,
        expand_allowed=rollout.status == "active" and ready and fresh and observation.can_expand, disabled_reasons=reasons)


async def command_release_rollout(session: AsyncSession, *, tenant_id: UUID, agent_id: UUID, rollout_id: UUID,
    actor_user_id: UUID, payload: ReleaseRolloutCommand, settings: Settings,
) -> AgentReleaseRollout:
    agent = await get_agent(session, agent_id=agent_id, tenant_id=tenant_id, lock=True)
    ensure_agent_mutable(agent)
    row = await get_release_rollout(session, tenant_id=tenant_id, agent_id=agent_id, rollout_id=rollout_id, lock=True)
    if row.revision != payload.expected_revision:
        raise ReleaseRolloutConflict("灰度状态或比例已变化，请刷新后重新操作")
    if row.status == "stopped":
        raise ReleaseRolloutConflict("该灰度已停止，不能复活")
    operation = payload.operation
    if operation != "expand" and payload.candidate_bps is not None:
        raise ReleaseRolloutConflict("只有扩大操作接受新的候选比例")
    now = utc_now()
    if operation == "pause":
        if row.status != "active":
            raise ReleaseRolloutConflict("仅运行中的灰度可以暂停")
        row.status, row.reason_code = "paused", "MANUAL_PAUSE"
    elif operation == "stop":
        row.status, row.reason_code = "stopped", "MANUAL_STOP"
        if row.epoch > 0 and agent.active_version_id != row.candidate_version_id:
            await _withdraw_candidate(session, row=row, actor_user_id=actor_user_id)
    else:
        policy = validate_rollout_definition(row)
        valid_state = row.status == {"start": "draft", "resume": "paused", "expand": "active"}[operation]
        if not valid_state or agent.status != AgentStatus.ACTIVE or agent.active_version_id != row.baseline_version_id:
            raise ReleaseRolloutConflict("当前状态或活动基线不允许此灰度操作")
        if not _approved(row, settings):
            raise ReleaseRolloutConflict("灰度策略摘要尚未获服务器配置批准")
        baseline, candidate = await _readiness(session, rollout=row, settings=settings, lock=True)
        if baseline.status != "passed" or candidate.status != "passed":
            raise ReleaseRolloutConflict("基线与候选均须有完整适用实测；开发未验收不能用于灰度")
        now = utc_now()
        if operation == "expand":
            observation = await observe_release_rollout(session, rollout=row, policy=policy)
            if not _supervisor_fresh(row, settings, now) or not observation.can_expand:
                raise ReleaseRolloutConflict("巡检、观察时长、两组样本或计量门槛未满足，不能扩大")
            if payload.candidate_bps is None or not row.candidate_bps < payload.candidate_bps <= min(policy.max_candidate_bps, row.candidate_bps+policy.max_step_bps):
                raise ReleaseRolloutConflict("每次扩大最多增加10个百分点，灰度最高90%；全量须显式激活候选")
            row.candidate_bps = payload.candidate_bps
        row.status, row.reason_code = "active", None
        row.epoch += 1
        row.epoch_started_at, row.last_checked_at = now, now
    row.revision += 1
    row.updated_at = now
    _append_event(session, row, event_type={"start": "started", "resume": "resumed", "expand": "expanded", "pause": "paused", "stop": "stopped"}[operation], actor_user_id=actor_user_id)
    await session.flush()
    return row


async def _withdraw_candidate(session: AsyncSession, *, row: AgentReleaseRollout, actor_user_id: UUID) -> None:
    from supportops_core.agent_services import append_agent_audit
    from supportops_core.enums import AuditAction
    candidate = await session.scalar(select(AgentVersion).where(AgentVersion.id == row.candidate_version_id,
        AgentVersion.agent_id == row.agent_id, AgentVersion.tenant_id == row.tenant_id)
        .execution_options(populate_existing=True).with_for_update())
    if candidate is not None and candidate.withdrawn_at is None:
        candidate.withdrawn_at, candidate.withdrawn_by = utc_now(), actor_user_id
        await append_agent_audit(session, tenant_id=row.tenant_id, agent_id=row.agent_id,
            actor_user_id=actor_user_id, action=AuditAction.VERSION_WITHDRAWN, correlation_id="release-rollout:"+str(row.id),
            version_id=candidate.id, metadata={"reason_code": "rollout_stopped", "rollout_id": str(row.id), "reconciliation_preserved": True})


async def close_rollouts_for_activation(session: AsyncSession, *, agent: Agent, selected_version_id: UUID, actor_user_id: UUID) -> None:
    rows = (await session.scalars(select(AgentReleaseRollout).where(AgentReleaseRollout.tenant_id == agent.tenant_id,
        AgentReleaseRollout.agent_id == agent.id, AgentReleaseRollout.status.in_(LIVE_STATUSES)).with_for_update())).all()
    for row in rows:
        if row.epoch > 0 and selected_version_id != row.candidate_version_id:
            await _withdraw_candidate(session, row=row, actor_user_id=actor_user_id)
        row.status, row.reason_code, row.updated_at = "stopped", "ACTIVATION_CHANGED", utc_now()
        row.revision += 1
        _append_event(session, row, event_type="activation_closed", actor_user_id=actor_user_id)


@dataclass(frozen=True)
class ConversationReleaseSelection:
    version_id: UUID
    rollout_id: UUID | None = None
    epoch: int | None = None
    selection: str | None = None
    bucket: int | None = None


async def select_conversation_release(session: AsyncSession, *, agent: Agent, user_id: UUID, settings: Settings) -> ConversationReleaseSelection:
    """调用方先验证Agent使用权限并持有Agent锁；同主体/灰度ID分组稳定。"""
    if agent.active_version_id is None:
        raise ReleaseRolloutConflict("Agent没有活动版本")
    fallback = ConversationReleaseSelection(agent.active_version_id)
    row = await session.scalar(select(AgentReleaseRollout).where(AgentReleaseRollout.tenant_id == agent.tenant_id,
        AgentReleaseRollout.agent_id == agent.id, AgentReleaseRollout.status == "active"))
    if row is None or row.baseline_version_id != agent.active_version_id:
        return fallback
    try:
        validate_rollout_definition(row)
    except ReleaseRolloutConflict:
        return fallback
    if (not _approved(row, settings) or not _supervisor_fresh(row, settings, utc_now())
        or row.subject_ids and str(user_id) not in row.subject_ids):
        return fallback
    bucket = int.from_bytes(sha256(f"{agent.tenant_id}:{agent.id}:{row.id}:{user_id}".encode()).digest()[:8], "big") % 10000
    selection = "candidate" if bucket < row.candidate_bps else "baseline"
    try:
        baseline, candidate = await _readiness(session, rollout=row, settings=settings, lock=True)
    except ReleaseRolloutConflict:
        return fallback
    # 灰度在任何环境都不沿用开发豁免；候选失效只关闭候选新流量。
    if baseline.status != "passed" or candidate.status != "passed":
        return fallback
    return ConversationReleaseSelection(row.candidate_version_id if selection == "candidate" else row.baseline_version_id,
        row.id, row.epoch, selection, bucket)


async def scan_release_rollouts(session_factory: async_sessionmaker[AsyncSession], *, settings: Settings) -> int:
    async with session_factory() as session:
        ids = (await session.execute(select(AgentReleaseRollout.id, AgentReleaseRollout.agent_id, AgentReleaseRollout.tenant_id)
            .where(AgentReleaseRollout.status == "active")
            .order_by(AgentReleaseRollout.last_checked_at.asc().nullsfirst(), AgentReleaseRollout.id)
            .limit(settings.release_rollout_scan_batch_size))).all()
    scanned = 0
    for rollout_id, agent_id, tenant_id in ids:
        async with session_factory() as session, session.begin():
            agent = await session.scalar(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id).with_for_update(skip_locked=True))
            if agent is None:
                continue
            row = await session.scalar(select(AgentReleaseRollout).where(AgentReleaseRollout.id == rollout_id,
                AgentReleaseRollout.tenant_id == tenant_id, AgentReleaseRollout.status == "active").with_for_update(skip_locked=True))
            if row is None:
                continue
            reason = None
            try:
                policy = validate_rollout_definition(row)
                if agent.status != AgentStatus.ACTIVE or agent.active_version_id != row.baseline_version_id:
                    reason = "ROLLOUT_BASELINE_CHANGED"
                elif not _approved(row, settings):
                    reason = "ROLLOUT_POLICY_UNAPPROVED"
                else:
                    baseline, candidate = await _readiness(session, rollout=row, settings=settings, lock=True)
                    if baseline.status != "passed" or candidate.status != "passed":
                        reason = "ROLLOUT_RELEASE_GATE_BLOCKED"
                    else:
                        observation = await observe_release_rollout(session, rollout=row, policy=policy)
                        if observation.pause_recommended:
                            # 记录触发暂停的固定检查码，不复制正文或把系统决定伪装成人工。
                            blocking = next((check for check in observation.checks if check.status == "failed"), None)
                            blocking = blocking or next(check for check in observation.checks if check.status == "missing")
                            reason = "ROLLOUT_" + blocking.code
            except ReleaseRolloutConflict:
                reason = "ROLLOUT_DEFINITION_INVALID"
            row.last_checked_at = utc_now()
            if reason:
                row.status, row.reason_code, row.updated_at = "paused", reason, utc_now()
                row.revision += 1
                _append_event(session, row, event_type="paused", actor_user_id=None)
            scanned += 1
    return scanned


async def list_release_rollouts(session: AsyncSession, *, tenant_id: UUID, agent_id: UUID, offset: int, page_size: int) -> tuple[list[ReleaseRolloutSummary], int]:
    await get_agent(session, agent_id=agent_id, tenant_id=tenant_id)
    scope = (AgentReleaseRollout.tenant_id == tenant_id, AgentReleaseRollout.agent_id == agent_id)
    total = int(await session.scalar(select(func.count()).select_from(AgentReleaseRollout).where(*scope)) or 0)
    rows = (await session.scalars(select(AgentReleaseRollout).where(*scope)
        .order_by(AgentReleaseRollout.created_at.desc(), AgentReleaseRollout.id.desc()).offset(offset).limit(page_size))).all()
    return [rollout_summary(row) for row in rows], total


async def list_release_rollout_events(session: AsyncSession, *, rollout: AgentReleaseRollout, offset: int, page_size: int) -> tuple[list[ReleaseRolloutEventSummary], int]:
    scope = (AgentReleaseRolloutEvent.tenant_id == rollout.tenant_id, AgentReleaseRolloutEvent.rollout_id == rollout.id)
    total = int(await session.scalar(select(func.count()).select_from(AgentReleaseRolloutEvent).where(*scope)) or 0)
    rows = (await session.scalars(select(AgentReleaseRolloutEvent).where(*scope)
        .order_by(AgentReleaseRolloutEvent.created_at.desc(), AgentReleaseRolloutEvent.id.desc()).offset(offset).limit(page_size))).all()
    return [ReleaseRolloutEventSummary.model_validate({name: getattr(row, name) for name in ReleaseRolloutEventSummary.model_fields}) for row in rows], total
