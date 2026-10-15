"""从已关联的Mock动作、独立回执及当前目标冻结补偿意向；永不自动关闭工单。"""
from hashlib import sha256
import json
from uuid import UUID, uuid4
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from supportops_core.action_contracts import MockTicketParameters, frozen_parameters_digest
from supportops_core.action_ledger_services import ActionStateConflict, assert_action_release_allowed, lock_business_action
from supportops_core.action_proposal_services import ActionFeatureUnavailable, authorized_action_source, get_action_for_user
from supportops_core.auth import IdentityContext
from supportops_core.compensation_contracts import CompensationCreate, CompensationDetail, CompensationReadiness, CompensationScope, CompensationSummary
from supportops_core.config import Settings
from supportops_core.enums import AuditAction, AuditResourceType, RunStatus
from supportops_core.mock_action_connector import MockTargetProof
from supportops_core.models import ActionAttempt, ActionCompensationProposal, ActionReceipt, AgentAuditEvent, AgentRun, AgentVersion, MockSupportTicket, utc_now
from supportops_core.services import ResourceNotFoundError, append_event

EXECUTION_BLOCKERS = ["COMPENSATION_EXECUTOR_NOT_IMPLEMENTED", "NEW_INDEPENDENT_APPROVAL_REQUIRED", "FRESH_AUTHORIZATION_AND_TARGET_CHECK_REQUIRED"]


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def compensation_readiness(session: AsyncSession, *, action_id: UUID, context_run_id: UUID,
    identity: IdentityContext, settings: Settings, lock: bool = False,
) -> CompensationReadiness:
    if not settings.business_action_mock_enabled or settings.environment == "production":
        raise ActionFeatureUnavailable("补偿提案仅支持显式启用的隔离Mock，不能连接真实副作用")
    action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    run, version, policy, project_id = await authorized_action_source(session, run_id=context_run_id, identity=identity, lock=lock)
    if lock:
        action = await lock_business_action(session, action_id=action.id, tenant_id=identity.principal.tenant_id)
    blockers: list[str] = []
    if run.status != RunStatus.COMPLETED:
        blockers.append("COMPLETED_CONTEXT_REQUIRED")
    if action.status not in {"succeeded", "needs_attention"} or action.generation < 1:
        blockers.append("ACTION_RECONCILIATION_REQUIRED")
    if action.parameter_schema_version != "mock_ticket_v1" or action.tool_id != "create_mock_ticket":
        blockers.append("COMPENSATION_CONNECTOR_UNSUPPORTED")
    original_version = await session.scalar(select(AgentVersion).where(AgentVersion.id == action.agent_version_id,
        AgentVersion.tenant_id == action.tenant_id))
    if original_version is None or original_version.withdrawn_at is not None or version.withdrawn_at is not None:
        blockers.append("ACTION_RELEASE_WITHDRAWN")
    try:
        parameters = MockTicketParameters.model_validate(action.parameters)
        if frozen_parameters_digest(parameters) != action.parameters_digest or action.resource_id != str(project_id):
            blockers.append("ACTION_SCOPE_INTEGRITY_FAILED")
    except (ValidationError, ValueError, TypeError):
        blockers.append("ACTION_SCOPE_INTEGRITY_FAILED")
    attempt = await session.scalar(select(ActionAttempt).where(ActionAttempt.id == action.current_attempt_id,
        ActionAttempt.action_id == action.id, ActionAttempt.tenant_id == action.tenant_id, ActionAttempt.generation == action.generation))
    if (attempt is None or attempt.kind not in {"verify", "reconcile"} or attempt.status == "running" or attempt.finished_at is None
        or attempt.parameters_digest != action.parameters_digest):
        blockers.append("INDEPENDENT_RECEIPT_REQUIRED")
    receipt = await session.scalar(select(ActionReceipt).where(ActionReceipt.tenant_id == action.tenant_id,
        ActionReceipt.action_id == action.id, ActionReceipt.attempt_id == action.current_attempt_id)
        .order_by(ActionReceipt.observed_at.desc(), ActionReceipt.id.desc()).limit(1))
    proof = None
    if receipt is not None:
        try:
            proof = MockTargetProof.model_validate(receipt.verified_fields)
            expected = "verified" if action.status == "succeeded" else "conflict"
            if (receipt.status != expected or proof.status != expected or _digest(receipt.verified_fields) != receipt.result_digest
                or proof.logical_key != action.logical_key or proof.owner_user_id != action.proposed_by
                or proof.external_id != receipt.external_id or receipt.connector_version != proof.connector_version
                or (expected == "verified" and proof.parameters_digest != action.parameters_digest)):
                blockers.append("SOURCE_RECEIPT_INTEGRITY_FAILED")
        except (ValidationError, ValueError, TypeError):
            blockers.append("SOURCE_RECEIPT_INTEGRITY_FAILED")
    else:
        blockers.append("INDEPENDENT_RECEIPT_REQUIRED")
    # 固定租户/本人/项目/业务键过滤后才读取目标；返回只保留标识/版本/受限参数摘要，不复制正文。
    query = select(MockSupportTicket).where(MockSupportTicket.tenant_id == action.tenant_id,
        MockSupportTicket.owner_user_id == identity.principal.user_id, MockSupportTicket.project_id == project_id,
        MockSupportTicket.logical_key == action.logical_key).execution_options(populate_existing=True)
    ticket = await session.scalar(query.with_for_update(read=True) if lock else query)
    if ticket is None or ticket.external_id != (proof.external_id if proof else None) or not ticket.external_id.startswith("MOCK-"):
        blockers.append("CURRENT_TARGET_NOT_PROVEN")
    elif ticket.status not in {"open", "in_progress", "escalated"}:
        blockers.append("TARGET_ALREADY_CLOSED_OR_UNSUPPORTED")
    if blockers:
        return CompensationReadiness(scope=None, scope_digest=None, proposal_supported=False, blockers=sorted(set(blockers)))
    assert ticket is not None and receipt is not None and proof is not None
    try:
        target_digest = frozen_parameters_digest(MockTicketParameters(title=ticket.title, summary=ticket.summary, issue_key=ticket.issue_key))
    except (ValidationError, ValueError, TypeError):
        return CompensationReadiness(scope=None, scope_digest=None, proposal_supported=False, blockers=["CURRENT_TARGET_INTEGRITY_FAILED"])
    scope = CompensationScope(tenant_id=action.tenant_id, subject_user_id=action.proposed_by, action_id=action.id,
        action_generation=action.generation, action_status=action.status, original_parameters_digest=action.parameters_digest,
        logical_key=action.logical_key, source_receipt_id=receipt.id, source_receipt_digest=receipt.result_digest,
        source_receipt_status=receipt.status, target_ticket_id=ticket.id, target_external_id=ticket.external_id,
        target_version=ticket.version, target_status=ticket.status, target_parameters_digest=target_digest,
        context_run_id=run.id, agent_version_id=version.id, config_digest=run.config_digest,
        model_version_id=run.model_endpoint_version_id, credential_revision=run.credential_revision,
        original_tool_implementation_version=action.implementation_version, context_tool_implementation_version=policy.implementation_version,
        current_catalog_version=policy.catalog_version,
        reason_code="verification_conflict" if action.status == "needs_attention" else "user_requested_closure",
        minimum_risk="R4" if action.risk_level == "R4" or policy.risk_number >= 4 else "R3")
    return CompensationReadiness(scope=scope, scope_digest=_digest(scope.model_dump(mode="json")), proposal_supported=True,
        blockers=EXECUTION_BLOCKERS)


def compensation_summary(row: ActionCompensationProposal) -> CompensationSummary:
    return CompensationSummary(id=row.id, action_id=row.action_id, source_receipt_id=row.source_receipt_id,
        scope_digest=row.scope_digest, minimum_risk=row.minimum_risk, reason_code=row.reason_code, created_at=row.created_at)


def _recorded_scope(row: ActionCompensationProposal) -> CompensationScope:
    scope = CompensationScope.model_validate(row.scope)
    if (scope.tenant_id != row.tenant_id or scope.subject_user_id != row.subject_user_id or scope.action_id != row.action_id
        or scope.source_receipt_id != row.source_receipt_id or scope.minimum_risk != row.minimum_risk
        or scope.reason_code != row.reason_code or _digest(row.scope) != row.scope_digest or row.status != "proposed"):
        raise ValueError("compensation scope mismatch")
    return scope


async def create_compensation_proposal(session: AsyncSession, *, action_id: UUID, payload: CompensationCreate,
    identity: IdentityContext, settings: Settings,
) -> ActionCompensationProposal:
    readiness = await compensation_readiness(session, action_id=action_id, context_run_id=payload.context_run_id,
        identity=identity, settings=settings, lock=True)
    if not readiness.proposal_supported or readiness.scope is None or readiness.scope_digest != payload.expected_scope_digest:
        raise ActionStateConflict("补偿来源、当前工单或授权范围已变化，请重新读取；未知结果须先独立对账")
    scope = readiness.scope
    # 新提案也遵守版本撤回门禁；不因原动作曾获批准而复用写许可。
    action = await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=payload.context_run_id)
    await assert_action_release_allowed(session, tenant_id=scope.tenant_id,
        version_ids={action.agent_version_id, scope.agent_version_id})
    existing = await session.scalar(select(ActionCompensationProposal).where(ActionCompensationProposal.tenant_id == scope.tenant_id,
        ActionCompensationProposal.action_id == action_id, ActionCompensationProposal.scope_digest == readiness.scope_digest))
    if existing is not None:
        try:
            _recorded_scope(existing)
        except (ValidationError, ValueError, TypeError) as exc:
            raise ActionStateConflict("已有补偿提案完整性失败，不能覆盖历史") from exc
        return existing
    row = ActionCompensationProposal(id=uuid4(), tenant_id=scope.tenant_id, subject_user_id=scope.subject_user_id,
        action_id=scope.action_id, source_receipt_id=scope.source_receipt_id, scope=scope.model_dump(mode="json"),
        scope_digest=readiness.scope_digest, minimum_risk=scope.minimum_risk, reason_code=scope.reason_code,
        status="proposed", created_at=utc_now())
    session.add(row)
    await session.flush()
    run = await session.get(AgentRun, payload.context_run_id)
    if run is None:
        raise ResourceNotFoundError("补偿来源上下文不存在")
    session.add(AgentAuditEvent(tenant_id=scope.tenant_id, actor_user_id=identity.principal.user_id,
        resource_type=AuditResourceType.TOOL, action=AuditAction.BUSINESS_ACTION_COMPENSATION_PROPOSED,
        agent_id=run.agent_id, version_id=scope.agent_version_id, correlation_id=run.correlation_id,
        metadata_payload={"action_id": str(action_id), "proposal_id": str(row.id), "scope_digest": row.scope_digest,
            "source_receipt_id": str(row.source_receipt_id), "risk_level": row.minimum_risk, "execution_enabled": False}))
    await append_event(session, run_id=payload.context_run_id, event_type="action.compensation_proposed",
        data={"action_id": str(action_id), "proposal_id": str(row.id), "scope_digest": row.scope_digest,
            "source_receipt_id": str(row.source_receipt_id), "risk_level": row.minimum_risk,
            "actor_user_id": str(identity.principal.user_id), "status": "proposed", "data_environment": "mock", "execution_enabled": False})
    return row


async def compensation_detail(session: AsyncSession, *, row: ActionCompensationProposal, context_run_id: UUID,
    identity: IdentityContext, settings: Settings,
) -> CompensationDetail:
    try:
        scope = _recorded_scope(row)
    except (ValidationError, ValueError, TypeError):
        return CompensationDetail(proposal=compensation_summary(row), scope=None, integrity_status="integrity_failed",
            applicability="integrity_failed", blockers=["COMPENSATION_SCOPE_INTEGRITY_FAILED"])
    current = await compensation_readiness(session, action_id=row.action_id, context_run_id=context_run_id,
        identity=identity, settings=settings)
    applicability = "unavailable" if not current.proposal_supported else "current" if current.scope_digest == row.scope_digest else "changed"
    return CompensationDetail(proposal=compensation_summary(row), scope=scope if current.proposal_supported else None,
        integrity_status="recorded", applicability=applicability,
        blockers=current.blockers + (["FROZEN_SCOPE_CHANGED"] if applicability == "changed" else []))


async def get_compensation_proposal(session: AsyncSession, *, action_id: UUID, proposal_id: UUID,
    identity: IdentityContext, context_run_id: UUID,
) -> ActionCompensationProposal:
    await get_action_for_user(session, action_id=action_id, identity=identity, context_run_id=context_run_id)
    row = await session.scalar(select(ActionCompensationProposal).where(ActionCompensationProposal.id == proposal_id,
        ActionCompensationProposal.action_id == action_id, ActionCompensationProposal.tenant_id == identity.principal.tenant_id,
        ActionCompensationProposal.subject_user_id == identity.principal.user_id))
    if row is None:
        raise ResourceNotFoundError("补偿提案不存在")
    return row
