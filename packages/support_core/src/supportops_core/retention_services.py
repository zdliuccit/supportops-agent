"""不可变策略草稿、本人申请与一次只读影响预览；没有擦除执行能力。"""
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_services import PermissionDeniedError, require_platform_admin
from supportops_core.auth import AuthenticationError, IdentityNotProvisionedError, IdentityContext, ensure_identity
from supportops_core.models import AuthSession, ErasureRequest, RetentionPolicy, Tenant, User, utc_now
from supportops_core.retention_contracts import (
    DATA_CATEGORIES, ErasurePreviewReport, ErasureRequestCreate, ErasureRequestDetail,
    ErasureRequestSummary, ErasureReviewScope, RetentionPolicyCreate,
    RetentionPolicyDetail, RetentionPolicySummary,
)
from supportops_core.retention_inventory import category_impact, inventory_queries, live_reference_counts
from supportops_core.services import ResourceNotFoundError


class DataGovernanceConflict(ValueError):
    code = "DATA_GOVERNANCE_CONFLICT"


class DataGovernanceUnavailable(RuntimeError):
    code = "DATA_GOVERNANCE_PREVIEW_UNAVAILABLE"


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _time(value: datetime) -> str:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def _policy_digest(row: RetentionPolicy) -> str:
    return digest({"id": str(row.id), "tenant": str(row.tenant_id), "creator": str(row.created_by),
        "name": row.name, "definition": row.definition, "created_at": _time(row.created_at)})


def policy_definition(row: RetentionPolicy) -> RetentionPolicyCreate:
    try:
        definition = RetentionPolicyCreate.model_validate(row.definition)
        if row.status != "draft" or definition.name != row.name or _policy_digest(row) != row.policy_digest:
            raise ValueError("policy digest mismatch")
        return definition
    except (ValidationError, ValueError, TypeError) as exc:
        raise DataGovernanceConflict("策略草稿完整性校验失败") from exc


def policy_detail(row: RetentionPolicy) -> RetentionPolicyDetail:
    summary = RetentionPolicySummary(id=row.id, name=row.name, policy_digest=row.policy_digest,
        created_by=row.created_by, created_at=row.created_at)
    try:
        definition = policy_definition(row)
    except DataGovernanceConflict:
        return RetentionPolicyDetail(policy=summary, definition=None, integrity_status="integrity_failed")
    return RetentionPolicyDetail(policy=summary, definition=definition, integrity_status="recorded")


async def _current_subject(session: AsyncSession, identity: IdentityContext) -> IdentityContext:
    # 与同主体申请和身份撤销串行，避免依赖认证阶段留下的旧 identity map。
    await session.scalar(select(User).where(User.id == identity.principal.user_id,
        User.tenant_id == identity.principal.tenant_id).execution_options(populate_existing=True).with_for_update())
    await session.get(Tenant, identity.principal.tenant_id, populate_existing=True)
    if identity.principal.session_id is not None:
        await session.get(AuthSession, identity.principal.session_id, populate_existing=True)
    try:
        return await ensure_identity(session, identity.principal, require_session=True)
    except (AuthenticationError, IdentityNotProvisionedError) as exc:
        raise PermissionDeniedError("当前身份已失效，请重新登录") from exc


async def create_policy(session: AsyncSession, *, identity: IdentityContext, payload: RetentionPolicyCreate) -> RetentionPolicy:
    current = await _current_subject(session, identity)
    require_platform_admin(current)
    if payload.reviewer_user_ids:
        reviewers = (await session.scalars(select(User.id).where(User.tenant_id == current.principal.tenant_id,
            User.id.in_(payload.reviewer_user_ids), User.status == "active"))).all()
        if set(reviewers) != set(payload.reviewer_user_ids):
            raise DataGovernanceConflict("候选复核人员必须是同租户有效用户；该配置不授予复核权限")
    now = utc_now()
    row = RetentionPolicy(id=uuid4(), tenant_id=current.principal.tenant_id, name=payload.name,
        definition=payload.model_dump(mode="json"), status="draft", created_by=current.principal.user_id, created_at=now)
    row.policy_digest = _policy_digest(row)
    session.add(row)
    await session.flush()
    return row


def _payload(payload: ErasureRequestCreate) -> dict[str, object]:
    return {"categories": [category for category in DATA_CATEGORIES if category in payload.categories], "reason_code": payload.reason_code}


def _scope(row: ErasureRequest) -> str:
    return digest({"schema": "erasure_scope_v1", "id": str(row.id), "tenant": str(row.tenant_id),
        "subject": str(row.subject_user_id), "categories": row.categories, "reason": row.reason_code,
        "payload_digest": row.payload_digest, "policy_id": str(row.policy_id) if row.policy_id else None,
        "policy_digest": row.policy_digest, "cutoff_at": _time(row.cutoff_at)})


def _validate_scope(row: ErasureRequest) -> ErasureRequestCreate:
    try:
        payload = ErasureRequestCreate.model_validate({"categories": row.categories, "reason_code": row.reason_code})
        if digest(_payload(payload)) != row.payload_digest or _scope(row) != row.scope_digest:
            raise ValueError("scope digest mismatch")
        return payload
    except (ValidationError, ValueError, TypeError) as exc:
        raise DataGovernanceConflict("申请范围完整性校验失败") from exc


async def create_request(session: AsyncSession, *, identity: IdentityContext, payload: ErasureRequestCreate,
    idempotency_key: str,
) -> ErasureRequest:
    current = await _current_subject(session, identity)
    body = _payload(payload)
    body_digest = digest(body)
    previous = await session.scalar(select(ErasureRequest).where(ErasureRequest.tenant_id == current.principal.tenant_id,
        ErasureRequest.subject_user_id == current.principal.user_id, ErasureRequest.idempotency_key == idempotency_key))
    if previous is not None:
        _validate_scope(previous)
        if previous.payload_digest != body_digest:
            raise DataGovernanceConflict("同一幂等键已用于其他申请范围，请重新提交新申请")
        return previous
    policy = await session.scalar(select(RetentionPolicy).where(RetentionPolicy.tenant_id == current.principal.tenant_id)
        .order_by(RetentionPolicy.created_at.desc(), RetentionPolicy.id.desc()).limit(1))
    if policy is not None:
        policy_definition(policy)
    now = utc_now()
    row = ErasureRequest(id=uuid4(), tenant_id=current.principal.tenant_id, subject_user_id=current.principal.user_id,
        idempotency_key=idempotency_key, payload_digest=body_digest,
        categories=[category for category in DATA_CATEGORIES if category in payload.categories], reason_code=payload.reason_code,
        policy_id=policy.id if policy else None, policy_digest=policy.policy_digest if policy else None,
        cutoff_at=now, created_at=now, status="requested", revision=1)
    row.scope_digest = _scope(row)
    session.add(row)
    await session.flush()
    return row


async def get_request(session: AsyncSession, *, identity: IdentityContext, request_id: UUID, lock: bool = False) -> ErasureRequest:
    query = select(ErasureRequest).where(ErasureRequest.tenant_id == identity.principal.tenant_id,
        ErasureRequest.subject_user_id == identity.principal.user_id, ErasureRequest.id == request_id).execution_options(populate_existing=True)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise ResourceNotFoundError("申请不存在")
    return row


def request_summary(row: ErasureRequest) -> ErasureRequestSummary:
    _validate_scope(row)
    return ErasureRequestSummary(**{name: getattr(row, name) for name in ErasureRequestSummary.model_fields if name != "execution_enabled"})


def request_detail(row: ErasureRequest) -> ErasureRequestDetail:
    summary = request_summary(row)
    if row.preview_report is None:
        return ErasureRequestDetail(request=summary, report=None, report_digest=None, report_status="not_recorded", review_scope=None)
    try:
        report = ErasurePreviewReport.model_validate(row.preview_report)
        if (digest(row.preview_report) != row.preview_digest or report.request_id != row.id or report.scope_digest != row.scope_digest
            or report.policy_digest != row.policy_digest or _time(report.cutoff_at) != _time(row.cutoff_at)
            or [item.category for item in report.impacts] != row.categories):
            raise ValueError("report binding mismatch")
    except (ValidationError, ValueError, TypeError):
        return ErasureRequestDetail(request=summary, report=None, report_digest=None, report_status="integrity_failed", review_scope=None)
    scope = ErasureReviewScope(scope_digest=row.scope_digest, preview_digest=row.preview_digest,
        approval_scope_digest=digest({"scope": row.scope_digest, "preview": row.preview_digest, "policy": row.policy_digest}))
    return ErasureRequestDetail(request=summary, report=report, report_digest=row.preview_digest, report_status="recorded", review_scope=scope)


async def preview_request(session: AsyncSession, *, identity: IdentityContext, request_id: UUID, expected_revision: int) -> ErasureRequest:
    await _current_subject(session, identity)
    row = await get_request(session, identity=identity, request_id=request_id, lock=True)
    _validate_scope(row)
    if row.revision != expected_revision or row.status != "requested":
        raise DataGovernanceConflict("申请已变化或已有预览，请刷新；原预览不可覆盖")
    blockers = ["PREVIEW_ONLY", "POLICY_NOT_APPROVED", "LEGAL_HOLD_REVIEW_REQUIRED",
        "MAINTENANCE_IDENTITY_NOT_CONFIGURED", "DEPENDENCY_INVENTORY_REQUIRED"]
    if row.policy_id is None:
        blockers.append("POLICY_NOT_CONFIGURED")
    else:
        policy = await session.scalar(select(RetentionPolicy).where(RetentionPolicy.id == row.policy_id,
            RetentionPolicy.tenant_id == row.tenant_id))
        if policy is None or policy.policy_digest != row.policy_digest:
            raise DataGovernanceConflict("固定策略不可用，不能重绑后续草稿")
        definition = policy_definition(policy)
        rules = {rule.category: rule for rule in definition.rules}
        if any(category not in rules or rules[category].retain_days is None or rules[category].legal_hold_required is None
            or rules[category].backup_handling == "unresolved" for category in row.categories):
            blockers.append("RETENTION_RULES_UNCONFIRMED")
        if not any(reviewer != row.subject_user_id for reviewer in definition.reviewer_user_ids):
            blockers.append("INDEPENDENT_REVIEWER_NOT_CONFIGURED")
    observed_at = utc_now()
    queries = inventory_queries(tenant_id=row.tenant_id, subject_id=row.subject_user_id, cutoff=row.cutoff_at)
    impacts = [await category_impact(session, category=category, queries=queries[category]) for category in _validate_scope(row).categories]
    active, unresolved = await live_reference_counts(session, tenant_id=row.tenant_id, subject_id=row.subject_user_id)
    if active:
        blockers.append("ACTIVE_RUNS_PRESENT")
    if unresolved:
        blockers.append("UNRESOLVED_ACTIONS_PRESENT")
    report = ErasurePreviewReport(request_id=row.id, scope_digest=row.scope_digest, policy_digest=row.policy_digest,
        cutoff_at=row.cutoff_at, observed_at=observed_at, finished_at=utc_now(), impacts=impacts,
        active_run_count=active, unresolved_action_count=unresolved, blockers=blockers)
    row.preview_report = report.model_dump(mode="json")
    row.preview_digest = digest(row.preview_report)
    row.previewed_at = report.finished_at
    row.status, row.revision = "previewed", row.revision + 1
    await session.flush()
    return row


async def withdraw_request(session: AsyncSession, *, identity: IdentityContext, request_id: UUID, expected_revision: int) -> ErasureRequest:
    await _current_subject(session, identity)
    row = await get_request(session, identity=identity, request_id=request_id, lock=True)
    _validate_scope(row)
    if row.revision != expected_revision or row.status == "withdrawn":
        raise DataGovernanceConflict("申请已变化，请刷新后重试")
    row.status, row.withdrawn_at, row.revision = "withdrawn", utc_now(), row.revision + 1
    await session.flush()
    return row
