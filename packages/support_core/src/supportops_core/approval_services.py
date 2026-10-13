"""显式 Mock 审批；审批者身份仅用于决定，执行仍使用原申请者授权。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.action_contracts import MockTicketParameters, frozen_parameters_digest
from supportops_core.agent_config import SECRET_PATTERNS
from supportops_core.action_ledger_services import ActionStateConflict, lock_business_action
from supportops_core.action_proposal_services import ActionFeatureUnavailable, authorized_action_source, get_action_for_user
from supportops_core.auth import IdentityContext, Principal
from supportops_core.config import Settings
from supportops_core.enums import AuditAction, AuditResourceType, RunStatus
from supportops_core.models import (
    ActionAttempt, AgentAuditEvent, AgentRun, AgentVersion, ApprovalConsumption, ApprovalDecision,
    ApprovalRequest, BusinessAction, MockResourceGrant, ModelCredential, ModelCredentialRevision, ModelEndpointVersion, Tenant, User, utc_now,
)
from supportops_core.services import ResourceNotFoundError, append_event
from supportops_core.run_attempt_services import RuntimeAuthorizationDenied, RunCancellationRequested, lock_run_scope
from supportops_core.tool_policy import ResolvedToolPolicy, ToolPolicyError

STAFF_ROLES = frozenset({"platform_admin", "employee", "agent_user"})


def _safe_reason(reason: str) -> str:
    for pattern in SECRET_PATTERNS:
        reason = pattern.sub("[REDACTED]", reason)
    return reason[:1000]


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


def _scope(action: BusinessAction, run: AgentRun, version: AgentVersion, policy: ResolvedToolPolicy) -> str:
    data = {"tenant_id": str(action.tenant_id), "applicant_id": str(action.proposed_by), "action_id": str(action.id),
        "logical_key": action.logical_key, "run_id": str(run.id), "resource_kind": action.resource_kind, "resource_id": action.resource_id,
        "parameters_digest": action.parameters_digest, "schema": action.parameter_schema_version,
        "tool_id": action.tool_id, "implementation_version": policy.implementation_version, "catalog_version": policy.catalog_version,
        "agent_version_id": str(version.id), "config_digest": run.config_digest,
        "model_version_id": str(run.model_endpoint_version_id), "credential_revision": run.credential_revision,
        "risk_level": action.risk_level, "approval_required": action.approval_required}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def set_mock_resource_grant(session: AsyncSession, *, identity: IdentityContext, settings: Settings,
    owner_user_id: UUID, user_id: UUID, permission: str, is_enabled: bool, expected_revision: int,
    correlation_id: str,
) -> MockResourceGrant:
    require_mock(settings)
    current = await live_identity(session, tenant_id=identity.principal.tenant_id, user_id=identity.user.id)
    if "platform_admin" not in current.principal.roles:
        raise ToolPolicyError("MOCK_GRANT_ADMIN_REQUIRED", "仅当前租户管理员可管理显式 Mock 授权")
    if permission not in {"approve", "ticket_handle"}:
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
) -> ApprovalRequest:
    require_mock(settings)
    action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    run, version, policy, project_id = await authorized_action_source(session,
        run_id=context_run_id or action.execution_run_id or action.run_id, identity=identity)
    action = await lock_business_action(session, action_id=action.id, tenant_id=action.tenant_id)
    if approver_id == action.proposed_by:
        raise ToolPolicyError("APPROVAL_SELF_DECISION_FORBIDDEN", "申请者不得本人审批")
    if (run.status != RunStatus.COMPLETED or action.generation != expected_generation or action.parameters_digest != expected_parameters_digest
        or policy.catalog_version != expected_catalog_version or policy.implementation_version != action.implementation_version
        or action.resource_kind != "mock_project" or action.resource_id != str(project_id)
        or frozen_parameters_digest(MockTicketParameters.model_validate(action.parameters)) != action.parameters_digest):
        raise ActionStateConflict("动作来源、冻结参数、版本或资源已变化，请刷新")
    await require_mock_staff(session, tenant_id=action.tenant_id, owner_user_id=action.proposed_by,
        user_id=approver_id, permission="approve", lock=True)
    active = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.action_id == action.id,
        ApprovalRequest.tenant_id == action.tenant_id, ApprovalRequest.status.in_(("pending", "approved"))).with_for_update())
    if active is not None:
        if _aware(active.expires_at) <= utc_now():
            active.status, active.finished_at, active.revision = "expired", utc_now(), active.revision + 1
            await session.flush()
        elif active.approver_id == approver_id and active.scope_digest == _scope(action, run, version, policy):
            return active
        else:
            raise ActionStateConflict("已存在不同范围的审批，需先取消原申请")
    if action.status not in {"proposed", "waiting_approval"}:
        raise ActionStateConflict("该动作已经进入执行或终止，不能重新申请审批")
    approval = ApprovalRequest(tenant_id=action.tenant_id, action_id=action.id, run_id=run.id,
        applicant_id=action.proposed_by, approver_id=approver_id, tool_id=action.tool_id,
        implementation_version=policy.implementation_version, catalog_version=policy.catalog_version,
        parameters_digest=action.parameters_digest, scope_digest=_scope(action, run, version, policy), status="pending",
        revision=1, expires_at=utc_now() + timedelta(seconds=settings.approval_ttl_seconds))
    session.add(approval)
    action.status, action.error_code = "waiting_approval", None
    action.execution_run_id, action.execution_agent_version_id, action.execution_catalog_version = run.id, version.id, policy.catalog_version
    await session.flush()
    await append_event(session, run_id=run.id, event_type="approval.requested", data={"approval_id": str(approval.id),
        "action_id": str(action.id), "status": "pending", "expires_at": approval.expires_at.isoformat(), "data_environment": "mock"})
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
    if _aware(approval.expires_at) <= utc_now():
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
        or approval.parameters_digest != action.parameters_digest or approval.scope_digest != _scope(action, run, version, policy)
        or run.status != RunStatus.COMPLETED or frozen_parameters_digest(MockTicketParameters.model_validate(action.parameters)) != action.parameters_digest):
        raise ActionStateConflict("审批已过期、已使用或冻结授权范围已变化")
    await require_mock_staff(session, tenant_id=action.tenant_id, owner_user_id=action.proposed_by,
        user_id=approval.approver_id, permission="approve", lock=True)
    credential = await session.scalar(select(ModelCredentialRevision.id).join(ModelCredential,
        ModelCredential.id == ModelCredentialRevision.credential_id).join(ModelEndpointVersion,
        ModelEndpointVersion.credential_id == ModelCredential.id).where(ModelEndpointVersion.id == run.model_endpoint_version_id,
        ModelEndpointVersion.tenant_id == action.tenant_id, ModelCredential.tenant_id == action.tenant_id,
        ModelCredentialRevision.tenant_id == action.tenant_id, ModelCredentialRevision.revision == run.credential_revision))
    if credential is None:
        raise ActionStateConflict("固定凭据 revision 已不可用，不能借审批恢复执行")


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
    action.status = "queued" if decision == "approved" else "cancelled"
    action.next_attempt_at = utc_now()
    action.error_code = None if decision == "approved" else "APPROVAL_REJECTED"
    if decision == "rejected":
        action.finished_at = utc_now()
    await append_event(session, run_id=run.id, event_type="approval.decided", data={"approval_id": str(approval.id),
        "action_id": str(action.id), "status": decision, "revision": approval.revision,
        "reason_digest": hashlib.sha256(safe_reason.encode()).hexdigest(), "data_environment": "mock"})
    return approval


async def validate_approved_action(session: AsyncSession, *, action: BusinessAction, run: AgentRun,
    version: AgentVersion, policy: ResolvedToolPolicy,
) -> ApprovalRequest:
    approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.action_id == action.id,
        ApprovalRequest.tenant_id == action.tenant_id, ApprovalRequest.status == "approved").with_for_update())
    if approval is None:
        raise ActionStateConflict("动作缺少有效独立批准")
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
    action.status, action.error_code, action.finished_at = "cancelled", "APPROVAL_CANCELLED", utc_now()
    await append_event(session, run_id=approval.run_id, event_type="approval.cancelled", data={"approval_id": str(approval.id),
        "action_id": str(action.id), "status": "cancelled"})
    return approval
