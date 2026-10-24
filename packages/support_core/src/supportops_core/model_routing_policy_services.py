"""路由策略版本控制面；不发布Agent、不解析v3或授予模型调用许可。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_services import PermissionDeniedError, require_platform_admin
from supportops_core.auth import AuthenticationError, IdentityContext, ensure_identity
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.model_routing_policy_contracts import (
    RoutingPolicyRevoke, RoutingPolicyRevocationRecord, RoutingPolicySave,
    RoutingPolicyVersionRecord, RoutingPolicyVersionView,
)
from supportops_core.model_routing_reservation_contracts import RoutingCallPlan
from supportops_core.models import (
    AgentAuditEvent, AuthSession, ModelCredential, ModelEndpoint, ModelEndpointModel,
    ModelEndpointVersion, ModelRoutingPolicyRevocation, ModelRoutingPolicyVersion, Tenant, User, utc_now,
)
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.services import ResourceNotFoundError


class RoutingPolicyConflict(ValueError):
    def __init__(self, code: str = "ROUTING_POLICY_CONFLICT") -> None:
        super().__init__("策略版本或固定候选已变化，请只读刷新原版本后重新确认")
        self.code = code


class RoutingPolicyUnavailable(RuntimeError):
    code = "ROUTING_POLICY_UNCONFIRMED"

    def __init__(self) -> None:
        super().__init__("策略操作结果暂不可确认，请只读查询原版本，勿重新保存或撤销")


class RoutingPolicySessionInvalid(AuthenticationError):
    pass


class RoutingPolicyPermissionDenied(PermissionDeniedError):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _administrator(session: AsyncSession, identity: IdentityContext, *, lock: bool) -> IdentityContext:
    """写操作沿既有治理顺序锁租户、人员和原会话，复检当前身份与期限。"""
    principal = identity.principal
    queries = (
        select(Tenant).where(Tenant.id == principal.tenant_id),
        select(User).where(User.id == principal.user_id, User.tenant_id == principal.tenant_id),
        select(AuthSession).where(AuthSession.id == principal.session_id,
            AuthSession.tenant_id == principal.tenant_id, AuthSession.user_id == principal.user_id),
    )
    if principal.session_id is None:
        raise RoutingPolicySessionInvalid("登录会话已失效，请重新登录")
    for query in queries:
        query = query.execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        if await session.scalar(query) is None:
            raise RoutingPolicySessionInvalid("登录会话已失效，请重新登录")
    try:
        current = await ensure_identity(session, principal, require_session=True)
    except AuthenticationError:
        raise RoutingPolicySessionInvalid("登录会话已失效，请重新登录") from None
    try:
        require_platform_admin(current)
    except PermissionDeniedError:
        raise RoutingPolicyPermissionDenied("需要平台管理员权限") from None
    return current


async def require_routing_administrator(session: AsyncSession, identity: IdentityContext, *, lock: bool = False) -> IdentityContext:
    """共享当前管理员复检；写候选显式锁定身份，仍不授予路由执行许可。"""
    return await _administrator(session, identity, lock=lock)


async def read_routing_policy_version_by_id(session: AsyncSession, identity: IdentityContext, *,
    version_id: UUID,
) -> RoutingPolicyVersionView:
    await require_routing_administrator(session, identity)
    row = await session.scalar(select(ModelRoutingPolicyVersion).where(
        ModelRoutingPolicyVersion.tenant_id == identity.principal.tenant_id,
        ModelRoutingPolicyVersion.id == version_id))
    if row is None:
        raise ResourceNotFoundError("策略版本不存在")
    return await read_routing_policy_version(session, identity, policy_id=row.policy_id, version_id=version_id)


def _record(row: ModelRoutingPolicyVersion) -> RoutingPolicyVersionRecord:
    try:
        record = RoutingPolicyVersionRecord.model_validate(row.payload)
        if (record.model_dump(mode="json") != row.payload
            or manifest_digest(row.payload) != row.policy_digest
            or record.version_id != row.id or record.tenant_id != row.tenant_id
            or record.policy_id != row.policy_id or record.revision != row.revision
            or record.previous_version_id != row.previous_version_id
            or record.previous_policy_digest != row.previous_policy_digest
            or record.created_by != row.created_by or record.created_at != _utc(row.created_at)
            or record.definition.valid_until != _utc(row.valid_until)
            or list(record.definition.candidates) != sorted(record.definition.candidates,
                key=lambda candidate: (candidate.priority, str(candidate.candidate_id)))):
            raise ValueError()
        return record
    except (TypeError, ValueError):
        raise RoutingPolicyConflict("ROUTING_POLICY_HISTORY_INVALID") from None


async def _views(session: AsyncSession, rows: list[ModelRoutingPolicyVersion]) -> list[RoutingPolicyVersionView]:
    if not rows:
        return []
    tenant_id = rows[0].tenant_id
    parents = {row.id: row for row in rows}
    missing = {row.previous_version_id for row in rows if row.previous_version_id is not None} - parents.keys()
    if missing:
        parents.update({row.id: row for row in (await session.scalars(select(ModelRoutingPolicyVersion).where(
            ModelRoutingPolicyVersion.tenant_id == tenant_id, ModelRoutingPolicyVersion.id.in_(missing)))).all()})
    revocations = {row.policy_version_id: row for row in (await session.scalars(select(ModelRoutingPolicyRevocation).where(
        ModelRoutingPolicyRevocation.tenant_id == tenant_id,
        ModelRoutingPolicyRevocation.policy_version_id.in_([row.id for row in rows])))).all()}
    results = []
    for row in rows:
        record = _record(row)
        if record.previous_version_id is not None:
            parent = parents.get(record.previous_version_id)
            if parent is None:
                raise RoutingPolicyConflict("ROUTING_POLICY_HISTORY_INVALID")
            previous = _record(parent)
            if (previous.tenant_id != record.tenant_id or previous.policy_id != record.policy_id
                or previous.revision + 1 != record.revision or parent.policy_digest != record.previous_policy_digest):
                raise RoutingPolicyConflict("ROUTING_POLICY_HISTORY_INVALID")
        revoked = revocations.get(row.id)
        revocation = None
        if revoked is not None:
            try:
                revocation = RoutingPolicyRevocationRecord.model_validate(revoked.payload)
                if (revocation.model_dump(mode="json") != revoked.payload
                    or manifest_digest(revoked.payload) != revoked.record_digest
                    or revocation.revocation_id != revoked.id or revocation.tenant_id != tenant_id
                    or revocation.policy_id != record.policy_id or revocation.version_id != row.id
                    or revoked.policy_id != record.policy_id or revoked.policy_digest != row.policy_digest
                    or revocation.policy_digest != row.policy_digest or revocation.revoked_by != revoked.revoked_by
                    or revocation.revoked_at != _utc(revoked.revoked_at) or revocation.revoked_at < record.created_at):
                    raise ValueError()
            except (TypeError, ValueError):
                raise RoutingPolicyConflict("ROUTING_POLICY_HISTORY_INVALID") from None
        status = "revoked" if revocation else "expired" if record.definition.valid_until <= utc_now() else "candidate"
        results.append(RoutingPolicyVersionView(record=record, policy_digest=row.policy_digest,
            status=status, revocation=revocation, revocation_digest=revoked.record_digest if revoked else None))
    return results


async def _scoped(session: AsyncSession, *, tenant_id: UUID, policy_id: UUID, version_id: UUID) -> ModelRoutingPolicyVersion:
    row = await session.scalar(select(ModelRoutingPolicyVersion).where(
        ModelRoutingPolicyVersion.tenant_id == tenant_id, ModelRoutingPolicyVersion.policy_id == policy_id,
        ModelRoutingPolicyVersion.id == version_id).execution_options(populate_existing=True))
    if row is None:
        raise ResourceNotFoundError("策略版本不存在")
    return row


async def read_routing_policy_version(session: AsyncSession, identity: IdentityContext, *,
    policy_id: UUID, version_id: UUID,
) -> RoutingPolicyVersionView:
    await _administrator(session, identity, lock=False)
    row = await _scoped(session, tenant_id=identity.principal.tenant_id, policy_id=policy_id, version_id=version_id)
    result = (await _views(session, [row]))[0]
    await _administrator(session, identity, lock=False)
    return result


async def assert_routing_policy_plan(session: AsyncSession, *, plan: RoutingCallPlan) -> None:
    """内部当前范围复核；可信调用者提供租户，原政策仍需独立批准与发布解析。"""
    row = await session.scalar(select(ModelRoutingPolicyVersion).where(
        ModelRoutingPolicyVersion.tenant_id == plan.requirements.tenant_id,
        ModelRoutingPolicyVersion.id == plan.requirements.policy_version_id).execution_options(populate_existing=True))
    if row is None:
        raise RoutingPolicyConflict("ROUTING_POLICY_UNAVAILABLE")
    view = (await _views(session, [row]))[0]
    definition = view.record.definition
    if (view.status != "candidate" or view.policy_digest != plan.requirements.policy_digest
        or definition.valid_until <= utc_now() or definition.valid_until < plan.expires_at
        or tuple(sorted(plan.candidates, key=lambda item: (item.priority, str(item.candidate_id)))) != definition.candidates
        or not set(plan.requirements.allowed_regions) <= set(definition.allowed_regions)
        or plan.requirements.data_classification not in definition.allowed_data_classifications
        or plan.purpose not in definition.allowed_purposes):
        raise RoutingPolicyConflict("ROUTING_POLICY_SCOPE_MISMATCH")


async def list_routing_policy_versions(session: AsyncSession, identity: IdentityContext, *,
    page_size: int, offset: int, policy_id: UUID | None = None,
) -> tuple[list[RoutingPolicyVersionView], int]:
    await _administrator(session, identity, lock=False)
    if not 1 <= page_size <= 100 or offset < 0:
        raise RoutingPolicyConflict()
    filters = [ModelRoutingPolicyVersion.tenant_id == identity.principal.tenant_id]
    if policy_id is not None:
        filters.append(ModelRoutingPolicyVersion.policy_id == policy_id)
    total = int(await session.scalar(select(func.count()).select_from(ModelRoutingPolicyVersion).where(*filters)) or 0)
    rows = list((await session.scalars(select(ModelRoutingPolicyVersion).where(*filters)
        .order_by(ModelRoutingPolicyVersion.created_at.desc(), ModelRoutingPolicyVersion.id.desc())
        .offset(offset).limit(page_size))).all())
    results = await _views(session, rows)
    await _administrator(session, identity, lock=False)
    return results, total


async def _candidate_scope(session: AsyncSession, *, tenant_id: UUID, command: RoutingPolicySave) -> None:
    """仅核对精确资源归属；探测、价格和独立数据批准仍须发布/逐调用门禁。"""
    candidates = command.definition.candidates
    rows = (await session.execute(select(ModelEndpointVersion.id, ModelEndpointVersion.endpoint_id,
        ModelEndpointVersion.endpoint_model_id, ModelCredential.revision)
        .select_from(ModelEndpointVersion)
        .join(ModelEndpoint, ModelEndpoint.id == ModelEndpointVersion.endpoint_id)
        .join(ModelEndpointModel, ModelEndpointModel.id == ModelEndpointVersion.endpoint_model_id)
        .join(ModelCredential, ModelCredential.id == ModelEndpointVersion.credential_id)
        .where(ModelEndpointVersion.tenant_id == tenant_id, ModelEndpoint.tenant_id == tenant_id,
            ModelEndpointModel.tenant_id == tenant_id, ModelCredential.tenant_id == tenant_id,
            ModelEndpointModel.endpoint_id == ModelEndpoint.id, ModelCredential.endpoint_id == ModelEndpoint.id,
            ModelEndpointVersion.id.in_([row.model_version_id for row in candidates]),
            ModelEndpointVersion.deleted_at.is_(None), ModelEndpoint.deleted_at.is_(None),
            ModelEndpointModel.deleted_at.is_(None), ModelCredential.deleted_at.is_(None)))).all()
    references = {row.id: row for row in rows}
    for candidate in candidates:
        row = references.get(candidate.model_version_id)
        if (row is None or row.endpoint_id != candidate.endpoint_id or row.endpoint_model_id != candidate.endpoint_model_id
            or row.revision != candidate.credential_revision):
            raise RoutingPolicyConflict("ROUTING_POLICY_CANDIDATE_INVALID")


def _audit(session: AsyncSession, *, identity: IdentityContext, row: ModelRoutingPolicyVersion,
    action: AuditAction, correlation_id: str, revocation_digest: str | None = None,
) -> None:
    session.add(AgentAuditEvent(tenant_id=row.tenant_id, resource_type=AuditResourceType.MODEL_ROUTING_POLICY,
        action=action, actor_user_id=identity.principal.user_id, version_id=row.id, correlation_id=correlation_id,
        metadata_payload={"policy_id": str(row.policy_id), "version_id": str(row.id), "revision": row.revision,
            "policy_digest": row.policy_digest, "revocation_digest": revocation_digest, "routing_enabled": False}))


async def save_routing_policy_version(session: AsyncSession, identity: IdentityContext, *,
    policy_id: UUID, command: RoutingPolicySave, correlation_id: str,
) -> RoutingPolicyVersionView:
    current = await _administrator(session, identity, lock=True)
    tenant_id = current.principal.tenant_id
    definition = command.definition.model_copy(update={"candidates": tuple(sorted(command.definition.candidates,
        key=lambda candidate: (candidate.priority, str(candidate.candidate_id))))})
    existing = await session.scalar(select(ModelRoutingPolicyVersion).where(ModelRoutingPolicyVersion.id == command.version_id))
    if existing is not None:
        if existing.tenant_id != tenant_id or existing.policy_id != policy_id:
            raise RoutingPolicyConflict()
        view = (await _views(session, [existing]))[0]
        if (view.record.created_by != current.principal.user_id or view.record.revision != command.expected_revision + 1
            or view.record.previous_policy_digest != command.expected_policy_digest or view.record.definition != definition):
            raise RoutingPolicyConflict()
        return view
    latest = await session.scalar(select(ModelRoutingPolicyVersion).where(ModelRoutingPolicyVersion.tenant_id == tenant_id,
        ModelRoutingPolicyVersion.policy_id == policy_id).order_by(ModelRoutingPolicyVersion.revision.desc()).limit(1))
    if latest is not None:
        await _views(session, [latest])
    if ((latest.revision if latest else 0) != command.expected_revision
        or (latest.policy_digest if latest else None) != command.expected_policy_digest):
        raise RoutingPolicyConflict()
    if definition.valid_until <= utc_now():
        raise RoutingPolicyConflict("ROUTING_POLICY_EXPIRED")
    await _candidate_scope(session, tenant_id=tenant_id, command=command)
    recorded_at = utc_now()
    if definition.valid_until <= recorded_at:
        raise RoutingPolicyConflict("ROUTING_POLICY_EXPIRED")
    record = RoutingPolicyVersionRecord(version_id=command.version_id, tenant_id=tenant_id, policy_id=policy_id,
        revision=command.expected_revision + 1, previous_version_id=latest.id if latest else None,
        previous_policy_digest=command.expected_policy_digest, created_by=current.principal.user_id,
        created_at=recorded_at, definition=definition)
    payload = record.model_dump(mode="json")
    row = ModelRoutingPolicyVersion(id=record.version_id, tenant_id=tenant_id, policy_id=policy_id,
        revision=record.revision, previous_version_id=record.previous_version_id,
        previous_policy_digest=record.previous_policy_digest, policy_digest=manifest_digest(payload), payload=payload,
        created_by=record.created_by, created_at=record.created_at, valid_until=definition.valid_until)
    session.add(row)
    _audit(session, identity=current, row=row, action=AuditAction.MODEL_ROUTING_POLICY_SAVED, correlation_id=correlation_id)
    await session.flush()
    result = (await _views(session, [row]))[0]
    await _administrator(session, current, lock=True)
    return result


async def revoke_routing_policy_version(session: AsyncSession, identity: IdentityContext, *,
    policy_id: UUID, version_id: UUID, command: RoutingPolicyRevoke, correlation_id: str,
) -> RoutingPolicyVersionView:
    current = await _administrator(session, identity, lock=True)
    row = await _scoped(session, tenant_id=current.principal.tenant_id, policy_id=policy_id, version_id=version_id)
    view = (await _views(session, [row]))[0]
    if view.policy_digest != command.expected_policy_digest:
        raise RoutingPolicyConflict()
    if view.revocation is not None:
        if view.revocation.reason != command.reason:
            raise RoutingPolicyConflict()
        return view
    record = RoutingPolicyRevocationRecord(revocation_id=uuid4(), tenant_id=row.tenant_id, policy_id=policy_id,
        version_id=row.id, policy_digest=row.policy_digest, revoked_by=current.principal.user_id,
        revoked_at=utc_now(), reason=command.reason)
    payload = record.model_dump(mode="json")
    digest = manifest_digest(payload)
    session.add(ModelRoutingPolicyRevocation(id=record.revocation_id, tenant_id=row.tenant_id, policy_id=policy_id,
        policy_version_id=row.id, policy_digest=row.policy_digest, record_digest=digest, payload=payload,
        revoked_by=record.revoked_by, revoked_at=record.revoked_at))
    _audit(session, identity=current, row=row, action=AuditAction.MODEL_ROUTING_POLICY_REVOKED,
        correlation_id=correlation_id, revocation_digest=digest)
    await session.flush()
    result = (await _views(session, [row]))[0]
    await _administrator(session, current, lock=True)
    return result
