"""本地有限清单的导入持久化服务；无默认批准策略、来源读取或后台入口。"""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal, cast, get_args
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from supportops_core.agent_services import PermissionDeniedError, require_platform_admin
from supportops_core.auth import AuthenticationError, IdentityContext, IdentityNotProvisionedError, ensure_identity
from supportops_core.enums import (
    AuditAction, AuditResourceType, KnowledgeDocumentStatus, KnowledgeVersionStatus,
)
from supportops_core.knowledge_content import knowledge_content_digest, normalize_knowledge_markdown
from supportops_core.knowledge_ingestion_contracts import (
    INGESTION_MANUAL_WINDOW_SECONDS, INGESTION_TRANSACTION_TIMEOUT_SECONDS,
    Digest, IngestionContract, IngestionCounts, IngestionCursor, IngestionFailure, IngestionJobDefinition,
    IngestionJobState, IngestionObjectReceipt, IngestionPageReceipt, IngestionScope, ingestion_draft_identity,
    ingestion_page_key, ingestion_page_result_digest, ingestion_scope_digest,
    IngestionCreationStatus, IngestionJobView, PersistedIngestionStatus, validate_ingestion_page, validate_ingestion_state,
)
from supportops_core.knowledge_markdown_source import (
    LOCAL_MARKDOWN_IDENTITY_POLICY_DIGEST, supports_local_markdown_implementation,
)
from supportops_core.knowledge_services import (
    _acl_digest, append_knowledge_audit, create_knowledge_document, create_knowledge_version,
    lock_knowledge_document,
)
from supportops_core.models import (
    AuthSession, KnowledgeDocument, KnowledgeIngestionCreationStop, KnowledgeIngestionJob, KnowledgeIngestionMaterialization,
    KnowledgeIngestionObject, KnowledgeIngestionPage, KnowledgeSource, KnowledgeVersion,
    Tenant, User, utc_now,
)
from supportops_core.services import ResourceNotFoundError


class IngestionConflict(ValueError):
    code = "KNOWLEDGE_INGESTION_CONFLICT"


class IngestionNotApproved(PermissionDeniedError):
    code = "KNOWLEDGE_INGESTION_NOT_APPROVED"


class IngestionAuthenticationInvalid(AuthenticationError):
    code = "AUTH_SESSION_INVALID"


class IngestionCreationStopUncertain(IngestionConflict):
    code = "KNOWLEDGE_INGESTION_EXECUTION_UNCERTAIN"


class IngestionTargetChanged(IngestionConflict):
    """读取前的目标观察已变化，本页尚未物化任何草稿。"""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def ingestion_authorization_digest(*, tenant_id: UUID, source_id: UUID, requested_by: UUID,
    document_owner_id: UUID, allowed_object_keys: tuple[str, ...], source_acl_digest: str,
    target_acl_digest: str, valid_until: datetime,
) -> str:
    if valid_until.tzinfo is None:
        raise IngestionConflict("来源批准期限必须包含时区")
    return _digest(["knowledge_ingestion_authorization_v1", str(tenant_id), str(source_id),
        str(requested_by), str(document_owner_id), sorted(allowed_object_keys), source_acl_digest,
        target_acl_digest, valid_until.astimezone(UTC).isoformat()])


class IngestionWritePolicy(IngestionContract):
    """只允许可信服务端配置供给，不得从HTTP/工具/模型参数构造或当作审批接口。"""

    scope: IngestionScope
    requested_by: UUID
    document_owner_id: UUID
    allowed_object_keys: tuple[Digest, ...] = Field(min_length=1, max_length=100)
    source_acl_digest: Digest
    target_acl_digest: Digest
    valid_until: datetime

    @model_validator(mode="after")
    def validate_policy(self) -> "IngestionWritePolicy":
        if self.valid_until.tzinfo is None or len(set(self.allowed_object_keys)) != len(self.allowed_object_keys):
            raise ValueError("来源策略必须有固定时区期限和唯一对象清单")
        if self.scope.authorization_scope_digest != self.authorization_digest():
            raise ValueError("来源批准范围未绑定申请人、负责人、对象和ACL映射")
        return self

    def authorization_digest(self) -> str:
        return ingestion_authorization_digest(tenant_id=self.scope.tenant_id, source_id=self.scope.source_id,
            requested_by=self.requested_by, document_owner_id=self.document_owner_id,
            allowed_object_keys=self.allowed_object_keys, source_acl_digest=self.source_acl_digest,
            target_acl_digest=self.target_acl_digest, valid_until=self.valid_until)


class IngestionDraftCandidate(IngestionContract):
    """仅接可信适配器输出；正文不保存到任务/页回执，也不代表内容已批准。"""

    object_key: Digest
    raw_content_digest: Digest
    parsed_content_digest: Digest
    source_revision_digest: Digest | None = None
    title: str = Field(min_length=1, max_length=500, repr=False)
    content_markdown: str = Field(min_length=1, max_length=1_048_576, repr=False)


async def set_ingestion_transaction_limits(session: AsyncSession) -> None:
    if session.get_bind().dialect.name == "postgresql":
        # 维护调用使用独立业务事务，限制等待在线文档/身份锁的时间。
        await session.execute(text("SET LOCAL lock_timeout = '1s'"))
        await session.execute(text("SET LOCAL statement_timeout = '5s'"))


async def _require_ingestion_fencing_database(session: AsyncSession) -> None:
    if session.get_bind().dialect.name != "postgresql":
        raise IngestionConflict("导入创建、执行和请求封锁需要PostgreSQL，当前数据库不支持该操作")
    connection = await session.connection()
    if await connection.get_isolation_level() != "READ COMMITTED":
        raise IngestionConflict("导入创建、执行和请求封锁需要READ COMMITTED事务")


async def _current_admin(session: AsyncSession, identity: IdentityContext) -> IdentityContext:
    await set_ingestion_transaction_limits(session)
    await session.scalar(select(User).where(User.id == identity.principal.user_id,
        User.tenant_id == identity.principal.tenant_id).execution_options(populate_existing=True).with_for_update())
    await session.get(Tenant, identity.principal.tenant_id, populate_existing=True)
    if identity.principal.session_id is not None:
        await session.get(AuthSession, identity.principal.session_id, populate_existing=True)
    try:
        current = await ensure_identity(session, identity.principal, require_session=True)
    except (AuthenticationError, IdentityNotProvisionedError):
        raise IngestionAuthenticationInvalid("登录会话已失效，请重新登录") from None
    require_platform_admin(current)
    return current


async def _approved_source(session: AsyncSession, *, identity: IdentityContext,
    scope: IngestionScope, policy: IngestionWritePolicy | None,
) -> KnowledgeSource:
    if (policy is None or policy.scope != scope or policy.requested_by != identity.principal.user_id
        or scope.tenant_id != identity.principal.tenant_id or policy.valid_until <= utc_now()
        or scope.identity_policy_digest != LOCAL_MARKDOWN_IDENTITY_POLICY_DIGEST
        or not supports_local_markdown_implementation(scope.adapter_manifest_digest, scope.parser_manifest_digest)):
        raise IngestionNotApproved("没有当前有效的服务端来源批准策略")
    source = await session.scalar(select(KnowledgeSource).where(KnowledgeSource.id == scope.source_id,
        KnowledgeSource.tenant_id == scope.tenant_id).execution_options(populate_existing=True).with_for_update())
    # 负责人有效状态保持到当前事务结束；NOWAIT避免与已持申请人锁形成交叉等待。
    try:
        owner = await session.scalar(select(User.id).where(User.id == policy.document_owner_id,
            User.tenant_id == scope.tenant_id, User.status == "active").with_for_update(nowait=True))
    except SQLAlchemyError:
        raise IngestionConflict("来源负责人当前状态暂时无法确认，本次操作未继续") from None
    if (source is None or source.source_type != "local_markdown"
        or source.owner_user_id != policy.document_owner_id or owner is None):
        raise IngestionNotApproved("来源类型、负责人或当前批准范围不可用")
    conflicting = await session.scalar(select(KnowledgeIngestionObject.id).where(
        KnowledgeIngestionObject.tenant_id == scope.tenant_id,
        KnowledgeIngestionObject.source_id == scope.source_id,
        KnowledgeIngestionObject.identity_policy_digest != scope.identity_policy_digest).limit(1))
    if conflicting is not None:
        raise IngestionConflict("来源身份策略变化需要独立映射复核")
    return source


def _job_contracts(row: KnowledgeIngestionJob) -> tuple[IngestionJobDefinition, IngestionJobState]:
    try:
        definition = IngestionJobDefinition.model_validate_json(json.dumps(row.definition))
        state = IngestionJobState.model_validate_json(json.dumps(row.state))
        validate_ingestion_state(definition, state)
        if (definition.id != row.id or definition.scope.tenant_id != row.tenant_id
            or definition.scope.source_id != row.source_id or definition.requested_by != row.requested_by
            or definition.created_at != _utc(row.created_at) or definition.deadline_at != _utc(row.deadline_at)
            or _digest(row.definition) != row.definition_digest or _digest(row.state) != row.state_digest
            or (state.status, state.revision, state.generation, state.attempts, state.owner_id,
                state.lease_expires_at) != (row.status, row.revision, row.generation, row.attempts,
                row.owner_id, _utc(row.lease_expires_at)) or definition.max_pages != 1):
            raise ValueError("binding mismatch")
        return definition, state
    except (ValueError, TypeError):
        raise IngestionConflict("任务定义或执行状态完整性校验失败") from None


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _store_state(row: KnowledgeIngestionJob, definition: IngestionJobDefinition,
    state: IngestionJobState,
) -> None:
    validate_ingestion_state(definition, state)
    row.state = state.model_dump(mode="json")
    row.state_digest = _digest(row.state)
    row.status, row.revision, row.generation = state.status, state.revision, state.generation
    row.attempts, row.owner_id, row.lease_expires_at = state.attempts, state.owner_id, state.lease_expires_at


async def _audit(session: AsyncSession, row: KnowledgeIngestionJob, action: AuditAction,
    correlation_id: str,
) -> None:
    await append_knowledge_audit(session, tenant_id=row.tenant_id, actor_user_id=row.requested_by,
        resource_type=AuditResourceType.KNOWLEDGE_SOURCE, action=action, correlation_id=correlation_id,
        metadata={"ingestion_job_id": str(row.id), "source_id": str(row.source_id),
            "status": row.status, "revision": row.revision, "generation": row.generation})


async def create_ingestion_job(session: AsyncSession, *, identity: IdentityContext,
    scope: IngestionScope, idempotency_key: str, policy: IngestionWritePolicy | None = None,
    window_seconds: int = INGESTION_MANUAL_WINDOW_SECONDS, max_attempts: int = 1, correlation_id: str,
) -> KnowledgeIngestionJob:
    current = await _current_admin(session, identity)
    await _require_ingestion_fencing_database(session)
    await _approved_source(session, identity=current, scope=scope, policy=policy)
    if (not isinstance(idempotency_key, str) or not 0 < len(idempotency_key) <= 200
        or idempotency_key != idempotency_key.strip()
        or any(ord(char) < 32 for char in idempotency_key)
        or type(window_seconds) is not int or not 1 <= window_seconds <= 86400
        or type(max_attempts) is not int or not 1 <= max_attempts <= 3):
        raise IngestionConflict("任务幂等键或固定预算无效")
    if await _find_creation_stop(session, identity=current, request_key=idempotency_key) is not None:
        raise IngestionConflict("该创建请求已由本人永久封锁，不能创建或复用新任务")
    request_digest = _digest(["knowledge_ingestion_request_v1", scope.model_dump(mode="json"),
        window_seconds, max_attempts])
    previous = await session.scalar(select(KnowledgeIngestionJob).where(
        KnowledgeIngestionJob.tenant_id == current.principal.tenant_id,
        KnowledgeIngestionJob.requested_by == current.principal.user_id,
        KnowledgeIngestionJob.idempotency_key == idempotency_key).execution_options(populate_existing=True))
    if previous is not None:
        _job_contracts(previous)
        if previous.request_digest != request_digest:
            raise IngestionConflict("同一幂等键已用于其他导入范围或预算")
        return previous
    now = utc_now()
    deadline = now + timedelta(seconds=window_seconds)
    if policy is None or deadline > policy.valid_until:
        raise IngestionNotApproved("任务窗口超过来源批准期限")
    definition = IngestionJobDefinition(id=uuid4(), scope=scope, requested_by=current.principal.user_id,
        created_at=now, deadline_at=deadline, initial_cursor=IngestionCursor(kind="initial", page_sequence=0),
        max_attempts=max_attempts, max_pages=1, max_objects_per_page=len(policy.allowed_object_keys))
    row = KnowledgeIngestionJob(id=definition.id, tenant_id=scope.tenant_id, source_id=scope.source_id,
        requested_by=current.principal.user_id, idempotency_key=idempotency_key, request_digest=request_digest,
        definition=definition.model_dump(mode="json"), created_at=now, deadline_at=deadline)
    row.definition_digest = _digest(row.definition)
    _store_state(row, definition, IngestionJobState(job_id=row.id, scope_digest=ingestion_scope_digest(scope),
        status="queued", revision=1, generation=0, attempts=0, cursor=definition.initial_cursor))
    session.add(row)
    await session.flush()
    await _audit(session, row, AuditAction.KNOWLEDGE_INGESTION_CREATED, correlation_id)
    return row


async def get_ingestion_job(session: AsyncSession, *, identity: IdentityContext,
    job_id: UUID,
) -> KnowledgeIngestionJob:
    current = await _current_admin(session, identity)
    row = await session.scalar(select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == job_id,
        KnowledgeIngestionJob.tenant_id == current.principal.tenant_id,
        KnowledgeIngestionJob.requested_by == current.principal.user_id).execution_options(populate_existing=True))
    if row is None:
        raise ResourceNotFoundError("导入任务不存在")
    _job_contracts(row)
    return row


async def check_ingestion_source(session: AsyncSession, *, identity: IdentityContext,
    policy: IngestionWritePolicy,
) -> KnowledgeSource:
    current = await _current_admin(session, identity)
    await _require_ingestion_fencing_database(session)
    return await _approved_source(session, identity=current, scope=policy.scope, policy=policy)


def _creation_stop_digest(row: KnowledgeIngestionCreationStop) -> str:
    recorded_at = _utc(row.created_at)
    if recorded_at is None:
        raise ValueError("停止事实缺少原始记录时间")
    return _digest(["knowledge_ingestion_creation_stop_v1", str(row.id), str(row.tenant_id),
        str(row.requested_by), row.request_key, recorded_at.isoformat()])


async def _find_creation_stop(session: AsyncSession, *, identity: IdentityContext,
    request_key: str,
) -> KnowledgeIngestionCreationStop | None:
    row = await session.scalar(select(KnowledgeIngestionCreationStop).where(
        KnowledgeIngestionCreationStop.tenant_id == identity.principal.tenant_id,
        KnowledgeIngestionCreationStop.requested_by == identity.principal.user_id,
        KnowledgeIngestionCreationStop.request_key == request_key).execution_options(populate_existing=True))
    if row is not None:
        try:
            if row.record_digest != _creation_stop_digest(row):
                raise ValueError("stop integrity mismatch")
        except (ValueError, TypeError, AttributeError):
            raise IngestionConflict("创建停止事实完整性无法确认，请保留原请求核对") from None
    return row


async def _creation_status_locked(session: AsyncSession, *, identity: IdentityContext,
    request_key: UUID,
) -> IngestionCreationStatus:
    stopped = await _find_creation_stop(session, identity=identity, request_key=str(request_key))
    job = await session.scalar(select(KnowledgeIngestionJob).where(
        KnowledgeIngestionJob.tenant_id == identity.principal.tenant_id,
        KnowledgeIngestionJob.requested_by == identity.principal.user_id,
        KnowledgeIngestionJob.idempotency_key == str(request_key)).execution_options(populate_existing=True))
    if job is not None and stopped is not None:
        raise IngestionConflict("同一创建请求出现冲突历史，请保留原请求核对")
    return IngestionCreationStatus(request_key=request_key,
        status="recorded" if job is not None else "stopped" if stopped is not None else "unobserved",
        job=ingestion_job_view(job) if job is not None else None,
        stopped_at=_utc(stopped.created_at) if stopped is not None else None, observed_at=utc_now())


async def read_ingestion_creation_status(session: AsyncSession, *, identity: IdentityContext,
    request_key: UUID,
) -> IngestionCreationStatus:
    current = await _current_admin(session, identity)
    return await _creation_status_locked(session, identity=current, request_key=request_key)


async def stop_ingestion_creation_request(session: AsyncSession, *, identity: IdentityContext,
    request_key: UUID, correlation_id: str,
) -> IngestionCreationStatus:
    """同当前本人锁下检查/封锁创建；已生成任务只返回，不取消、不读取来源。"""
    current = await _current_admin(session, identity)
    await _require_ingestion_fencing_database(session)
    state = await _creation_status_locked(session, identity=current, request_key=request_key)
    if state.status != "unobserved":
        return state
    row = KnowledgeIngestionCreationStop(id=uuid4(), tenant_id=current.principal.tenant_id,
        requested_by=current.principal.user_id, request_key=str(request_key), created_at=utc_now())
    row.record_digest = _creation_stop_digest(row)
    session.add(row)
    await session.flush()
    await append_knowledge_audit(session, tenant_id=row.tenant_id, actor_user_id=row.requested_by,
        resource_type=AuditResourceType.KNOWLEDGE_SOURCE,
        action=AuditAction.KNOWLEDGE_INGESTION_REQUEST_STOPPED, correlation_id=correlation_id,
        metadata={"request_key": row.request_key, "creation_stop_id": str(row.id), "record_digest": row.record_digest})
    await _current_admin(session, identity)
    result = await _creation_status_locked(session, identity=current, request_key=request_key)
    if result.status != "stopped":
        raise IngestionConflict("创建停止事实未能核对，当前事务已拒绝")
    return result


async def read_ingestion_job_by_request(session: AsyncSession, *, identity: IdentityContext,
    request_key: str,
) -> IngestionJobView:
    current = await _current_admin(session, identity)
    row = await session.scalar(select(KnowledgeIngestionJob).where(
        KnowledgeIngestionJob.tenant_id == current.principal.tenant_id,
        KnowledgeIngestionJob.requested_by == current.principal.user_id,
        KnowledgeIngestionJob.idempotency_key == request_key))
    if row is None:
        # 此时未观察到记录，不等于此前在途事务一定没有提交。
        raise ResourceNotFoundError("导入任务不存在")
    return ingestion_job_view(row)


def ingestion_job_view(row: KnowledgeIngestionJob, *, source_name: str | None = None) -> IngestionJobView:
    """GET只投影既有事实和当前操作条件，不写回失败或修复损坏快照。"""
    try:
        definition, state = _job_contracts(row)
        if state.status not in get_args(PersistedIngestionStatus):
            raise IngestionConflict("当前版本不支持该任务状态")
    except IngestionConflict:
        return IngestionJobView(id=row.id)
    # 仅返回当前API实际使用的规范UUID，不暴露旧内部任意字符串键或改变原键语义。
    creation_request_key = None
    if isinstance(row.idempotency_key, str):
        try:
            parsed_key = UUID(row.idempotency_key)
            if str(parsed_key) == row.idempotency_key:
                creation_request_key = parsed_key
        except ValueError:
            pass
    now = utc_now()
    terminal = state.status in {"completed", "failed", "cancelled"}
    exhausted = (state.status == "running" and state.lease_expires_at is not None
        and state.lease_expires_at <= now and state.attempts >= definition.max_attempts)
    return IngestionJobView(id=row.id, creation_request_key=creation_request_key,
        integrity_status="recorded", status=cast(PersistedIngestionStatus, state.status),
        source_id=definition.scope.source_id, scope_digest=ingestion_scope_digest(definition.scope),
        source_name=source_name, revision=state.revision,
        generation=state.generation, attempts=state.attempts, max_attempts=definition.max_attempts,
        created_at=definition.created_at, deadline_at=definition.deadline_at,
        lease_expires_at=state.lease_expires_at, retry_at=state.retry_at, failure_code=state.failure_code,
        checkpoint_kind=state.cursor.kind, committed_pages=state.cursor.page_sequence,
        max_pages=definition.max_pages, max_objects_per_page=definition.max_objects_per_page,
        counts=IngestionCounts(draft_created=state.draft_created_count, unchanged=state.unchanged_count,
            source_removed=state.source_removed_count, rejected=state.rejected_count,
            quarantined=state.quarantined_count), cancel_available=not terminal,
        expire_available=not terminal and (definition.deadline_at <= now or exhausted))


async def list_ingestion_job_views(session: AsyncSession, *, identity: IdentityContext,
    page_size: int, offset: int, status: PersistedIngestionStatus | None = None,
    source_id: UUID | None = None,
) -> tuple[list[IngestionJobView], int]:
    current = await _current_admin(session, identity)
    if (type(page_size) is not int or not 1 <= page_size <= 100 or type(offset) is not int or offset < 0
        or (status is not None and status not in get_args(PersistedIngestionStatus))):
        raise IngestionConflict("导入任务分页或筛选无效")
    filters = [KnowledgeIngestionJob.tenant_id == current.principal.tenant_id,
        KnowledgeIngestionJob.requested_by == current.principal.user_id]
    if status is not None:
        filters.append(KnowledgeIngestionJob.status == status)
    if source_id is not None:
        filters.append(KnowledgeIngestionJob.source_id == source_id)
    total = int(await session.scalar(select(func.count()).select_from(KnowledgeIngestionJob).where(*filters)) or 0)
    rows = (await session.execute(select(KnowledgeIngestionJob, KnowledgeSource.name)
        .outerjoin(KnowledgeSource, (KnowledgeSource.id == KnowledgeIngestionJob.source_id)
            & (KnowledgeSource.tenant_id == KnowledgeIngestionJob.tenant_id))
        .where(*filters).order_by(KnowledgeIngestionJob.created_at.desc(), KnowledgeIngestionJob.id.desc())
        .offset(offset).limit(page_size).execution_options(populate_existing=True))).all()
    return [ingestion_job_view(row, source_name=name) for row, name in rows], total


async def read_ingestion_job_view(session: AsyncSession, *, identity: IdentityContext,
    job_id: UUID,
) -> IngestionJobView:
    current = await _current_admin(session, identity)
    result = (await session.execute(select(KnowledgeIngestionJob, KnowledgeSource.name)
        .outerjoin(KnowledgeSource, (KnowledgeSource.id == KnowledgeIngestionJob.source_id)
            & (KnowledgeSource.tenant_id == KnowledgeIngestionJob.tenant_id))
        .where(KnowledgeIngestionJob.id == job_id, KnowledgeIngestionJob.tenant_id == current.principal.tenant_id,
            KnowledgeIngestionJob.requested_by == current.principal.user_id)
        .execution_options(populate_existing=True))).one_or_none()
    if result is None:
        raise ResourceNotFoundError("导入任务不存在")
    row, name = result
    return ingestion_job_view(row, source_name=name)


async def _locked_job(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    policy: IngestionWritePolicy | None,
) -> tuple[KnowledgeIngestionJob, IngestionJobDefinition, IngestionJobState]:
    row = await get_ingestion_job(session, identity=identity, job_id=job_id)
    await _require_ingestion_fencing_database(session)
    definition, _ = _job_contracts(row)
    await _approved_source(session, identity=identity, scope=definition.scope, policy=policy)
    row = await session.scalar(select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == job_id,
        KnowledgeIngestionJob.tenant_id == identity.principal.tenant_id,
        KnowledgeIngestionJob.requested_by == identity.principal.user_id)
        .execution_options(populate_existing=True).with_for_update())
    if row is None:
        raise ResourceNotFoundError("导入任务不存在")
    definition, state = _job_contracts(row)
    return row, definition, state


def _owned(definition: IngestionJobDefinition, state: IngestionJobState, *, owner_id: UUID,
    generation: int, expected_revision: int,
) -> None:
    now = utc_now()
    if (type(generation) is not int or type(expected_revision) is not int or not isinstance(owner_id, UUID)
        or state.status != "running" or state.owner_id != owner_id or state.generation != generation
        or state.revision != expected_revision or state.lease_expires_at is None
        or state.lease_expires_at <= now or definition.deadline_at <= now):
        raise IngestionConflict("执行者、代次、修订或有效租约不匹配")


async def claim_ingestion_job(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    owner_id: UUID, expected_revision: int, policy: IngestionWritePolicy | None = None,
    correlation_id: str,
) -> KnowledgeIngestionJob:
    row, definition, state = await _locked_job(session, identity=identity, job_id=job_id, policy=policy)
    now = utc_now()
    reclaimable = state.status == "running" and state.lease_expires_at is not None and state.lease_expires_at <= now
    if (type(expected_revision) is not int or not isinstance(owner_id, UUID)
        or state.revision != expected_revision or definition.deadline_at <= now
        or state.attempts >= definition.max_attempts or state.status not in {"queued", "retry_wait", "running"}
        or (state.status == "running" and not reclaimable)
        or (state.retry_at is not None and state.retry_at > now)):
        raise IngestionConflict("导入任务不可领取或累计预算已耗尽")
    updated = IngestionJobState.model_validate({**state.model_dump(), "status": "running",
        "revision": state.revision + 1, "generation": state.generation + 1, "attempts": state.attempts + 1,
        "owner_id": owner_id, "lease_expires_at": min(now + timedelta(seconds=60), definition.deadline_at),
        "retry_at": None, "failure_code": None})
    _store_state(row, definition, updated)
    await _audit(session, row, AuditAction.KNOWLEDGE_INGESTION_UPDATED, correlation_id)
    return row


async def renew_ingestion_job(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    owner_id: UUID, generation: int, expected_revision: int,
    policy: IngestionWritePolicy | None = None, correlation_id: str,
) -> KnowledgeIngestionJob:
    row, definition, state = await _locked_job(session, identity=identity, job_id=job_id, policy=policy)
    _owned(definition, state, owner_id=owner_id, generation=generation, expected_revision=expected_revision)
    updated = IngestionJobState.model_validate({**state.model_dump(), "revision": state.revision + 1,
        "lease_expires_at": min(utc_now() + timedelta(seconds=60), definition.deadline_at)})
    _store_state(row, definition, updated)
    await _audit(session, row, AuditAction.KNOWLEDGE_INGESTION_UPDATED, correlation_id)
    return row


async def _ingestion_target_digest(session: AsyncSession, *, row: KnowledgeIngestionJob,
    policy: IngestionWritePolicy,
) -> str:
    """只冻结控制面元数据；调用方已锁来源，文档按UUID顺序锁定，不读取版本正文。"""
    def metadata_value(value: object) -> object:
        if isinstance(value, datetime):
            current = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
            return current.isoformat()
        return str(value) if isinstance(value, UUID) else value

    bindings = (await session.execute(select(KnowledgeIngestionObject.object_key,
        KnowledgeIngestionObject.document_id).where(
        KnowledgeIngestionObject.tenant_id == row.tenant_id, KnowledgeIngestionObject.source_id == row.source_id,
        KnowledgeIngestionObject.identity_policy_digest == policy.scope.identity_policy_digest,
        KnowledgeIngestionObject.object_key.in_(policy.allowed_object_keys))
        .order_by(KnowledgeIngestionObject.document_id))).all()
    targets = []
    for binding in bindings:
        document = await lock_knowledge_document(session, document_id=binding.document_id, tenant_id=row.tenant_id)
        acl_digest = await _acl_digest(session, document_id=document.id, tenant_id=row.tenant_id)
        if (document.source_id != row.source_id or document.owner_user_id != policy.document_owner_id
            or document.status == KnowledgeDocumentStatus.ARCHIVED or acl_digest != policy.target_acl_digest):
            raise IngestionNotApproved("当前目标范围与来源批准映射不一致")
        latest_id = await session.scalar(select(KnowledgeVersion.id).where(
            KnowledgeVersion.document_id == document.id, KnowledgeVersion.tenant_id == row.tenant_id)
            .order_by(KnowledgeVersion.version_number.desc()).limit(1).execution_options(include_deleted=True))
        if latest_id is None:
            raise IngestionConflict("当前目标缺少最新版本，不能继续导入")
        version_ids = {latest_id}
        if document.current_version_id is not None:
            version_ids.add(document.current_version_id)
        versions = (await session.execute(select(KnowledgeVersion.id, KnowledgeVersion.version_number,
            KnowledgeVersion.title, KnowledgeVersion.content_digest, KnowledgeVersion.status,
            KnowledgeVersion.review_revision, KnowledgeVersion.review_decision,
            KnowledgeVersion.effective_from, KnowledgeVersion.effective_until, KnowledgeVersion.deleted_at)
            .where(KnowledgeVersion.id.in_(version_ids), KnowledgeVersion.document_id == document.id,
                KnowledgeVersion.tenant_id == row.tenant_id).order_by(KnowledgeVersion.id)
            .execution_options(include_deleted=True))).all()
        if len(versions) != len(version_ids) or any(version.deleted_at is not None for version in versions):
            raise IngestionConflict("当前目标版本缺失或已删除，不能继续导入")
        # 固定投影不含正文；UUID/时区时间转换后只保存域隔离摘要，不输出原元数据。
        version_states = [{name: metadata_value(value)
            for name, value in version._mapping.items()} for version in versions]
        targets.append([binding.object_key, str(document.id), document.title,
            str(document.owner_user_id), document.status, metadata_value(document.current_version_id),
            metadata_value(document.updated_at), metadata_value(document.review_due_at),
            acl_digest, str(latest_id), version_states])
    return _digest(["knowledge_ingestion_targets_v1", sorted(policy.allowed_object_keys), targets])


async def check_ingestion_execution(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    owner_id: UUID, generation: int, expected_revision: int, policy: IngestionWritePolicy | None = None,
) -> tuple[IngestionJobState, str]:
    """来源IO之前的当前许可检查；不续租、不增加修订，调用方随后释放事务锁。"""
    row, definition, state = await _locked_job(session, identity=identity, job_id=job_id, policy=policy)
    _owned(definition, state, owner_id=owner_id, generation=generation, expected_revision=expected_revision)
    if policy is None or state.cursor != definition.initial_cursor:
        raise IngestionConflict("当前执行不是获准的本地初始单页")
    target_digest = await _ingestion_target_digest(session, row=row, policy=policy)
    # 文档复检可能耗时；离开事务前再次判断固定租约与来源期限，不补发或延长许可。
    _owned(definition, state, owner_id=owner_id, generation=generation, expected_revision=expected_revision)
    if policy.valid_until <= utc_now():
        raise IngestionNotApproved("来源批准已到期")
    return state, target_digest


async def invalidate_ingestion_execution(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    owner_id: UUID, generation: int, expected_revision: int, correlation_id: str,
    failure_code: Literal["source_unapproved", "authorization_changed"] = "source_unapproved",
) -> KnowledgeIngestionJob:
    """来源读取许可失效后仅关闭本人当前执行；不要求用旧批准读取来源，不自动重试。"""
    if failure_code not in {"source_unapproved", "authorization_changed"}:
        raise IngestionConflict("停止执行必须使用来源许可失败码")
    row = await get_ingestion_job(session, identity=identity, job_id=job_id)
    row = await session.scalar(select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == row.id,
        KnowledgeIngestionJob.tenant_id == identity.principal.tenant_id,
        KnowledgeIngestionJob.requested_by == identity.principal.user_id)
        .execution_options(populate_existing=True).with_for_update())
    if row is None:
        raise ResourceNotFoundError("导入任务不存在")
    definition, state = _job_contracts(row)
    _owned(definition, state, owner_id=owner_id, generation=generation, expected_revision=expected_revision)
    updated = IngestionJobState.model_validate({**state.model_dump(), "status": "failed",
        "revision": state.revision + 1, "generation": state.generation + 1,
        "owner_id": None, "lease_expires_at": None, "retry_at": None, "failure_code": failure_code})
    _store_state(row, definition, updated)
    await _audit(session, row, AuditAction.KNOWLEDGE_INGESTION_UPDATED, correlation_id)
    return row


async def cancel_ingestion_job(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    expected_revision: int, correlation_id: str,
) -> KnowledgeIngestionJob:
    # 撤销来源批准后仍允许本人停止任务，不以旧来源策略阻止取消。
    row = await get_ingestion_job(session, identity=identity, job_id=job_id)
    row = await session.scalar(select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == row.id)
        .execution_options(populate_existing=True).with_for_update())
    if row is None:
        raise ResourceNotFoundError("导入任务不存在")
    definition, state = _job_contracts(row)
    if state.status == "cancelled":
        return row
    if type(expected_revision) is not int or state.revision != expected_revision or state.status in {"completed", "failed"}:
        raise IngestionConflict("任务状态或修订不允许取消")
    updated = IngestionJobState.model_validate({**state.model_dump(), "status": "cancelled",
        "revision": state.revision + 1, "generation": state.generation + 1,
        "owner_id": None, "lease_expires_at": None, "retry_at": None, "failure_code": None})
    _store_state(row, definition, updated)
    await _audit(session, row, AuditAction.KNOWLEDGE_INGESTION_UPDATED, correlation_id)
    return row


async def fail_ingestion_job(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    owner_id: UUID, generation: int, expected_revision: int, failure_code: IngestionFailure,
    policy: IngestionWritePolicy | None = None, correlation_id: str,
) -> KnowledgeIngestionJob:
    if failure_code not in get_args(IngestionFailure):
        raise IngestionConflict("来源失败必须使用已定义的固定失败码")
    row, definition, state = await _locked_job(session, identity=identity, job_id=job_id, policy=policy)
    _owned(definition, state, owner_id=owner_id, generation=generation, expected_revision=expected_revision)
    retry_at = utc_now() + timedelta(seconds=30)
    retrying = (failure_code in {"source_unavailable", "rate_limited", "commit_conflict", "recovery_required"}
        and state.attempts < definition.max_attempts and retry_at < definition.deadline_at)
    updated = IngestionJobState.model_validate({**state.model_dump(),
        "status": "retry_wait" if retrying else "failed", "revision": state.revision + 1,
        "owner_id": None, "lease_expires_at": None, "failure_code": failure_code,
        "retry_at": retry_at if retrying else None})
    _store_state(row, definition, updated)
    await _audit(session, row, AuditAction.KNOWLEDGE_INGESTION_UPDATED, correlation_id)
    return row


async def expire_ingestion_job(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    expected_revision: int, correlation_id: str,
) -> KnowledgeIngestionJob:
    """本人显式收敛超时/耗尽任务；不需要已撤销的来源读取许可，不重跑来源。"""
    row = await get_ingestion_job(session, identity=identity, job_id=job_id)
    row = await session.scalar(select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.id == row.id)
        .execution_options(populate_existing=True).with_for_update())
    if row is None:
        raise ResourceNotFoundError("导入任务不存在")
    definition, state = _job_contracts(row)
    now = utc_now()
    exhausted = (state.status == "running" and state.lease_expires_at is not None
        and state.lease_expires_at <= now and state.attempts >= definition.max_attempts)
    if (type(expected_revision) is not int or state.revision != expected_revision
        or state.status in {"completed", "failed", "cancelled"}
        or (definition.deadline_at > now and not exhausted)):
        raise IngestionConflict("任务尚未达到可收敛的超时或耗尽边界")
    updated = IngestionJobState.model_validate({**state.model_dump(), "status": "failed",
        "revision": state.revision + 1, "generation": state.generation + 1,
        "owner_id": None, "lease_expires_at": None, "retry_at": None,
        "failure_code": "budget_exhausted" if exhausted else "recovery_required"})
    _store_state(row, definition, updated)
    await _audit(session, row, AuditAction.KNOWLEDGE_INGESTION_UPDATED, correlation_id)
    return row


async def _materialize_candidate(session: AsyncSession, *, row: KnowledgeIngestionJob,
    scope: IngestionScope, policy: IngestionWritePolicy, candidate: IngestionDraftCandidate,
    correlation_id: str,
) -> IngestionObjectReceipt:
    normalized = normalize_knowledge_markdown(candidate.content_markdown)
    title = candidate.title.strip()
    if (not title or knowledge_content_digest(normalized) != candidate.parsed_content_digest
        or sum(line.startswith("#") for line in normalized.splitlines()) > 1000):
        raise IngestionConflict("候选正文或标题与解析摘要不一致")
    metadata_digest = _digest({"title": title, "owner": str(policy.document_owner_id),
        "target_acl_digest": policy.target_acl_digest, "effective_policy": "first_import_no_expiry"})
    draft_key = ingestion_draft_identity(scope, object_key=candidate.object_key,
        parsed_content_digest=candidate.parsed_content_digest,
        document_metadata_digest=metadata_digest, source_acl_digest=policy.source_acl_digest)
    binding = await session.scalar(select(KnowledgeIngestionObject).where(
        KnowledgeIngestionObject.tenant_id == row.tenant_id, KnowledgeIngestionObject.source_id == row.source_id,
        KnowledgeIngestionObject.identity_policy_digest == scope.identity_policy_digest,
        KnowledgeIngestionObject.object_key == candidate.object_key))
    initial_version = None
    if binding is None:
        external_key = "ingestion:" + candidate.object_key
        collision = await session.scalar(select(KnowledgeDocument.id).where(
            KnowledgeDocument.tenant_id == row.tenant_id, KnowledgeDocument.source_id == row.source_id,
            KnowledgeDocument.external_key == external_key).execution_options(include_deleted=True))
        if collision is not None:
            raise IngestionConflict("来源标识已有文档但缺少可信绑定，需要人工映射复核")
        if policy.target_acl_digest != hashlib.sha256(b"").hexdigest():
            raise IngestionConflict("新文档没有ACL，不能自动应用来源权限或扩权")
        document = await create_knowledge_document(session, tenant_id=row.tenant_id,
            actor_user_id=row.requested_by, source_id=row.source_id, external_key=external_key,
            owner_user_id=policy.document_owner_id, title=title, content_markdown=normalized,
            change_summary="来源导入候选，待独立审核", correlation_id=correlation_id)
        binding = KnowledgeIngestionObject(id=uuid4(), tenant_id=row.tenant_id, source_id=row.source_id,
            identity_policy_digest=scope.identity_policy_digest, object_key=candidate.object_key,
            document_id=document.id)
        session.add(binding)
        await session.flush()
        initial_version = await session.scalar(select(KnowledgeVersion).where(
            KnowledgeVersion.document_id == document.id, KnowledgeVersion.tenant_id == row.tenant_id,
            KnowledgeVersion.version_number == 1))
    document = await lock_knowledge_document(session, document_id=binding.document_id, tenant_id=row.tenant_id)
    if (document.source_id != row.source_id or document.owner_user_id != policy.document_owner_id
        or document.status == KnowledgeDocumentStatus.ARCHIVED
        or await _acl_digest(session, document_id=document.id, tenant_id=row.tenant_id) != policy.target_acl_digest):
        raise IngestionConflict("文档绑定、负责人或当前知识ACL与批准映射不一致")
    latest = await session.scalar(select(KnowledgeVersion).where(
        KnowledgeVersion.document_id == document.id, KnowledgeVersion.tenant_id == row.tenant_id)
        .order_by(KnowledgeVersion.version_number.desc()).limit(1).execution_options(include_deleted=True))
    if latest is None or latest.deleted_at is not None:
        raise IngestionConflict("文档最新版本缺失或已删除，需要人工复核")
    existing = await session.scalar(select(KnowledgeIngestionMaterialization).where(
        KnowledgeIngestionMaterialization.tenant_id == row.tenant_id,
        KnowledgeIngestionMaterialization.source_id == row.source_id,
        KnowledgeIngestionMaterialization.draft_key == draft_key))
    outcome: Literal["unchanged", "draft_created"]
    if existing is not None:
        if (existing.object_id != binding.id or existing.version_id != latest.id
            or latest.status in {KnowledgeVersionStatus.SUPERSEDED, KnowledgeVersionStatus.RETIRED}
            or latest.title != title or latest.content_digest != candidate.parsed_content_digest
            or knowledge_content_digest(latest.content_markdown) != candidate.parsed_content_digest):
            raise IngestionConflict("历史物化不再是当前候选，不能自动复用或复活旧版本")
        version, outcome = latest, "unchanged"
    else:
        version = initial_version or await create_knowledge_version(session, document_id=document.id,
            tenant_id=row.tenant_id, actor_user_id=row.requested_by, title=title, content_markdown=normalized,
            expected_revision=latest.version_number, allow_duplicate=True,
            change_summary="来源增量候选，待独立审核", correlation_id=correlation_id)
        if version is None:
            raise IngestionConflict("候选版本未完成物化")
        session.add(KnowledgeIngestionMaterialization(tenant_id=row.tenant_id, source_id=row.source_id,
            object_id=binding.id, job_id=row.id, draft_key=draft_key, version_id=version.id))
        outcome = "draft_created"
    return IngestionObjectReceipt(object_key=candidate.object_key,
        raw_content_digest=candidate.raw_content_digest, parsed_content_digest=candidate.parsed_content_digest,
        source_revision_digest=candidate.source_revision_digest, document_metadata_digest=metadata_digest,
        source_acl_digest=policy.source_acl_digest, change_kind="upsert", outcome=outcome,
        document_id=document.id, knowledge_version_id=version.id)


@asynccontextmanager
async def _page_transaction(session: AsyncSession) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(INGESTION_TRANSACTION_TIMEOUT_SECONDS):
            async with session.begin_nested():
                yield
    except TimeoutError:
        raise IngestionConflict("页面事务超过固定时限，请查询原任务和页回执，勿重放执行") from None
    except SQLAlchemyError:
        # SQL异常可能含INSERT正文参数；对外固定码，原事务/保存点仍正常回滚。
        raise IngestionConflict("页面数据库写入结果无法确认，请查询原任务和页回执，勿重放执行") from None


async def _check_replay_targets(session: AsyncSession, *, receipt: IngestionPageReceipt,
    policy: IngestionWritePolicy,
) -> None:
    if {item.object_key for item in receipt.objects} != set(policy.allowed_object_keys):
        raise IngestionConflict("页面回执对象与固定来源范围不一致")
    for item in sorted(receipt.objects, key=lambda item: str(item.document_id)):
        if item.document_id is None or item.knowledge_version_id is None:
            raise IngestionConflict("当前适配器不能复用缺少目标的页面回执")
        document = await lock_knowledge_document(session, document_id=item.document_id,
            tenant_id=receipt.scope.tenant_id)
        version = await session.scalar(select(KnowledgeVersion.id).where(
            KnowledgeVersion.id == item.knowledge_version_id,
            KnowledgeVersion.document_id == document.id, KnowledgeVersion.tenant_id == receipt.scope.tenant_id))
        if (document.source_id != receipt.scope.source_id or document.owner_user_id != policy.document_owner_id
            or document.status == KnowledgeDocumentStatus.ARCHIVED or version is None
            or await _acl_digest(session, document_id=document.id,
                tenant_id=receipt.scope.tenant_id) != policy.target_acl_digest):
            raise IngestionNotApproved("原页面目标的当前权限或可用性已变化")


async def commit_ingestion_page(session: AsyncSession, *, identity: IdentityContext, job_id: UUID,
    owner_id: UUID, generation: int, expected_revision: int,
    candidates: tuple[IngestionDraftCandidate, ...], expected_target_digest: str,
    policy: IngestionWritePolicy | None = None,
    correlation_id: str,
) -> IngestionPageReceipt:
    """本地固定清单整页提交；调用方提交外层事务，任何异常回滚整页保存点。"""
    row, definition, state = await _locked_job(session, identity=identity, job_id=job_id, policy=policy)
    if policy is None or type(candidates) is not tuple or not 0 < len(candidates) <= definition.max_objects_per_page:
        raise IngestionConflict("候选数量与固定页面预算不一致")
    if (len({item.object_key for item in candidates}) != len(candidates)
        or {item.object_key for item in candidates} != set(policy.allowed_object_keys)):
        raise IngestionNotApproved("候选对象清单与当前批准范围不一致")
    try:
        sizes = [len(item.content_markdown.encode("utf-8")) for item in candidates]
        if any(size > 1_048_576 for size in sizes) or sum(sizes) > 4_194_304:
            raise IngestionConflict("候选正文超过整页4MiB限制")
        request_digest = _digest(["knowledge_ingestion_candidates_v1", ingestion_scope_digest(definition.scope),
            [item.model_dump(mode="json") for item in sorted(candidates, key=lambda item: item.object_key)]])
    except (UnicodeError, TypeError):
        raise IngestionConflict("候选正文不能序列化为受限UTF-8") from None
    # 当前适配器只支持有限清单的一页；不存原始游标，也不伪造秘密存储引用。
    page_identity = IngestionPageReceipt(job_id=row.id, scope=definition.scope,
        expected_revision=expected_revision, generation=generation,
        input_cursor=definition.initial_cursor, output_cursor=IngestionCursor(kind="complete", page_sequence=1),
        objects=())
    page_key = ingestion_page_key(page_identity)
    previous = await session.scalar(select(KnowledgeIngestionPage).where(
        KnowledgeIngestionPage.tenant_id == row.tenant_id, KnowledgeIngestionPage.job_id == row.id,
        KnowledgeIngestionPage.page_key == page_key))
    if previous is not None:
        try:
            receipt = IngestionPageReceipt.model_validate_json(json.dumps(previous.receipt))
            validate_ingestion_page(definition, receipt)
            if (state.status != "completed" or previous.request_digest != request_digest
                or previous.result_digest != ingestion_page_result_digest(receipt)
                or ingestion_page_key(receipt) != page_key):
                raise ValueError("receipt mismatch")
        except (ValueError, TypeError):
            raise IngestionConflict("同页改参或原页面回执完整性校验失败") from None
        await _check_replay_targets(session, receipt=receipt, policy=policy)
        if policy.valid_until <= utc_now():
            raise IngestionNotApproved("来源批准已到期")
        await _current_admin(session, identity)
        return receipt
    _owned(definition, state, owner_id=owner_id, generation=generation, expected_revision=expected_revision)
    if state.cursor != definition.initial_cursor:
        raise IngestionConflict("当前读取器不支持非初始检查点")
    target_digest = await _ingestion_target_digest(session, row=row, policy=policy)
    if not isinstance(expected_target_digest, str) or expected_target_digest != target_digest:
        raise IngestionTargetChanged("来源读取期间目标版本或治理范围已变化，本页未生成草稿")
    async with _page_transaction(session):
        # 目标摘要复检已按UUID顺序持有全部旧文档锁；新文档尚不可被其他事务读取。
        receipts = []
        for candidate in sorted(candidates, key=lambda item: item.object_key):
            receipts.append(await _materialize_candidate(session, row=row, scope=definition.scope,
                policy=policy, candidate=candidate, correlation_id=correlation_id))
        # 不能因候选物化耗时而延长许可；提交前再次检查当前身份和时间边界。
        await _current_admin(session, identity)
        if policy.valid_until <= utc_now():
            raise IngestionNotApproved("来源批准已到期")
        _owned(definition, state, owner_id=owner_id, generation=generation, expected_revision=expected_revision)
        page = IngestionPageReceipt(job_id=row.id, scope=definition.scope,
            expected_revision=expected_revision, generation=generation, input_cursor=state.cursor,
            output_cursor=IngestionCursor(kind="complete", page_sequence=1), objects=tuple(receipts))
        validate_ingestion_page(definition, page)
        session.add(KnowledgeIngestionPage(tenant_id=row.tenant_id, job_id=row.id,
            page_key=page_key, request_digest=request_digest, result_digest=ingestion_page_result_digest(page),
            receipt=page.model_dump(mode="json")))
        updated = IngestionJobState.model_validate({**state.model_dump(), "status": "completed",
            "revision": state.revision + 1, "cursor": page.output_cursor,
            "owner_id": None, "lease_expires_at": None, "failure_code": None,
            "draft_created_count": sum(item.outcome == "draft_created" for item in receipts),
            "unchanged_count": sum(item.outcome == "unchanged" for item in receipts)})
        _store_state(row, definition, updated)
        await _audit(session, row, AuditAction.KNOWLEDGE_INGESTION_PAGE_COMMITTED, correlation_id)
    return page
