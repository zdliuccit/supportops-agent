"""动作独立租约：先记意图，外部提交，独立查询，未知先对账。"""

from __future__ import annotations

import hashlib
import json
import re
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.action_contracts import MockTicketParameters, frozen_parameters_digest
from supportops_core.action_ledger_services import ActionStateConflict, assert_action_release_allowed, transition_business_action
from supportops_core.action_proposal_services import authorized_action_source
from supportops_core.approval_services import (
    consume_action_approval, requires_action_approval, validate_approved_action, validate_consumed_approval,
    settle_waiting_run,
)
from supportops_core.agent_config import parse_agent_config
from supportops_core.auth import IdentityContext, Principal
from supportops_core.config import Settings
from supportops_core.enums import RunStatus
from supportops_core.mock_action_connector import MockTargetError, MockTargetProof, query_mock_ticket_proof, submit_mock_ticket
from supportops_core.models import ActionAttempt, ActionReceipt, AgentRun, BusinessAction, Conversation, Message, RunAttempt, Tenant, ToolInvocation, User
from supportops_core.quota_services import quota_day_start
from supportops_core.run_attempt_services import RuntimeAuthorizationDenied, RunCancellationRequested
from supportops_core.services import ResourceNotFoundError, append_event
from supportops_core.tool_observation_services import count_tool_invocations, record_tool_observation
from supportops_core.tool_policy import ToolPolicyError
from supportops_core.ticket_takeover_services import assert_mock_write_allowed

logger = structlog.get_logger()
_MODE = re.compile(r"\[action:(lost|timeout|not_executed|unknown|conflict)\]", re.IGNORECASE)


class ActionLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ActionPermit:
    action_id: UUID
    attempt_id: UUID
    invocation_id: UUID
    tenant_id: UUID
    run_id: UUID
    owner_user_id: UUID
    project_id: UUID
    owner_id: str
    generation: int
    kind: str
    logical_key: str
    parameters: MockTicketParameters
    mode: str
    conversation_id: UUID


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def _actor(session: AsyncSession, action: BusinessAction) -> IdentityContext:
    user = await session.scalar(select(User).where(User.id == action.proposed_by, User.tenant_id == action.tenant_id,
        User.status == "active").execution_options(populate_existing=True))
    if user is None:
        raise RuntimeAuthorizationDenied("动作主体已失效")
    return IdentityContext(user=user, principal=Principal(user_id=user.id, tenant_id=user.tenant_id,
        roles=tuple(user.roles), display_name=user.display_name))


async def _journal_parent(session: AsyncSession, *, run_id: UUID, tenant_id: UUID) -> AgentRun:
    """系统日志保持会话→Run→动作顺序，已删除会话也保留副作用事实。"""
    row = await session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id))
    if row is None:
        raise ResourceNotFoundError("动作来源不存在")
    await session.scalar(select(Conversation).where(Conversation.id == row.conversation_id, Conversation.tenant_id == tenant_id)
        .execution_options(include_deleted=True).with_for_update(key_share=True))
    return await session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == tenant_id)
        .execution_options(populate_existing=True).with_for_update())


async def _fenced(session: AsyncSession, permit: ActionPermit) -> tuple[AgentRun, BusinessAction, ActionAttempt, ToolInvocation]:
    run = await _journal_parent(session, run_id=permit.run_id, tenant_id=permit.tenant_id)
    action = await session.scalar(select(BusinessAction).where(BusinessAction.id == permit.action_id,
        BusinessAction.tenant_id == permit.tenant_id).execution_options(populate_existing=True).with_for_update())
    attempt = await session.scalar(select(ActionAttempt).where(ActionAttempt.id == permit.attempt_id,
        ActionAttempt.tenant_id == permit.tenant_id, ActionAttempt.action_id == permit.action_id).with_for_update())
    invocation = await session.scalar(select(ToolInvocation).where(ToolInvocation.id == permit.invocation_id,
        ToolInvocation.tenant_id == permit.tenant_id, ToolInvocation.business_action_id == permit.action_id).with_for_update())
    if (action is None or attempt is None or invocation is None or action.current_attempt_id != attempt.id
        or action.generation != permit.generation or attempt.owner_id != permit.owner_id or attempt.status != "running"
        or invocation.status != "running" or _aware(attempt.lease_expires_at) <= datetime.now(UTC)
        or action.parameters_digest != attempt.parameters_digest):
        raise ActionLeaseLost("动作 owner/generation/租约已失效，旧执行者不能写回执")
    return run, action, attempt, invocation


async def claim_action(session: AsyncSession, *, settings: Settings, owner_id: str) -> ActionPermit | None:
    if not settings.business_action_mock_enabled or settings.environment == "production":
        return None
    live = exists(select(ActionAttempt.id).where(ActionAttempt.id == BusinessAction.current_attempt_id,
        ActionAttempt.status == "running", ActionAttempt.lease_expires_at > datetime.now(UTC)))
    candidate = await session.scalar(select(BusinessAction).where(BusinessAction.status.in_(("queued", "unknown", "reconciling")),
        BusinessAction.next_attempt_at <= datetime.now(UTC), ~live).order_by(BusinessAction.next_attempt_at, BusinessAction.created_at, BusinessAction.id).limit(1))
    if candidate is None:
        return None
    run_id = candidate.execution_run_id or candidate.run_id
    await _journal_parent(session, run_id=run_id, tenant_id=candidate.tenant_id)
    action = await session.scalar(select(BusinessAction).where(BusinessAction.id == candidate.id).execution_options(populate_existing=True).with_for_update(skip_locked=True))
    if action is None or action.status not in {"queued", "unknown", "reconciling"} or (action.execution_run_id or action.run_id) != run_id:
        return None
    current_attempt = await session.get(ActionAttempt, action.current_attempt_id) if action.current_attempt_id else None
    if current_attempt is not None and current_attempt.status == "running" and _aware(current_attempt.lease_expires_at) > datetime.now(UTC):
        return None
    kind = "execute" if action.status == "queued" else "verify" if action.error_code == "ACTION_VERIFICATION_PENDING" else "reconcile"
    try:
        identity = await _actor(session, action)
        run, version, policy, project_id = await authorized_action_source(session, run_id=run_id, identity=identity)
        if action.resource_kind != "mock_project" or action.resource_id != str(project_id):
            raise ActionStateConflict("动作资源与当前主体项目不一致")
        if run.status not in {RunStatus.COMPLETED, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION} or run.current_attempt_id is None:
            raise ActionStateConflict("动作来源未完成或已取消，不能执行")
        if action.tool_id != "create_mock_ticket" or action.parameter_schema_version != "mock_ticket_v1":
            raise ActionStateConflict("动作实现尚未接通")
        parameters = MockTicketParameters.model_validate(action.parameters)
        if kind == "execute":
            await assert_action_release_allowed(session, tenant_id=action.tenant_id, version_ids={version.id, action.agent_version_id})
            await assert_mock_write_allowed(session, tenant_id=action.tenant_id, project_id=project_id,
                owner_user_id=action.proposed_by, issue_key=parameters.issue_key, conversation_id=run.conversation_id)
        if frozen_parameters_digest(parameters) != action.parameters_digest or policy.implementation_version != action.implementation_version:
            raise ActionStateConflict("冻结参数或实现已变化")
        needs_approval = await requires_action_approval(session, action, policy)
        if kind == "execute" and (policy.catalog_version != action.execution_catalog_version or version.id != action.execution_agent_version_id):
            raise ActionStateConflict("当前写动作策略或审批条件不满足，不能继续执行")
        if kind == "execute" and needs_approval:
            await validate_approved_action(session, action=action, run=run, version=version, policy=policy)
        execute_count = int(await session.scalar(select(func.count()).select_from(ActionAttempt).where(
            ActionAttempt.action_id == action.id, ActionAttempt.tenant_id == action.tenant_id, ActionAttempt.kind == "execute")) or 0)
        if kind == "execute" and execute_count >= settings.action_max_executions:
            raise ActionStateConflict("逻辑动作执行次数已耗尽，恢复不清零")
        if kind != "execute" and action.reconciliation_count >= min(action.reconciliation_limit, settings.action_max_manual_reconciliations):
            raise ActionStateConflict("逻辑动作对账次数已耗尽，需要人工接管")
        await session.scalar(select(Tenant).where(Tenant.id == action.tenant_id).with_for_update(key_share=True))
        consumed = (ToolInvocation.tenant_id == action.tenant_id, ToolInvocation.run_id == run.id, ToolInvocation.status != "denied")
        used = int(await session.scalar(select(func.count()).select_from(ToolInvocation).where(*consumed)) or 0)
        per_tool = int(await session.scalar(select(func.count()).select_from(ToolInvocation).where(*consumed, ToolInvocation.tool_id == action.tool_id)) or 0)
        daily = int(await session.scalar(select(func.count()).select_from(ToolInvocation).where(ToolInvocation.tenant_id == action.tenant_id,
            ToolInvocation.status != "denied", ToolInvocation.created_at >= quota_day_start())) or 0)
        needed = 2 if kind == "execute" else 1
        config = parse_agent_config(version.config)
        if used + needed > config.runtime.tool_call_limit or per_tool + needed > policy.max_calls_per_run or daily + needed > settings.tenant_tool_limit_per_day:
            raise ToolPolicyError("TOOL_CALL_QUOTA_EXCEEDED", "动作需要执行和独立复核额度，当前剩余许可不足")
    except (RuntimeAuthorizationDenied, ToolPolicyError, ActionStateConflict, ResourceNotFoundError, RunCancellationRequested) as exc:
        action.error_code = getattr(exc, "code", "ACTION_EXECUTION_BLOCKED")
        if kind == "execute" and action.generation == 0 and getattr(exc, "code", None) == "APPROVAL_SCOPE_INVALID":
            action.status, action.error_code = "cancelled", "APPROVAL_INVALIDATED"
        else:
            action.status = "failed" if kind == "execute" else "needs_attention"
        action.finished_at = datetime.now(UTC) if kind == "execute" else None
        await append_event(session, run_id=run_id, event_type="action.blocked", data={"action_id": str(action.id), "status": action.status, "code": action.error_code})
        run = await session.get(AgentRun, run_id)
        if run is not None:
            await settle_waiting_run(session, run=run)
        return None
    generation, attempt_id, invocation_id = action.generation + 1, uuid4(), uuid4()
    expires = datetime.now(UTC) + timedelta(seconds=settings.action_lease_seconds)
    attempt = ActionAttempt(id=attempt_id, tenant_id=action.tenant_id, action_id=action.id, generation=generation,
        kind=kind, owner_id=owner_id, status="running", lease_expires_at=expires, parameters_digest=action.parameters_digest)
    invocation = ToolInvocation(id=invocation_id, tenant_id=action.tenant_id, run_id=run.id, attempt_id=run.current_attempt_id,
        tool_call_id=f"action:{action.id}:{generation}:{kind}", tool_id=action.tool_id,
        implementation_version=action.implementation_version, catalog_version=policy.catalog_version,
        risk_level=action.risk_level if kind == "execute" else "R1", arguments_digest=action.parameters_digest,
        status="running", business_action_id=action.id)
    session.add_all([attempt, invocation])
    await session.flush()
    if kind == "execute" and needs_approval:
        await consume_action_approval(session, action=action, run=run, version=version, policy=policy, attempt=attempt)
    action.generation, action.current_attempt_id = generation, attempt_id
    action.status = "running" if kind == "execute" else "reconciling"
    if kind != "execute":
        action.reconciliation_count += 1
    message = await session.get(Message, run.input_message_id)
    match = _MODE.search(message.content) if message is not None else None
    await append_event(session, run_id=run.id, event_type="action.started", data={"action_id": str(action.id), "generation": generation, "kind": kind, "status": action.status})
    return ActionPermit(action.id, attempt_id, invocation_id, action.tenant_id, run.id, action.proposed_by,
        project_id, owner_id, generation, kind, action.logical_key, parameters, match.group(1).lower() if match else "normal", run.conversation_id)


async def record_action_outcome(session_factory: async_sessionmaker[AsyncSession], *, permit: ActionPermit, settings: Settings,
    proof: MockTargetProof | None, code: str | None,
) -> None:
    async with session_factory() as session, session.begin():
        run, action, attempt, invocation = await _fenced(session, permit)
        now = datetime.now(UTC)
        receipt = None
        if proof is not None:
            if proof.logical_key != action.logical_key or proof.owner_user_id != action.proposed_by:
                raise ActionStateConflict("回执业务键或主体不匹配")
            fields = proof.model_dump(mode="json")
            digest = hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            receipt = ActionReceipt(tenant_id=action.tenant_id, action_id=action.id, attempt_id=attempt.id,
                connector_version=proof.connector_version, external_id=proof.external_id, status=proof.status,
                result_digest=digest, verified_fields=fields, observed_at=proof.observed_at)
            session.add(receipt)
            await session.flush()
            invocation.result_digest = digest
        if permit.kind == "execute":
            action.status, action.error_code = "unknown", "ACTION_VERIFICATION_PENDING" if proof is not None and proof.status == "submitted" else code or "ACTION_RESULT_UNKNOWN"
            attempt.status = "succeeded" if proof is not None and proof.status == "submitted" else "unknown"
            invocation.status = "succeeded" if attempt.status == "succeeded" else "unknown"
            action.next_attempt_at = now
        elif proof is not None and proof.status == "verified" and proof.parameters_digest == action.parameters_digest and receipt is not None:
            await transition_business_action(session, action_id=action.id, tenant_id=action.tenant_id,
                expected_generation=permit.generation, target="succeeded", receipt_id=receipt.id)
            action.error_code, attempt.status, invocation.status = None, "succeeded", "succeeded"
        elif proof is not None and proof.status == "not_executed" and proof.execution_fence >= permit.generation and receipt is not None:
            execute_count = int(await session.scalar(select(func.count()).select_from(ActionAttempt).where(ActionAttempt.action_id == action.id,
                ActionAttempt.tenant_id == action.tenant_id, ActionAttempt.kind == "execute")) or 0)
            from supportops_core.models import ApprovalConsumption
            approved_execution = await session.scalar(select(ApprovalConsumption.id).where(ApprovalConsumption.action_id == action.id))
            retry = execute_count < settings.action_max_executions and not action.approval_required and action.risk_level == "R2" and approved_execution is None
            await transition_business_action(session, action_id=action.id, tenant_id=action.tenant_id,
                expected_generation=permit.generation, target="queued" if retry else "failed", receipt_id=receipt.id)
            action.error_code, attempt.status, invocation.status = "ACTION_PROVEN_NOT_EXECUTED", "succeeded", "succeeded"
            action.next_attempt_at = now + timedelta(seconds=2)
        else:
            action.status = "needs_attention" if proof is not None and proof.status == "conflict" or action.reconciliation_count >= action.reconciliation_limit else "unknown"
            action.error_code = "ACTION_VERIFICATION_CONFLICT" if proof is not None and proof.status == "conflict" else code or "ACTION_RESULT_UNKNOWN"
            action.next_attempt_at = now + timedelta(seconds=min(60, 2 ** action.reconciliation_count))
            attempt.status = "failed" if proof is not None and proof.status == "conflict" else "unknown"
            invocation.status = "failed" if attempt.status == "failed" else "unknown"
        attempt.error_code, invocation.error_code = action.error_code, action.error_code
        attempt.finished_at = invocation.finished_at = now
        attempt.lease_expires_at = now
        run.tool_call_count = await count_tool_invocations(session, tenant_id=run.tenant_id, run_id=run.id)
        await append_event(session, run_id=run.id, event_type="action.updated", data={"action_id": str(action.id), "status": action.status,
            "generation": action.generation, "code": action.error_code, "data_environment": "mock",
            "action_attempt_id": str(attempt.id), "receipt_id": str(receipt.id) if receipt is not None else None,
            "receipt_status": receipt.status if receipt is not None else "unknown", "actor_id": str(action.proposed_by),
            "parameters_digest": action.parameters_digest, "implementation_version": action.implementation_version,
            "catalog_version": invocation.catalog_version})
        await settle_waiting_run(session, run=run)
    try:
        await record_tool_observation(session_factory, invocation_id=permit.invocation_id, tenant_id=permit.tenant_id)
    except Exception as exc:
        logger.warning("action_observation_unavailable", action_id=str(permit.action_id), error_type=type(exc).__name__)


async def execute_action(session_factory: async_sessionmaker[AsyncSession], *, permit: ActionPermit, settings: Settings) -> None:
    # 目标操作本身是有界本地事务；没有第三方网络或凭据。超时不宣称未执行。
    proof = None
    code = None
    try:
        async with session_factory() as session, session.begin():
            _, action, _, _ = await _fenced(session, permit)
            identity = await _actor(session, action)
            run, version, policy, project_id = await authorized_action_source(session, run_id=permit.run_id, identity=identity)
            if run.status not in {RunStatus.COMPLETED, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION} or project_id != permit.project_id or frozen_parameters_digest(permit.parameters) != action.parameters_digest:
                raise ActionStateConflict("动作出站前主体、资源或冻结参数已变化")
            if permit.kind == "execute" and (policy.catalog_version != action.execution_catalog_version or version.id != action.execution_agent_version_id
                or policy.implementation_version != action.implementation_version):
                raise ActionStateConflict("动作出站前策略已变化，不再满足执行条件")
            if permit.kind == "execute" and await requires_action_approval(session, action, policy):
                await validate_consumed_approval(session, action=action, run=run, version=version, policy=policy,
                    attempt_id=permit.attempt_id, generation=permit.generation)
            if permit.kind == "execute":
                await assert_action_release_allowed(session, tenant_id=action.tenant_id, version_ids={version.id, action.agent_version_id})
                await assert_mock_write_allowed(session, tenant_id=permit.tenant_id, project_id=permit.project_id,
                    owner_user_id=permit.owner_user_id, issue_key=permit.parameters.issue_key, conversation_id=permit.conversation_id)
        async with asyncio.timeout(settings.action_target_timeout_seconds):
            if permit.kind == "execute":
                proof = await submit_mock_ticket(session_factory, settings=settings, tenant_id=permit.tenant_id,
                    owner_user_id=permit.owner_user_id, project_id=permit.project_id, logical_key=permit.logical_key,
                    generation=permit.generation, parameters=permit.parameters, mode=permit.mode, conversation_id=permit.conversation_id,
                    action_id=permit.action_id, agent_version_id=version.id)
            else:
                proof = await query_mock_ticket_proof(session_factory, settings=settings, tenant_id=permit.tenant_id,
                    owner_user_id=permit.owner_user_id, logical_key=permit.logical_key, generation=permit.generation, parameters=permit.parameters)
    except MockTargetError as exc:
        code = exc.code
    except (RuntimeAuthorizationDenied, ToolPolicyError, ActionStateConflict, ResourceNotFoundError, RunCancellationRequested) as exc:
        code = getattr(exc, "code", "ACTION_EXECUTION_BLOCKED")
    except ActionLeaseLost:
        return
    except TimeoutError:
        code = "ACTION_DEPENDENCY_TIMEOUT"
    except Exception as exc:
        code = "ACTION_DEPENDENCY_UNAVAILABLE"
        logger.warning("action_dependency_unavailable", action_id=str(permit.action_id), error_type=type(exc).__name__)
    try:
        await record_action_outcome(session_factory, permit=permit, settings=settings, proof=proof, code=code)
    except ActionLeaseLost:
        # 目标可能已提交，当前 generation 负责查询证明；旧 owner 不能补写成功。
        return


async def recover_expired_actions(session: AsyncSession) -> int:
    candidate = await session.scalar(select(BusinessAction).join(ActionAttempt, BusinessAction.current_attempt_id == ActionAttempt.id).where(
        BusinessAction.status.in_(("running", "reconciling")), ActionAttempt.status == "running", ActionAttempt.lease_expires_at <= datetime.now(UTC))
        .order_by(ActionAttempt.lease_expires_at, BusinessAction.id).limit(1))
    if candidate is None:
        return 0
    run = await _journal_parent(session, run_id=candidate.execution_run_id or candidate.run_id, tenant_id=candidate.tenant_id)
    action = await session.scalar(select(BusinessAction).where(BusinessAction.id == candidate.id).execution_options(populate_existing=True).with_for_update())
    attempt = await session.scalar(select(ActionAttempt).where(ActionAttempt.id == action.current_attempt_id).with_for_update())
    if attempt is None or attempt.status != "running" or _aware(attempt.lease_expires_at) > datetime.now(UTC):
        return 0
    now = datetime.now(UTC)
    attempt.status, attempt.error_code, attempt.finished_at = "unknown", "ACTION_LEASE_EXPIRED", now
    invocations = (await session.scalars(select(ToolInvocation).where(ToolInvocation.tenant_id == action.tenant_id,
        ToolInvocation.business_action_id == action.id, ToolInvocation.tool_call_id == f"action:{action.id}:{action.generation}:{attempt.kind}",
        ToolInvocation.status == "running"))).all()
    for invocation in invocations:
        invocation.status, invocation.error_code, invocation.finished_at = "unknown", "ACTION_LEASE_EXPIRED", now
    action.status, action.error_code, action.next_attempt_at = "unknown", "ACTION_LEASE_EXPIRED", now
    await append_event(session, run_id=run.id, event_type="action.recovering", data={"action_id": str(action.id), "status": "unknown", "code": action.error_code})
    return 1
