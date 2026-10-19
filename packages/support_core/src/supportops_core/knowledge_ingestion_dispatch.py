"""本人显式单次导入派发；数据库领取后复用执行库，不扫描来源或自动重试。"""

import asyncio
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.auth import IdentityContext, Principal, ensure_identity
from supportops_core.agent_services import PermissionDeniedError
from supportops_core.config import Settings
from supportops_core.enums import AuditAction, AuditResourceType
from supportops_core.knowledge_ingestion_approval import FileLocalMarkdownApprovalProvider
from supportops_core.knowledge_ingestion_contracts import (
    IngestionContract, IngestionRevisionCommand, ingestion_scope_digest,
)
from supportops_core.knowledge_ingestion_runtime import execute_local_markdown_ingestion
from supportops_core.knowledge_ingestion_services import (
    IngestionConflict, IngestionNotApproved, _current_admin,
    _digest, _job_contracts, _require_ingestion_fencing_database, _utc,
    check_ingestion_source, get_ingestion_job,
)
from supportops_core.knowledge_markdown_source import (
    LOCAL_MARKDOWN_ISOLATED_ADAPTER_MANIFEST_DIGEST, LOCAL_MARKDOWN_ISOLATED_PARSER_MANIFEST_DIGEST,
)
from supportops_core.knowledge_services import append_knowledge_audit
from supportops_core.models import KnowledgeIngestionExecutionRequest, User, utc_now
from supportops_core.services import ResourceNotFoundError

ExecutionStatus = Literal["queued", "claimed", "completed", "rejected", "unconfirmed"]
ExecutionError = Literal["REQUEST_EXPIRED", "AUTHORIZATION_OR_SCOPE_CHANGED", "JOB_CHANGED", "EXECUTION_UNCONFIRMED"]


class IngestionExecutionRequestView(IngestionContract):
    schema_version: Literal["knowledge_ingestion_execution_request_v1"] = "knowledge_ingestion_execution_request_v1"
    id: UUID
    job_id: UUID
    expected_job_revision: int = Field(ge=1)
    status: ExecutionStatus
    revision: int = Field(ge=1)
    created_at: datetime
    expires_at: datetime
    claimed_at: datetime | None
    finished_at: datetime | None
    error_code: ExecutionError | None
    automatic_retry_enabled: Literal[False] = False
    automatic_publish_enabled: Literal[False] = False


def request_digest(row: KnowledgeIngestionExecutionRequest) -> str:
    return _digest(["knowledge_ingestion_execution_request_v1", str(row.id), str(row.tenant_id),
        str(row.job_id), str(row.requested_by), str(row.auth_session_id), row.auth_generation,
        row.expected_job_revision, row.scope_digest, _utc(row.created_at).isoformat(), _utc(row.expires_at).isoformat()])


def request_view(row: KnowledgeIngestionExecutionRequest) -> IngestionExecutionRequestView:
    try:
        claimed = row.status in {"claimed", "completed", "unconfirmed"}
        terminal = row.status in {"completed", "rejected", "unconfirmed"}
        if (request_digest(row) != row.record_digest
            or not timedelta(0) < _utc(row.expires_at) - _utc(row.created_at) <= timedelta(seconds=60)
            or claimed != (row.claim_id is not None and row.claimed_at is not None)
            or (not claimed and (row.claim_id is not None or row.claimed_at is not None))
            or terminal != (row.finished_at is not None)
            or (row.status in {"rejected", "unconfirmed"}) != (row.error_code is not None)
            or row.revision != {"queued": 1, "claimed": 2, "rejected": 2, "completed": 3, "unconfirmed": 3}.get(row.status)):
            raise ValueError()
        return IngestionExecutionRequestView(id=row.id, job_id=row.job_id,
            expected_job_revision=row.expected_job_revision, status=row.status, revision=row.revision,
            created_at=_utc(row.created_at), expires_at=_utc(row.expires_at), claimed_at=_utc(row.claimed_at),
            finished_at=_utc(row.finished_at), error_code=row.error_code)
    except (ValueError, TypeError, AttributeError):
        raise IngestionConflict("单次执行意图完整性无法确认，请保留原任务核对") from None


async def _audit(session: AsyncSession, row: KnowledgeIngestionExecutionRequest) -> None:
    await append_knowledge_audit(session, tenant_id=row.tenant_id, actor_user_id=row.requested_by,
        resource_type=AuditResourceType.KNOWLEDGE_SOURCE, action=AuditAction.KNOWLEDGE_INGESTION_UPDATED,
        correlation_id=f"ingestion-dispatch:{row.id}", metadata={"operation": "manual_worker_execution",
            "ingestion_job_id": str(row.job_id), "execution_request_id": str(row.id),
            "request_status": row.status, "request_revision": row.revision})


async def read_execution_request(session: AsyncSession, *, identity: IdentityContext,
    job_id: UUID,
) -> IngestionExecutionRequestView | None:
    job = await get_ingestion_job(session, identity=identity, job_id=job_id)
    row = await session.scalar(select(KnowledgeIngestionExecutionRequest).where(
        KnowledgeIngestionExecutionRequest.job_id == job.id,
        KnowledgeIngestionExecutionRequest.tenant_id == job.tenant_id,
        KnowledgeIngestionExecutionRequest.requested_by == job.requested_by))
    return request_view(row) if row is not None else None


async def submit_execution_request(factory: async_sessionmaker[AsyncSession], *, settings: Settings,
    identity: IdentityContext, job_id: UUID, command: IngestionRevisionCommand,
) -> IngestionExecutionRequestView:
    provider = FileLocalMarkdownApprovalProvider(settings)
    # 未知POST响应只查询这个Job的唯一意图；同修订重放只读返回，不再次取得来源许可。
    async with asyncio.timeout(10), factory() as session, session.begin():
        job = await get_ingestion_job(session, identity=identity, job_id=job_id)
        existing = await read_execution_request(session, identity=identity, job_id=job_id)
        if existing is not None:
            if existing.expected_job_revision != command.expected_revision:
                raise IngestionConflict("原任务已有不可替换的执行意图，请查询原记录")
            return existing
        if not settings.knowledge_ingestion_worker_enabled:
            raise IngestionNotApproved("未启用人工Worker导入入口")
        await _require_ingestion_fencing_database(session)
        definition, state = _job_contracts(job)
        plan = await provider(scope=definition.scope, requested_by=job.requested_by)
        if (plan is None or definition.scope.adapter_manifest_digest != LOCAL_MARKDOWN_ISOLATED_ADAPTER_MANIFEST_DIGEST
            or definition.scope.parser_manifest_digest != LOCAL_MARKDOWN_ISOLATED_PARSER_MANIFEST_DIGEST):
            raise IngestionNotApproved("Worker导入只接受当前获准的独立读取与独立解析组合")
        await check_ingestion_source(session, identity=identity, policy=plan.write_policy)
        now = utc_now()
        if (state.revision != command.expected_revision or state.status != "queued" or state.attempts != 0
            or definition.deadline_at <= now):
            raise IngestionConflict("仅原修订、从未领取的排队任务可提交一次Worker执行意图")
        principal = identity.principal
        if principal.session_id is None or principal.auth_generation is None or principal.token_expires_at is None:
            raise IngestionNotApproved("执行意图需要当前可撤销登录会话")
        expires = min(now + timedelta(seconds=60), definition.deadline_at,
            plan.write_policy.valid_until, principal.token_expires_at)
        if expires <= now + timedelta(seconds=10):
            raise IngestionNotApproved("原来源、任务或登录窗口不足，请重新核对")
        row = KnowledgeIngestionExecutionRequest(id=uuid4(), tenant_id=job.tenant_id, job_id=job.id,
            requested_by=job.requested_by, auth_session_id=principal.session_id,
            auth_generation=principal.auth_generation, expected_job_revision=state.revision,
            scope_digest=ingestion_scope_digest(definition.scope), status="queued", revision=1,
            created_at=now, expires_at=expires)
        row.record_digest = request_digest(row)
        session.add(row)
        await session.flush()
        await _audit(session, row)
        if await provider(scope=definition.scope, requested_by=job.requested_by) != plan:
            raise IngestionNotApproved("来源批准已变化，未提交执行意图")
        await _current_admin(session, identity)
        if row.expires_at <= utc_now():
            raise IngestionNotApproved("单次执行意图已到期")
        result = request_view(row)
    return result


def _principal(row: KnowledgeIngestionExecutionRequest) -> Principal:
    # 只能从已提交的原会话记录重建定位；请求截止同时限制后台身份的有效时间。
    return Principal(user_id=row.requested_by, tenant_id=row.tenant_id, roles=(), display_name="",
        session_id=row.auth_session_id, auth_generation=row.auth_generation, token_expires_at=_utc(row.expires_at))


async def _finish(session: AsyncSession, row: KnowledgeIngestionExecutionRequest, *,
    status: Literal["completed", "rejected", "unconfirmed"], error: ExecutionError | None,
) -> None:
    row.status, row.error_code, row.finished_at = status, error, utc_now()
    row.revision += 1
    await session.flush()
    await _audit(session, row)


async def process_one_execution_request(factory: async_sessionmaker[AsyncSession], *, settings: Settings,
    stop: asyncio.Event,
) -> bool:
    """最多处理一个人工意图；领取提交不明、崩溃或过期均不重领，不自动创建Job。"""
    if not settings.knowledge_ingestion_worker_enabled or stop.is_set():
        return False
    async with asyncio.timeout(5), factory() as session, session.begin():
        await _require_ingestion_fencing_database(session)
        candidates = (await session.execute(select(KnowledgeIngestionExecutionRequest.id,
            KnowledgeIngestionExecutionRequest.tenant_id, KnowledgeIngestionExecutionRequest.requested_by)
            .where(KnowledgeIngestionExecutionRequest.status.in_(("queued", "claimed")))
            .order_by(KnowledgeIngestionExecutionRequest.created_at, KnowledgeIngestionExecutionRequest.id)
            .limit(20))).all()
    claimed = None
    provider = FileLocalMarkdownApprovalProvider(settings)
    for request_id, tenant_id, requested_by in candidates:
        async with asyncio.timeout(5), factory() as session, session.begin():
            await _require_ingestion_fencing_database(session)
            user = await session.scalar(select(User).where(User.id == requested_by,
                User.tenant_id == tenant_id).with_for_update(skip_locked=True))
            if user is None:
                continue
            row = await session.scalar(select(KnowledgeIngestionExecutionRequest).where(
                KnowledgeIngestionExecutionRequest.id == request_id,
                KnowledgeIngestionExecutionRequest.tenant_id == tenant_id,
                KnowledgeIngestionExecutionRequest.requested_by == requested_by).with_for_update(skip_locked=True))
            if row is None or row.status not in {"queued", "claimed"}:
                continue
            request_view(row)
            if _utc(row.expires_at) <= utc_now():
                await _finish(session, row, status="unconfirmed" if row.status == "claimed" else "rejected",
                    error="EXECUTION_UNCONFIRMED" if row.status == "claimed" else "REQUEST_EXPIRED")
                return True
            if row.status == "claimed" or stop.is_set():
                continue
            try:
                identity = await ensure_identity(session, _principal(row), require_session=True)
                job = await get_ingestion_job(session, identity=identity, job_id=row.job_id)
                definition, state = _job_contracts(job)
                plan = await provider(scope=definition.scope, requested_by=requested_by)
                if (plan is None or ingestion_scope_digest(definition.scope) != row.scope_digest
                    or definition.scope.adapter_manifest_digest != LOCAL_MARKDOWN_ISOLATED_ADAPTER_MANIFEST_DIGEST
                    or definition.scope.parser_manifest_digest != LOCAL_MARKDOWN_ISOLATED_PARSER_MANIFEST_DIGEST):
                    raise IngestionNotApproved("来源范围已变化")
                await check_ingestion_source(session, identity=identity, policy=plan.write_policy)
            except (ValueError, PermissionDeniedError, ResourceNotFoundError):
                await _finish(session, row, status="rejected", error="AUTHORIZATION_OR_SCOPE_CHANGED")
                return True
            if state.status != "queued" or state.revision != row.expected_job_revision or state.attempts != 0:
                await _finish(session, row, status="rejected", error="JOB_CHANGED")
                return True
            if stop.is_set():
                break
            row.status, row.claim_id, row.claimed_at = "claimed", uuid4(), utc_now()
            row.revision += 1
            await session.flush()
            await _audit(session, row)
            claimed = (row.id, row.tenant_id, row.job_id, row.claim_id, row.expected_job_revision, _principal(row))
        if claimed is not None:
            break
    if claimed is None or stop.is_set():
        return False
    # 事务成功退出后才允许调用执行库；来源IO期间不持派发表或身份锁。
    request_id, tenant_id, job_id, claim_id, expected_revision, principal = claimed
    completed = False
    try:
        remaining = (principal.token_expires_at - utc_now()).total_seconds()
        if remaining > 0:
            async with asyncio.timeout(remaining):
                async with factory() as session:
                    identity = await ensure_identity(session, principal, require_session=True)
                result = await execute_local_markdown_ingestion(factory, identity=identity,
                    job_id=job_id, expected_revision=expected_revision, approval_provider=provider)
                completed = result.status == "completed"
    except Exception:
        # 任何已消费意图的执行异常都不重放；原Job/页回执保持唯一业务事实源。
        completed = False
    async with asyncio.timeout(5), factory() as session, session.begin():
        row = await session.scalar(select(KnowledgeIngestionExecutionRequest).where(
            KnowledgeIngestionExecutionRequest.id == request_id,
            KnowledgeIngestionExecutionRequest.tenant_id == tenant_id).with_for_update())
        if row is not None and row.status == "claimed" and row.claim_id == claim_id:
            if _utc(row.expires_at) <= utc_now():
                completed = False
            await _finish(session, row, status="completed" if completed else "unconfirmed",
                error=None if completed else "EXECUTION_UNCONFIRMED")
    return True
