"""从可信 Run/主体/资源派生参数并原子冻结提案；不执行外部动作。"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.action_contracts import MockTicketParameters, frozen_parameters_digest
from supportops_core.action_ledger_services import ActionStateConflict, business_logical_key
from supportops_core.agent_config import parse_agent_config
from supportops_core.auth import IdentityContext
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot
from supportops_core.diagnostic_mock_services import MOCK_FIXTURE_REQUESTS
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.models import AgentAuditEvent, AgentRun, AgentVersion, BusinessAction, Conversation, Message, RunAttempt, RunEvent, utc_now
from supportops_core.enums import MessageRole, RunStatus
from supportops_core.action_ledger_services import lock_business_action, transition_business_action
from supportops_core.resource_policy import TrustedResource, authorize_resource
from supportops_core.run_attempt_services import current_run_identity, lock_run_scope
from supportops_core.services import ResourceNotFoundError, append_event
from supportops_core.tool_policy import ResolvedToolPolicy, resolve_tool_policy


class ActionFeatureUnavailable(RuntimeError):
    code = "ACTION_CONNECTOR_UNAVAILABLE"


async def _link_proposal(session: AsyncSession, *, run: AgentRun, action: BusinessAction) -> None:
    """Run 锁下幂等关联新上下文，不伪造 SDK 调用、不覆盖原动作出处。"""
    linked = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == run.tenant_id, RunEvent.run_id == run.id,
        RunEvent.event_type == "action.proposed", RunEvent.data["action_id"].as_string() == str(action.id)).limit(1))
    if linked is None:
        await append_event(session, run_id=run.id, event_type="action.proposed", attempt_id=run.current_attempt_id,
            data={"action_id": str(action.id), "logical_key": action.logical_key, "status": action.status, "risk_level": action.risk_level, "data_environment": "mock"})
    from supportops_core.escalation_services import ensure_escalation_package
    actor = await session.scalar(select(Message.user_id).where(Message.id == run.input_message_id, Message.tenant_id == run.tenant_id))
    if actor != action.proposed_by:
        raise ActionStateConflict("升级包来源主体与原动作不一致")
    from supportops_core.approval_services import live_identity
    identity = await live_identity(session, tenant_id=run.tenant_id, user_id=action.proposed_by)
    from supportops_core.ticket_takeover_services import ensure_mock_case
    await ensure_mock_case(session, tenant_id=run.tenant_id, project_id=UUID(action.resource_id), owner_user_id=action.proposed_by,
        issue_key=MockTicketParameters.model_validate(action.parameters).issue_key, conversation_id=run.conversation_id)
    await ensure_escalation_package(session, action=action, run=run, identity=identity)


async def authorized_action_source(session: AsyncSession, *, run_id: UUID, identity: IdentityContext,
    lock: bool = True,
) -> tuple[AgentRun, AgentVersion, ResolvedToolPolicy, UUID]:
    if lock:
        conversation, run = await lock_run_scope(session, run_id, tenant_id=identity.principal.tenant_id)
    else:
        run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.tenant_id == identity.principal.tenant_id))
        conversation = await session.scalar(select(Conversation).where(Conversation.id == run.conversation_id,
            Conversation.tenant_id == identity.principal.tenant_id)) if run is not None else None
        if run is None or conversation is None:
            raise ResourceNotFoundError("业务动作不存在")
    if conversation.user_id != identity.user.id:
        raise ResourceNotFoundError("业务动作不存在")
    current = await current_run_identity(session, run=run, conversation=conversation)
    version = await session.scalar(select(AgentVersion).where(AgentVersion.id == run.agent_version_id,
        AgentVersion.tenant_id == run.tenant_id, AgentVersion.agent_id == run.agent_id))
    if version is None:
        raise ResourceNotFoundError("固定 Agent 版本不存在")
    policy = await resolve_tool_policy(session, identity=current, config=parse_agent_config(version.config),
        allowed_tool_ids=version.resolved_tool_ids, implementation_versions=version.resolved_tool_versions,
        tool_id="create_mock_ticket", arguments={})
    if not policy.action_proposal_only:
        raise ActionStateConflict("当前工具不是服务端获准的提案入口")
    project_id = uuid5(NAMESPACE_URL, f"supportops:mock-project:{run.tenant_id}:{current.user.id}")
    authorize_resource(current, TrustedResource(kind="project", tenant_id=run.tenant_id, resource_id=str(project_id),
        owner_user_id=current.user.id, visible_fields=frozenset({"ticket_proposal"}), allowed_actions=frozenset({"propose"})),
        action="propose", fields=frozenset({"ticket_proposal"}))
    return run, version, policy, project_id


async def propose_mock_ticket(session: AsyncSession, *, run_id: UUID, identity: IdentityContext,
    settings: Settings, correlation_id: str,
) -> BusinessAction:
    if not settings.business_action_mock_enabled or settings.environment == "production":
        raise ActionFeatureUnavailable("隔离动作 Mock 未启用或当前为 production，不能提出模拟建单")
    run, version, policy, project_id = await authorized_action_source(session, run_id=run_id, identity=identity)
    message = await session.scalar(select(Message).where(Message.id == run.input_message_id,
        Message.tenant_id == run.tenant_id, Message.conversation_id == run.conversation_id, Message.user_id == identity.user.id,
        Message.role == MessageRole.USER, Message.turn_sequence == run.input_sequence))
    if message is None:
        raise ResourceNotFoundError("业务输入不存在")
    # 缺少请求标识时以当前支持会话作为排查意图，不让模型用新消息生成新键。
    issue_key = "conversation:" + str(run.conversation_id)
    if run.current_attempt_id is not None:
        attempt = await session.scalar(select(RunAttempt).where(RunAttempt.id == run.current_attempt_id,
            RunAttempt.tenant_id == run.tenant_id, RunAttempt.run_id == run.id))
        context = ContextSnapshot.model_validate(attempt.context_snapshot) if attempt is not None and attempt.context_snapshot else None
        if context is not None and context.diagnostic is not None and context.diagnostic.request_id in MOCK_FIXTURE_REQUESTS:
            issue_key = "request:" + context.diagnostic.request_id
    # 时间、Run、检索 ID 只留来源关联，不进入业务键/参数，避免恢复重新建单。
    parameters = MockTicketParameters(title="模拟支持问题接管", summary="隔离模拟支持问题，需人工复核；未确认真实生产原因。", issue_key=issue_key)
    digest = frozen_parameters_digest(parameters)
    key = business_logical_key(tenant_id=run.tenant_id, user_id=identity.user.id, tool_id="create_mock_ticket",
        resource_kind="mock_project", resource_id=str(project_id), issue_key=issue_key)
    lookup = select(BusinessAction).where(BusinessAction.tenant_id == run.tenant_id,
        BusinessAction.tool_id == "create_mock_ticket", BusinessAction.logical_key == key).with_for_update()
    existing = await session.scalar(lookup)
    if existing is not None:
        if (existing.parameters_digest != digest or existing.proposed_by != identity.user.id
            or existing.implementation_version != policy.implementation_version or existing.resource_id != str(project_id)):
            raise ActionStateConflict("同一业务键的冻结参数、版本或资源不同，原动作不会被覆盖")
        await _link_proposal(session, run=run, action=existing)
        return existing
    action = BusinessAction(tenant_id=run.tenant_id, proposed_by=identity.user.id, run_id=run.id,
        agent_version_id=version.id, tool_id="create_mock_ticket", implementation_version=policy.implementation_version,
        catalog_version=policy.catalog_version, logical_key=key, parameters=parameters.model_dump(mode="json"),
        parameters_digest=digest, parameter_schema_version="mock_ticket_v1", resource_kind="mock_project", resource_id=str(project_id),
        risk_level=f"R{max(2, policy.risk_number)}", approval_required=policy.approval_required, status="proposed")
    action.reconciliation_limit = settings.action_max_reconciliations
    try:
        async with session.begin_nested():
            session.add(action)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(lookup)
        if existing is None:
            raise
        if existing.parameters_digest != digest or existing.proposed_by != identity.user.id or existing.implementation_version != policy.implementation_version:
            raise ActionStateConflict("并发复用的业务键参数或版本不同")
        await _link_proposal(session, run=run, action=existing)
        return existing
    session.add(AgentAuditEvent(tenant_id=run.tenant_id, actor_user_id=identity.user.id,
        resource_type=AuditResourceType.TOOL, action=AuditAction.BUSINESS_ACTION_PROPOSED,
        correlation_id=correlation_id, version_id=version.id, metadata_payload={"action_id": str(action.id),
            "tool_id": action.tool_id, "parameters_digest": digest, "risk_level": action.risk_level,
            "implementation_version": action.implementation_version, "catalog_version": action.catalog_version,
            "status": "proposed", "data_environment": "mock"}))
    await _link_proposal(session, run=run, action=action)
    return action


async def resume_action_reconciliation(session: AsyncSession, *, action_id: UUID, identity: IdentityContext, settings: Settings,
    expected_generation: int, expected_reconciliation_count: int, context_run_id: UUID | None, correlation_id: str,
) -> BusinessAction:
    """人工只追加一次有限查询许可，不写入人工猜测的成功/失败回执。"""
    if not settings.business_action_mock_enabled or settings.environment == "production":
        raise ActionFeatureUnavailable("隔离动作 Mock 未启用，不能恢复查询")
    action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    run, version, _, _ = await authorized_action_source(session,
        run_id=context_run_id or action.execution_run_id or action.run_id, identity=identity)
    action = await lock_business_action(session, action_id=action.id, tenant_id=action.tenant_id)
    if action.status != "needs_attention" or action.generation != expected_generation or action.reconciliation_count != expected_reconciliation_count:
        raise ActionStateConflict("动作状态或累计对账次数已变化，请刷新后重新确认")
    if action.reconciliation_count >= settings.action_max_manual_reconciliations:
        raise ActionStateConflict("累计人工对账许可已耗尽，需要保留未知状态并联系管理员处理")
    if run.status != RunStatus.COMPLETED:
        raise ActionStateConflict("恢复查询需要有效完成的授权上下文")
    action.reconciliation_limit = max(action.reconciliation_limit, action.reconciliation_count + 1)
    action.execution_run_id, action.execution_agent_version_id = run.id, version.id
    action.status, action.error_code = "unknown", "ACTION_MANUAL_RECONCILIATION_REQUESTED"
    action.next_attempt_at = utc_now()
    await append_event(session, run_id=run.id, event_type="action.reconciliation_requested",
        data={"action_id": str(action.id), "status": "unknown", "reconciliation_count": action.reconciliation_count})
    return action


async def get_action_for_user(session: AsyncSession, *, action_id: UUID, identity: IdentityContext,
    context_run_id: UUID | None = None,
) -> BusinessAction:
    action = await session.scalar(select(BusinessAction).where(BusinessAction.id == action_id,
        BusinessAction.tenant_id == identity.principal.tenant_id, BusinessAction.proposed_by == identity.user.id))
    if action is None:
        raise ResourceNotFoundError("业务动作不存在")
    source_id = context_run_id or action.execution_run_id or action.run_id
    if source_id != action.run_id:
        linked = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == action.tenant_id, RunEvent.run_id == source_id,
            RunEvent.event_type == "action.proposed", RunEvent.data["action_id"].as_string() == str(action.id)).limit(1))
        if linked is None:
            raise ResourceNotFoundError("业务动作不存在")
    await authorized_action_source(session, run_id=source_id, identity=identity, lock=False)
    return action


async def request_action_execution(session: AsyncSession, *, action_id: UUID, identity: IdentityContext, settings: Settings,
    expected_generation: int, expected_parameters_digest: str, expected_catalog_version: int, correlation_id: str,
    context_run_id: UUID | None = None,
) -> BusinessAction:
    """先持久化有授权的执行意图，当前函数不产生外部副作用。"""
    if not settings.business_action_mock_enabled or settings.environment == "production":
        raise ActionFeatureUnavailable("隔离动作 Mock 未启用，不能提交执行意图")
    action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    source_id = context_run_id or action.execution_run_id or action.run_id
    run, version, policy, project_id = await authorized_action_source(session, run_id=source_id, identity=identity)
    action = await lock_business_action(session, action_id=action.id, tenant_id=identity.principal.tenant_id)
    if (action.parameters_digest != expected_parameters_digest or action.generation != expected_generation
        or policy.catalog_version != expected_catalog_version or policy.implementation_version != action.implementation_version):
        raise ActionStateConflict("动作参数、实现、generation 或当前策略已变化，请刷新后重新确认")
    if policy.approval_required or policy.risk_number >= 3 or action.approval_required or action.risk_level != "R2":
        raise ActionStateConflict("动作需指定非本人审批者批准，不能直接执行")
    from supportops_core.approval_services import requires_action_approval
    if await requires_action_approval(session, action, policy):
        raise ActionStateConflict("该动作已申请独立审批，不能通过直接执行绕过")
    if run.status != RunStatus.COMPLETED:
        raise ActionStateConflict("原始 Run 尚未有效完成，取消/失败的运行不能自动继续写动作")
    if action.status in {"queued", "running", "succeeded", "unknown", "reconciling", "needs_attention"}:
        return action
    from supportops_core.ticket_takeover_services import assert_mock_write_allowed
    await assert_mock_write_allowed(session, tenant_id=action.tenant_id, project_id=project_id, owner_user_id=action.proposed_by,
        issue_key=MockTicketParameters.model_validate(action.parameters).issue_key, conversation_id=run.conversation_id)
    action = await transition_business_action(session, action_id=action.id, tenant_id=action.tenant_id,
        expected_generation=expected_generation, target="queued")
    action.execution_catalog_version = policy.catalog_version
    action.execution_run_id, action.execution_agent_version_id = run.id, version.id
    session.add(AgentAuditEvent(tenant_id=action.tenant_id, actor_user_id=identity.user.id,
        resource_type=AuditResourceType.TOOL, action=AuditAction.BUSINESS_ACTION_QUEUED,
        correlation_id=correlation_id, version_id=action.agent_version_id,
        metadata_payload={"action_id": str(action.id), "parameters_digest": action.parameters_digest,
            "catalog_version": policy.catalog_version, "status": "queued", "data_environment": "mock"}))
    await append_event(session, run_id=run.id, event_type="action.queued", data={"action_id": str(action.id), "status": "queued", "data_environment": "mock"})
    return action
