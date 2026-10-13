"""显式 Mock 审批；审批者身份仅用于决定，执行仍使用原申请者授权。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.action_contracts import MockTicketParameters, frozen_parameters_digest
from supportops_core.redaction import redact_sensitive_text
from supportops_core.action_ledger_services import ActionStateConflict, lock_business_action, transition_business_action
from supportops_core.action_proposal_services import ActionFeatureUnavailable, authorized_action_source, get_action_for_user
from supportops_core.auth import IdentityContext, Principal
from supportops_core.config import Settings
from supportops_core.enums import AuditAction, AuditResourceType, RunStatus
from supportops_core.models import (
    ActionAttempt, AgentAuditEvent, AgentRun, AgentVersion, ApprovalConsumption, ApprovalDecision,
    ApprovalRequest, BusinessAction, MockResourceGrant, ModelCredential, ModelCredentialRevision, ModelEndpointVersion, Tenant, User, utc_now,
)
from supportops_core.services import ResourceNotFoundError, append_event, transition_run
from supportops_core.run_attempt_services import RuntimeAuthorizationDenied, RunCancellationRequested, lock_run_scope
from supportops_core.tool_policy import ResolvedToolPolicy, ToolPolicyError
from supportops_core.ticket_takeover_services import assert_mock_write_allowed, ensure_mock_case

STAFF_ROLES = frozenset({"platform_admin", "employee", "agent_user"})


class ApprovalScopeInvalid(ActionStateConflict):
    code = "APPROVAL_SCOPE_INVALID"


def _safe_reason(reason: str) -> str:
    return redact_sensitive_text(reason)[:1000]


def mock_project_id(tenant_id: UUID, owner_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"supportops:mock-project:{tenant_id}:{owner_id}")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def require_mock(settings: Settings) -> None:
    if not settings.business_action_mock_enabled or settings.environment == "production":
        raise ActionFeatureUnavailable("隔离 Mock 未启用，审批不能连接真实副作用")


async def live_identity(session: AsyncSession, *, tenant_id: UUID, user_id: UUID) -> IdentityContext:
    user = await session.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id,
        User.status == "active").execution_options(populate_existing=True))
    tenant = await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id, Tenant.status == "active"))
    if user is None or tenant is None:
        raise ToolPolicyError("MOCK_RESOURCE_ACCESS_DENIED", "主体或租户已失效")
    return IdentityContext(user=user, principal=Principal(user_id=user.id, tenant_id=user.tenant_id,
        roles=tuple(user.roles), display_name=user.display_name))


async def require_mock_staff(session: AsyncSession, *, tenant_id: UUID, owner_user_id: UUID,
    user_id: UUID, permission: str, lock: bool = False,
) -> MockResourceGrant:
    identity = await live_identity(session, tenant_id=tenant_id, user_id=user_id)
    if not STAFF_ROLES.intersection(identity.principal.roles):
        raise ToolPolicyError("MOCK_RESOURCE_ACCESS_DENIED", "当前角色不能处理该 Mock 资源")
    query = select(MockResourceGrant).where(MockResourceGrant.tenant_id == tenant_id,
        MockResourceGrant.project_id == mock_project_id(tenant_id, owner_user_id),
        MockResourceGrant.owner_user_id == owner_user_id, MockResourceGrant.user_id == user_id,
        MockResourceGrant.permission == permission, MockResourceGrant.is_enabled.is_(True)).execution_options(populate_existing=True)
    grant = await session.scalar(query.with_for_update(key_share=True) if lock else query)
    if grant is None:
        raise ToolPolicyError("MOCK_RESOURCE_ACCESS_DENIED", "未获得该 Mock 项目的显式人员授权")
    return grant


def approval_visibility(identity: IdentityContext):
    """SQL 中先过滤资源权限，再分页；管理员没有隐式业务详情权限。"""
    assigned = (ApprovalRequest.approver_id == identity.user.id) & (ApprovalRequest.applicant_id != identity.user.id)
    grant = exists(select(MockResourceGrant.id).where(MockResourceGrant.tenant_id == ApprovalRequest.tenant_id,
        MockResourceGrant.owner_user_id == ApprovalRequest.applicant_id, MockResourceGrant.user_id == identity.user.id,
        MockResourceGrant.permission == "approve", MockResourceGrant.is_enabled.is_(True)))
    return (ApprovalRequest.tenant_id == identity.principal.tenant_id) & (
        (ApprovalRequest.applicant_id == identity.user.id) | (assigned & grant if STAFF_ROLES.intersection(identity.principal.roles) else False))


def _scope(action: BusinessAction, run: AgentRun, version: AgentVersion, policy: ResolvedToolPolicy, resource_revision: int | None) -> str:
    data = {"tenant_id": str(action.tenant_id), "applicant_id": str(action.proposed_by), "action_id": str(action.id),
        "logical_key": action.logical_key, "run_id": str(run.id), "resource_kind": action.resource_kind, "resource_id": action.resource_id,
        "parameters_digest": action.parameters_digest, "schema": action.parameter_schema_version,
        "tool_id": action.tool_id, "implementation_version": policy.implementation_version, "catalog_version": policy.catalog_version,
        "agent_version_id": str(version.id), "config_digest": run.config_digest,
        "model_version_id": str(run.model_endpoint_version_id), "credential_revision": run.credential_revision,
        "risk_level": action.risk_level, "approval_required": action.approval_required, "resource_revision": resource_revision}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def set_mock_resource_grant(session: AsyncSession, *, identity: IdentityContext, settings: Settings,
    owner_user_id: UUID, user_id: UUID, permission: str, is_enabled: bool, expected_revision: int,
    correlation_id: str,
) -> MockResourceGrant:
    require_mock(settings)
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    if "platform_admin" not in current.principal.roles:
        raise ToolPolicyError("MOCK_GRANT_ADMIN_REQUIRED", "仅当前租户管理员可管理显式 Mock 授权")
    if permission not in {"approve", "ticket_handle", "support_context_read"}:
        raise ActionStateConflict("未知资源授权类型")
    owner = await live_identity(session, tenant_id=current.user.tenant_id, user_id=owner_user_id)
    grantee = await live_identity(session, tenant_id=current.user.tenant_id, user_id=user_id)
    if not STAFF_ROLES.intersection(grantee.principal.roles) or permission == "approve" and owner.user.id == grantee.user.id:
        raise ActionStateConflict("需指定有效员工审批/处理人员，不能为本人授予自审权限")
    # 同项目控制面更新串行，避免首次授予并发插入。
    await session.scalar(select(User).where(User.id == owner.user.id, User.tenant_id == current.user.tenant_id).with_for_update(key_share=True))
    grant = await session.scalar(select(MockResourceGrant).where(MockResourceGrant.tenant_id == current.user.tenant_id,
        MockResourceGrant.owner_user_id == owner_user_id, MockResourceGrant.user_id == user_id,
        MockResourceGrant.permission == permission).execution_options(populate_existing=True).with_for_update())
    if grant is None:
        if expected_revision != 0:
            raise ActionStateConflict("授权不存在，请刷新")
        grant = MockResourceGrant(tenant_id=current.user.tenant_id, project_id=mock_project_id(current.user.tenant_id, owner_user_id),
            owner_user_id=owner_user_id, user_id=user_id, permission=permission, is_enabled=is_enabled,
            revision=1, granted_by=current.user.id, updated_at=utc_now())
        session.add(grant)
    else:
        if grant.revision != expected_revision:
            raise ActionStateConflict("授权版本已变化，请刷新")
        grant.is_enabled, grant.revision, grant.granted_by, grant.updated_at = is_enabled, grant.revision + 1, current.user.id, utc_now()
    await session.flush()
    session.add(AgentAuditEvent(tenant_id=current.user.tenant_id, actor_user_id=current.user.id,
        resource_type=AuditResourceType.TOOL, action=AuditAction.TOOL_UPDATED, correlation_id=correlation_id,
        metadata_payload={"kind": "mock_resource_grant", "grant_id": str(grant.id), "permission": permission,
            "revision": grant.revision, "is_enabled": is_enabled, "data_environment": "mock"}))
    return grant


async def request_action_approval(session: AsyncSession, *, action_id: UUID, identity: IdentityContext,
    settings: Settings, approver_id: UUID, expected_generation: int, expected_parameters_digest: str,
    expected_catalog_version: int, context_run_id: UUID | None,
    runtime_wait: bool = False,
) -> ApprovalRequest:
    require_mock(settings)
    action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    run, version, policy, project_id = await authorized_action_source(session,
        run_id=context_run_id or action.execution_run_id or action.run_id, identity=identity)
    action = await lock_business_action(session, action_id=action.id, tenant_id=action.tenant_id)
    if approver_id == action.proposed_by:
        raise ToolPolicyError("APPROVAL_SELF_DECISION_FORBIDDEN", "申请者不得本人审批")
    allowed_statuses = {RunStatus.RUNNING} if runtime_wait else {RunStatus.COMPLETED, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION}
    if (run.status not in allowed_statuses or action.generation != expected_generation or action.parameters_digest != expected_parameters_digest
        or policy.catalog_version != expected_catalog_version or policy.implementation_version != action.implementation_version
        or action.resource_kind != "mock_project" or action.resource_id != str(project_id)
        or frozen_parameters_digest(MockTicketParameters.model_validate(action.parameters)) != action.parameters_digest):
        raise ActionStateConflict("动作来源、冻结参数、版本或资源已变化，请刷新")
    parameters = MockTicketParameters.model_validate(action.parameters)
    case = await ensure_mock_case(session, tenant_id=action.tenant_id, project_id=project_id, owner_user_id=action.proposed_by,
        issue_key=parameters.issue_key, conversation_id=run.conversation_id)
    await assert_mock_write_allowed(session, tenant_id=action.tenant_id, project_id=project_id, owner_user_id=action.proposed_by,
        issue_key=parameters.issue_key, conversation_id=run.conversation_id)
    await require_mock_staff(session, tenant_id=action.tenant_id, owner_user_id=action.proposed_by,
        user_id=approver_id, permission="approve", lock=True)
    active = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.action_id == action.id,
        ApprovalRequest.tenant_id == action.tenant_id, ApprovalRequest.status.in_(("pending", "approved"))).with_for_update())
    if active is not None:
        if _aware(active.expires_at) <= utc_now():
            active.status, active.finished_at, active.revision = "expired", utc_now(), active.revision + 1
            await session.flush()
        elif active.approver_id == approver_id and active.scope_digest == _scope(action, run, version, policy, case.version):
            return active
        else:
            raise ActionStateConflict("已存在不同范围的审批，需先取消原申请")
    if action.status not in {"proposed", "waiting_approval"}:
        if not (action.status == "cancelled" and action.error_code in {"APPROVAL_EXPIRED", "APPROVAL_INVALIDATED", "APPROVAL_CANCELLED", "RUN_CANCELLED_BEFORE_ACTION"}
            and action.generation == 0 and await session.scalar(select(ApprovalConsumption.id).where(ApprovalConsumption.action_id == action.id)) is None):
            raise ActionStateConflict("该动作已经进入执行或终止，不能重新申请审批")
    approval = ApprovalRequest(tenant_id=action.tenant_id, action_id=action.id, run_id=run.id,
        applicant_id=action.proposed_by, approver_id=approver_id, tool_id=action.tool_id,
        implementation_version=policy.implementation_version, catalog_version=policy.catalog_version,
        parameters_digest=action.parameters_digest, scope_digest=_scope(action, run, version, policy, case.version), resource_revision=case.version, status="pending",
        revision=1, expires_at=utc_now() + timedelta(seconds=settings.approval_ttl_seconds))
    session.add(approval)
    action.status, action.error_code = "waiting_approval", None
    action.finished_at = None
    action.execution_run_id, action.execution_agent_version_id, action.execution_catalog_version = run.id, version.id, policy.catalog_version
    await session.flush()
    await append_event(session, run_id=run.id, event_type="approval.requested", data={"approval_id": str(approval.id),
        "action_id": str(action.id), "status": "pending", "expires_at": approval.expires_at.isoformat(), "data_environment": "mock"})
    _audit_approval(session, approval, actor_id=identity.user.id, audit_action=AuditAction.APPROVAL_REQUESTED, correlation_id=run.correlation_id)
    return approval


async def maintain_waiting_approval(session: AsyncSession, *, settings: Settings) -> int:
    """有限领取一个等待项，过期/撤权/停用/删除收敛；已消费动作交由对账处理。"""
    if not settings.business_action_mock_enabled or settings.environment == "production":
        return 0
    candidate = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.status.in_(("pending", "approved")),
        ApprovalRequest.next_check_at <= utc_now()).order_by(ApprovalRequest.next_check_at, ApprovalRequest.id).limit(1))
    if candidate is None:
        return 0
    _, run = await lock_run_scope(session, candidate.run_id, tenant_id=candidate.tenant_id)
    action = await lock_business_action(session, action_id=candidate.action_id, tenant_id=candidate.tenant_id)
    approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == candidate.id)
        .execution_options(populate_existing=True).with_for_update(skip_locked=True))
    if approval is None or approval.status not in {"pending", "approved"}:
        return 0
    terminal = None
    if action.status not in {"waiting_approval", "queued"}:
        terminal = "invalidated"
    elif _aware(approval.expires_at) <= utc_now():
        terminal = "expired"
    else:
        try:
            actor = await live_identity(session, tenant_id=action.tenant_id, user_id=action.proposed_by)
            current_run, version, policy, _ = await authorized_action_source(session, run_id=approval.run_id, identity=actor)
            await _validate_approval(session, approval=approval, action=action, run=current_run,
                version=version, policy=policy, expected_status=approval.status)
        except (RuntimeAuthorizationDenied, RunCancellationRequested, ToolPolicyError, ActionStateConflict, ResourceNotFoundError):
            terminal = "invalidated"
    approval.next_check_at = utc_now() + timedelta(seconds=30)
    if terminal is not None:
        approval.status, approval.finished_at, approval.revision = terminal, utc_now(), approval.revision + 1
        if action.status in {"waiting_approval", "queued"}:
            # 尚未产生执行许可；未知/运行中结果绝不能当作取消成功。
            action.status, action.error_code, action.finished_at = "cancelled", "APPROVAL_" + terminal.upper(), utc_now()
        await append_event(session, run_id=run.id, event_type="approval." + terminal,
            data={"approval_id": str(approval.id), "action_id": str(action.id), "status": terminal})
        await settle_waiting_run(session, run=run)
    return 1


async def read_approval(session: AsyncSession, *, request_id: UUID, identity: IdentityContext) -> tuple[ApprovalRequest, BusinessAction]:
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == request_id, approval_visibility(current)))
    if approval is None:
        raise ResourceNotFoundError("审批不存在或当前不可访问")
    action = await session.scalar(select(BusinessAction).where(BusinessAction.id == approval.action_id,
        BusinessAction.tenant_id == approval.tenant_id))
    if action is None:
        raise ResourceNotFoundError("审批动作不存在")
    if current.user.id == approval.applicant_id:
        await get_action_for_user(session, action_id=action.id, identity=current, context_run_id=approval.run_id)
    else:
        await require_mock_staff(session, tenant_id=approval.tenant_id, owner_user_id=approval.applicant_id,
            user_id=current.user.id, permission="approve")
    return approval, action


async def _validate_approval(session: AsyncSession, *, approval: ApprovalRequest, action: BusinessAction,
    run: AgentRun, version: AgentVersion, policy: ResolvedToolPolicy, expected_status: str,
) -> None:
    if (approval.status != expected_status or _aware(approval.expires_at) <= utc_now()
        or approval.applicant_id != action.proposed_by or approval.approver_id == action.proposed_by
        or approval.run_id != run.id or approval.tool_id != action.tool_id
        or approval.parameters_digest != action.parameters_digest or approval.scope_digest != _scope(action, run, version, policy, approval.resource_revision)
        or run.status not in {RunStatus.COMPLETED, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION}
        or frozen_parameters_digest(MockTicketParameters.model_validate(action.parameters)) != action.parameters_digest):
        raise ApprovalScopeInvalid("审批已过期、已使用或冻结授权范围已变化")
    parameters = MockTicketParameters.model_validate(action.parameters)
    case = await ensure_mock_case(session, tenant_id=action.tenant_id, project_id=mock_project_id(action.tenant_id, action.proposed_by),
        owner_user_id=action.proposed_by, issue_key=parameters.issue_key, conversation_id=run.conversation_id)
    if approval.resource_revision is None or approval.resource_revision != case.version:
        raise ApprovalScopeInvalid("问题接管范围已变化或旧范围未知，需要重新独立审批")
    await assert_mock_write_allowed(session, tenant_id=action.tenant_id, project_id=case.project_id,
        owner_user_id=action.proposed_by, issue_key=parameters.issue_key, conversation_id=run.conversation_id)
    await require_mock_staff(session, tenant_id=action.tenant_id, owner_user_id=action.proposed_by,
        user_id=approval.approver_id, permission="approve", lock=True)
    credential = await session.scalar(select(ModelCredentialRevision.id).join(ModelCredential,
        ModelCredential.id == ModelCredentialRevision.credential_id).join(ModelEndpointVersion,
        ModelEndpointVersion.credential_id == ModelCredential.id).where(ModelEndpointVersion.id == run.model_endpoint_version_id,
        ModelEndpointVersion.tenant_id == action.tenant_id, ModelCredential.tenant_id == action.tenant_id,
        ModelCredentialRevision.tenant_id == action.tenant_id, ModelCredentialRevision.revision == run.credential_revision))
    if credential is None:
        raise ApprovalScopeInvalid("固定凭据 revision 已不可用，不能借审批恢复执行")


async def decide_approval(session: AsyncSession, *, request_id: UUID, identity: IdentityContext, settings: Settings,
    expected_revision: int, decision: str, reason: str,
) -> ApprovalRequest:
    require_mock(settings)
    visible, _ = await read_approval(session, request_id=request_id, identity=identity)
    if visible.applicant_id == identity.user.id or visible.approver_id != identity.user.id:
        raise ToolPolicyError("APPROVAL_SELF_DECISION_FORBIDDEN", "仅指定的非本人审批者可以作出决定")
    actor = await live_identity(session, tenant_id=visible.tenant_id, user_id=visible.applicant_id)
    run, version, policy, _ = await authorized_action_source(session, run_id=visible.run_id, identity=actor)
    action = await lock_business_action(session, action_id=visible.action_id, tenant_id=visible.tenant_id)
    approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == visible.id,
        ApprovalRequest.tenant_id == visible.tenant_id).execution_options(populate_existing=True).with_for_update())
    old = await session.scalar(select(ApprovalDecision).where(ApprovalDecision.request_id == approval.id))
    if old is not None:
        if old.decided_by == identity.user.id and old.decision == decision and old.reason == _safe_reason(reason):
            return approval
        raise ActionStateConflict("该申请已有不可覆盖的决定")
    if approval.revision != expected_revision or action.status != "waiting_approval":
        raise ActionStateConflict("审批或动作状态已变化")
    await _validate_approval(session, approval=approval, action=action, run=run, version=version, policy=policy, expected_status="pending")
    if decision not in {"approved", "rejected"} or not reason.strip() or len(reason) > 1000:
        raise ActionStateConflict("批准或拒绝必须给出有效理由")
    safe_reason = _safe_reason(reason)
    session.add(ApprovalDecision(tenant_id=approval.tenant_id, request_id=approval.id, decided_by=identity.user.id,
        decision=decision, reason=safe_reason))
    approval.status, approval.revision, approval.finished_at = decision, approval.revision + 1, utc_now()
    await transition_business_action(session, action_id=action.id, tenant_id=action.tenant_id,
        expected_generation=action.generation, target="queued" if decision == "approved" else "cancelled")
    action.next_attempt_at = utc_now()
    action.error_code = None if decision == "approved" else "APPROVAL_REJECTED"
    if decision == "rejected":
        action.finished_at = utc_now()
    await append_event(session, run_id=run.id, event_type="approval.decided", data={"approval_id": str(approval.id),
        "action_id": str(action.id), "status": decision, "revision": approval.revision,
        "reason_digest": hashlib.sha256(safe_reason.encode()).hexdigest(), "data_environment": "mock"})
    _audit_approval(session, approval, actor_id=identity.user.id, audit_action=AuditAction.APPROVAL_DECIDED, correlation_id=run.correlation_id)
    await settle_waiting_run(session, run=run)
    return approval


async def validate_approved_action(session: AsyncSession, *, action: BusinessAction, run: AgentRun,
    version: AgentVersion, policy: ResolvedToolPolicy,
) -> ApprovalRequest:
    approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.action_id == action.id,
        ApprovalRequest.tenant_id == action.tenant_id, ApprovalRequest.status == "approved").with_for_update())
    if approval is None:
        raise ApprovalScopeInvalid("动作缺少有效独立批准")
    await _validate_approval(session, approval=approval, action=action, run=run, version=version, policy=policy, expected_status="approved")
    previous = await session.scalar(select(ApprovalConsumption.id).where(ApprovalConsumption.action_id == action.id))
    if previous is not None:
        raise ActionStateConflict("该逻辑动作的批准已消费，不允许重复出站")
    return approval


async def requires_action_approval(session: AsyncSession, action: BusinessAction, policy: ResolvedToolPolicy) -> bool:
    return (action.approval_required or action.risk_level != "R2" or policy.approval_required or policy.risk_number >= 3
        or await session.scalar(select(ApprovalRequest.id).where(ApprovalRequest.action_id == action.id,
            ApprovalRequest.tenant_id == action.tenant_id, ApprovalRequest.status.in_(("pending", "approved", "consumed"))).limit(1)) is not None)


async def consume_action_approval(session: AsyncSession, *, action: BusinessAction, run: AgentRun,
    version: AgentVersion, policy: ResolvedToolPolicy, attempt: ActionAttempt,
) -> None:
    approval = await validate_approved_action(session, action=action, run=run, version=version, policy=policy)
    session.add(ApprovalConsumption(tenant_id=action.tenant_id, request_id=approval.id, action_id=action.id,
        attempt_id=attempt.id, generation=attempt.generation))
    approval.status, approval.revision = "consumed", approval.revision + 1
    await append_event(session, run_id=run.id, event_type="approval.consumed", data={"approval_id": str(approval.id),
        "action_id": str(action.id), "generation": attempt.generation, "data_environment": "mock"})
    _audit_approval(session, approval, actor_id=action.proposed_by, audit_action=AuditAction.APPROVAL_CONSUMED, correlation_id=run.correlation_id)


async def validate_consumed_approval(session: AsyncSession, *, action: BusinessAction, run: AgentRun,
    version: AgentVersion, policy: ResolvedToolPolicy, attempt_id: UUID, generation: int,
) -> None:
    consumption = await session.scalar(select(ApprovalConsumption).where(ApprovalConsumption.action_id == action.id,
        ApprovalConsumption.tenant_id == action.tenant_id, ApprovalConsumption.attempt_id == attempt_id,
        ApprovalConsumption.generation == generation))
    approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == consumption.request_id,
        ApprovalRequest.tenant_id == action.tenant_id).with_for_update()) if consumption is not None else None
    if approval is None:
        raise ActionStateConflict("出站缺少匹配的一次性批准消费")
    await _validate_approval(session, approval=approval, action=action, run=run, version=version, policy=policy, expected_status="consumed")


async def cancel_approval(session: AsyncSession, *, request_id: UUID, identity: IdentityContext,
    expected_revision: int, settings: Settings,
) -> ApprovalRequest:
    require_mock(settings)
    visible, _ = await read_approval(session, request_id=request_id, identity=identity)
    if visible.applicant_id != identity.user.id:
        raise ToolPolicyError("APPROVAL_CANCEL_FORBIDDEN", "仅申请者可取消等待中的审批")
    await authorized_action_source(session, run_id=visible.run_id, identity=identity)
    action = await lock_business_action(session, action_id=visible.action_id, tenant_id=visible.tenant_id)
    approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == visible.id).execution_options(populate_existing=True).with_for_update())
    if approval.status == "cancelled":
        return approval
    if approval.revision != expected_revision or approval.status not in {"pending", "approved"} or action.status not in {"waiting_approval", "queued"}:
        raise ActionStateConflict("已执行或已终止的审批不能取消；未知副作用需要对账")
    approval.status, approval.finished_at, approval.revision = "cancelled", utc_now(), approval.revision + 1
    await transition_business_action(session, action_id=action.id, tenant_id=action.tenant_id,
        expected_generation=action.generation, target="cancelled")
    action.error_code, action.finished_at = "APPROVAL_CANCELLED", utc_now()
    await append_event(session, run_id=approval.run_id, event_type="approval.cancelled", data={"approval_id": str(approval.id),
        "action_id": str(action.id), "status": "cancelled"})
    run = await session.get(AgentRun, approval.run_id)
    if run is not None:
        await settle_waiting_run(session, run=run)
    return approval


async def prepare_runtime_approvals(session: AsyncSession, *, run: AgentRun, identity: IdentityContext, settings: Settings) -> bool:
    """最终产品事务中挂起业务阶段；明确且唯一的项目审批人才可自动指定。"""
    if not settings.business_action_mock_enabled or settings.environment == "production":
        return False
    actions = (await session.scalars(select(BusinessAction).where(BusinessAction.tenant_id == run.tenant_id,
        BusinessAction.run_id == run.id, BusinessAction.proposed_by == identity.user.id,
        BusinessAction.status == "proposed").order_by(BusinessAction.created_at, BusinessAction.id).limit(20))).all()
    waiting = False
    for action in actions:
        _, _, policy, _ = await authorized_action_source(session, run_id=run.id, identity=identity)
        if not await requires_action_approval(session, action, policy):
            continue
        candidates = (await session.scalars(select(MockResourceGrant.user_id).where(MockResourceGrant.tenant_id == run.tenant_id,
            MockResourceGrant.owner_user_id == identity.user.id, MockResourceGrant.project_id == mock_project_id(run.tenant_id, identity.user.id),
            MockResourceGrant.permission == "approve", MockResourceGrant.is_enabled.is_(True), MockResourceGrant.user_id != identity.user.id)
            .order_by(MockResourceGrant.user_id).limit(2))).all()
        # 没有或多个明确人员时，保留提案由用户选择，不让模型猜测审批人。
        if len(candidates) != 1:
            continue
        try:
            await require_mock_staff(session, tenant_id=run.tenant_id, owner_user_id=identity.user.id,
                user_id=candidates[0], permission="approve")
        except ToolPolicyError:
            continue
        try:
            await request_action_approval(session, action_id=action.id, identity=identity, settings=settings, approver_id=candidates[0],
                expected_generation=action.generation, expected_parameters_digest=action.parameters_digest,
                expected_catalog_version=policy.catalog_version, context_run_id=run.id, runtime_wait=True)
        except (ToolPolicyError, ActionStateConflict):
            await append_event(session, run_id=run.id, event_type="action.write_frozen", data={"action_id": str(action.id), "status": action.status})
            continue
        waiting = True
    return waiting


async def queue_runtime_mock_tickets(session: AsyncSession, *, run: AgentRun, identity: IdentityContext, settings: Settings) -> None:
    """显式自动策略只提交已成功诊断提案的 R2 意图，仍需 Worker 独立执行与复核。"""
    if not settings.business_action_mock_auto_create_enabled or run.status != RunStatus.COMPLETED:
        return
    from supportops_core.models import RunAttempt, ToolInvocation
    attempt = await session.get(RunAttempt, run.current_attempt_id) if run.current_attempt_id is not None else None
    from supportops_core.context_services import ContextSnapshot
    context = ContextSnapshot.model_validate(attempt.context_snapshot) if attempt is not None and attempt.context_snapshot else None
    if context is None or context.diagnostic is None or context.diagnostic.escalation is None or context.diagnostic.status == "needs_information":
        return
    issued = (await session.scalars(select(ToolInvocation.business_action_id).where(ToolInvocation.tenant_id == run.tenant_id,
        ToolInvocation.run_id == run.id, ToolInvocation.attempt_id == run.current_attempt_id, ToolInvocation.tool_id == "create_mock_ticket",
        ToolInvocation.status == "succeeded", ToolInvocation.business_action_id.is_not(None)).distinct().limit(20))).all()
    from supportops_core.action_proposal_services import request_action_execution
    for action_id in issued:
        action = await session.scalar(select(BusinessAction).where(BusinessAction.id == action_id,
            BusinessAction.tenant_id == run.tenant_id, BusinessAction.proposed_by == identity.user.id))
        if action is None or action.status != "proposed":
            continue
        _, _, policy, _ = await authorized_action_source(session, run_id=run.id, identity=identity)
        if await requires_action_approval(session, action, policy):
            continue
        try:
            await request_action_execution(session, action_id=action.id, identity=identity, settings=settings,
                expected_generation=action.generation, expected_parameters_digest=action.parameters_digest,
                expected_catalog_version=policy.catalog_version, correlation_id=run.correlation_id, context_run_id=run.id)
        except (ToolPolicyError, ActionStateConflict):
            await append_event(session, run_id=run.id, event_type="action.auto_execution_blocked", data={"action_id": str(action.id), "status": action.status})


async def settle_waiting_run(session: AsyncSession, *, run: AgentRun) -> None:
    """仅收敛业务等待，不重跑模型；回答完成不冒充动作成功。调用方持有 Run 锁。"""
    if run.status not in {RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION}:
        return
    statuses = (await session.scalars(select(BusinessAction.status).where(BusinessAction.tenant_id == run.tenant_id,
        BusinessAction.execution_run_id == run.id))).all()
    target = RunStatus.WAITING_APPROVAL if "waiting_approval" in statuses else RunStatus.WAITING_ACTION if any(
        status in {"queued", "running", "unknown", "reconciling"} for status in statuses) else RunStatus.COMPLETED
    if run.status == target:
        return
    transition_run(run, target)
    if target == RunStatus.COMPLETED:
        run.end_to_end_latency_ms = max(0, int((_aware(run.finished_at) - _aware(run.created_at)).total_seconds() * 1000))
    await append_event(session, run_id=run.id, event_type="run." + target.value,
        data={"status": target.value, "answer_ready": True, "business_results_separate": True})


async def cancel_waiting_run_actions(session: AsyncSession, *, run: AgentRun) -> None:
    """Run 锁下取消尚未发出的副作用；执行中/未知动作保留并继续对账。"""
    actions = (await session.scalars(select(BusinessAction).where(BusinessAction.tenant_id == run.tenant_id,
        BusinessAction.execution_run_id == run.id).order_by(BusinessAction.id).with_for_update())).all()
    for action in actions:
        if action.status not in {"proposed", "waiting_approval", "queued"}:
            continue
        approvals = (await session.scalars(select(ApprovalRequest).where(ApprovalRequest.tenant_id == run.tenant_id,
            ApprovalRequest.action_id == action.id, ApprovalRequest.status.in_(("pending", "approved"))).with_for_update())).all()
        for approval in approvals:
            approval.status, approval.finished_at, approval.revision = "cancelled", utc_now(), approval.revision + 1
        action.status, action.error_code, action.finished_at = "cancelled", "RUN_CANCELLED_BEFORE_ACTION", utc_now()
        await append_event(session, run_id=run.id, event_type="action.cancelled",
            data={"action_id": str(action.id), "status": "cancelled", "code": action.error_code})


def _audit_approval(session: AsyncSession, approval: ApprovalRequest, *, actor_id: UUID,
    audit_action: AuditAction, correlation_id: str,
) -> None:
    session.add(AgentAuditEvent(tenant_id=approval.tenant_id, actor_user_id=actor_id,
        resource_type=AuditResourceType.TOOL, action=audit_action, correlation_id=correlation_id,
        metadata_payload={"approval_id": str(approval.id), "action_id": str(approval.action_id),
            "status": approval.status, "revision": approval.revision, "parameters_digest": approval.parameters_digest,
            "implementation_version": approval.implementation_version, "catalog_version": approval.catalog_version, "data_environment": "mock"}))


async def replace_action_proposal(session: AsyncSession, *, action_id: UUID, identity: IdentityContext, settings: Settings,
    expected_generation: int, expected_parameters_digest: str, expected_catalog_version: int,
    context_run_id: UUID | None, approver_id: UUID, title: str, summary: str,
) -> BusinessAction:
    """只替代尚无出站许可的冻结提案；改参的新意图必须重新独立审批。"""
    require_mock(settings)
    original = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    run, version, policy, project_id = await authorized_action_source(session,
        run_id=context_run_id or original.execution_run_id or original.run_id, identity=identity)
    original = await lock_business_action(session, action_id=action_id, tenant_id=original.tenant_id)
    if (original.generation != expected_generation or original.parameters_digest != expected_parameters_digest
        or policy.catalog_version != expected_catalog_version or policy.implementation_version != original.implementation_version
        or run.status not in {RunStatus.COMPLETED, RunStatus.WAITING_APPROVAL, RunStatus.WAITING_ACTION}
        or original.resource_kind != "mock_project" or original.resource_id != str(project_id)):
        raise ActionStateConflict("提案或授权上下文已变化，请刷新后重新确认改参")
    if approver_id == original.proposed_by:
        raise ToolPolicyError("APPROVAL_SELF_DECISION_FORBIDDEN", "改参后仍须指定非本人审批者")
    await require_mock_staff(session, tenant_id=original.tenant_id, owner_user_id=original.proposed_by,
        user_id=approver_id, permission="approve")
    try:
        parameters = MockTicketParameters.model_validate(original.parameters).model_copy(update={"title": title, "summary": summary})
        parameters = MockTicketParameters.model_validate(parameters.model_dump())
    except ValueError as exc:
        raise ActionStateConflict("改参字段包含疑似凭据或不符合受限 Schema") from exc
    digest = frozen_parameters_digest(parameters)
    if digest == original.parameters_digest:
        raise ActionStateConflict("参数未变化，无需替代原提案")
    # parent 为用户明确选择的版本，参数摘要为新意图；同一改参重放稳定去重。
    key = hashlib.sha256(("mock_amendment_v1:" + original.logical_key + ":" + digest).encode()).hexdigest()
    existing = await session.scalar(select(BusinessAction).where(BusinessAction.tenant_id == original.tenant_id,
        BusinessAction.tool_id == original.tool_id, BusinessAction.logical_key == key).with_for_update())
    if existing is not None:
        if existing.supersedes_action_id != original.id or existing.parameters_digest != digest:
            raise ActionStateConflict("替代意图已存在且范围不同")
        chosen = await session.scalar(select(ApprovalRequest.approver_id).where(ApprovalRequest.action_id == existing.id,
            ApprovalRequest.tenant_id == existing.tenant_id).order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc()).limit(1))
        if chosen != approver_id:
            raise ActionStateConflict("同一改参意图已指定其他审批者，不覆盖已有申请")
        from supportops_core.action_proposal_services import _link_proposal
        await _link_proposal(session, run=run, action=existing)
        return existing
    issued = await session.scalar(select(ActionAttempt.id).where(ActionAttempt.tenant_id == original.tenant_id,
        ActionAttempt.action_id == original.id, ActionAttempt.kind == "execute").limit(1))
    consumed = await session.scalar(select(ApprovalConsumption.id).where(ApprovalConsumption.action_id == original.id,
        ApprovalConsumption.tenant_id == original.tenant_id))
    if issued is not None or consumed is not None or original.status not in {"proposed", "waiting_approval", "queued", "cancelled"}:
        raise ActionStateConflict("已有出站许可或未知副作用，不能用改参换键绕过对账")
    approvals = (await session.scalars(select(ApprovalRequest).where(ApprovalRequest.tenant_id == original.tenant_id,
        ApprovalRequest.action_id == original.id, ApprovalRequest.status.in_(("pending", "approved"))).with_for_update())).all()
    for approval in approvals:
        approval.status, approval.revision, approval.finished_at = "invalidated", approval.revision + 1, utc_now()
    original.status, original.error_code, original.finished_at = "cancelled", "ACTION_PARAMETERS_REPLACED", utc_now()
    replacement = BusinessAction(tenant_id=original.tenant_id, proposed_by=original.proposed_by, run_id=run.id,
        agent_version_id=version.id, tool_id=original.tool_id, implementation_version=policy.implementation_version,
        catalog_version=policy.catalog_version, supersedes_action_id=original.id, logical_key=key,
        parameters=parameters.model_dump(mode="json"), parameters_digest=digest, parameter_schema_version=original.parameter_schema_version,
        resource_kind=original.resource_kind, resource_id=original.resource_id, risk_level=f"R{max(int(original.risk_level[1:]), 2, policy.risk_number)}",
        approval_required=True, status="proposed", reconciliation_limit=settings.action_max_reconciliations)
    session.add(replacement)
    await session.flush()
    from supportops_core.action_proposal_services import _link_proposal
    await _link_proposal(session, run=run, action=replacement)
    await request_action_approval(session, action_id=replacement.id, identity=identity, settings=settings, approver_id=approver_id,
        expected_generation=0, expected_parameters_digest=digest, expected_catalog_version=policy.catalog_version, context_run_id=run.id)
    await append_event(session, run_id=run.id, event_type="action.parameters_replaced", data={"action_id": str(replacement.id),
        "supersedes_action_id": str(original.id), "status": "waiting_approval", "data_environment": "mock"})
    await settle_waiting_run(session, run=run)
    return replacement
